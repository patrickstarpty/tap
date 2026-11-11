"""Standalone replay primitives; never invoke a model or access the live session."""

# JavaScript literal shared in behavior with deploy/browser/page-actions.mjs.
# ruff: noqa: E501
REPLAY_HELPERS = r"""
// Identity comes from actual Frame and Document objects, never URLs or events.
function createDocumentProvenance(page) {
  const records = new WeakMap();
  let sequence = 0;
  function frames() {
    const current = page.frames();
    return current.length <= 100 ? current : [];
  }
  async function capture(frame) {
    if (!frame || !frames().includes(frame) || frame.isDetached()) return null;
    const url = frame.url();
    if (typeof url !== 'string' || !url || url.length > 2048) return null;
    let record = records.get(frame);
    if (!record) {
      record = {frame_id: String(++sequence), document_id: null, handle: null};
      records.set(frame, record);
    }
    let same = false;
    if (record.handle) {
      try { same = await record.handle.evaluate(doc => doc === document); } catch (error) {
        // A transient/unknown read failure is not evidence of document replacement.
        if (!/Execution context was destroyed|Cannot find context with specified id/.test(String(error?.message))) return null;
      }
    }
    if (!same) {
      let handle;
      try {
        handle = await frame.evaluateHandle(() => document);
        if (!await handle.evaluate(doc => doc === document)) throw new Error('document unavailable');
      } catch {
        await handle?.dispose().catch(() => {});
        return null;
      }
      await record.handle?.dispose().catch(() => {});
      record.handle = handle;
      record.document_id = String(++sequence);
    }
    if (frame.isDetached() || !frames().includes(frame) || frame.url() !== url) return null;
    return {frame_id:record.frame_id, document_id:record.document_id, url};
  }
  async function snapshot() {
    try {
      const result = [];
      for (const frame of frames()) {
        const proof = await capture(frame).catch(() => null);
        if (proof) result.push(proof);
      }
      return result;
    } catch { return []; }
  }
  async function read(action, readValue) {
    const element = await page.locator(action.selector).elementHandle({timeout:30000});
    if (!element) throw new Error('read document unavailable');
    try {
      const frame = await element.ownerFrame();
      const before = await capture(frame);
      if (!before) throw new Error('read document unavailable');
      const value = await readValue(page, action);
      const after = await capture(frame);
      const current = await page.locator(action.selector).elementHandle({timeout:30000});
      try {
        if (!after || before.document_id !== after.document_id || before.url !== after.url ||
            !current || await current.ownerFrame() !== frame ||
            !await element.evaluate(node => node.isConnected && node.ownerDocument === document)) {
          throw new Error('read document changed');
        }
      } finally { await current?.dispose(); }
      return {value, source_frame_id:before.frame_id, source_document_id:before.document_id, source_url:before.url};
    } finally { await element.dispose(); }
  }
  return {snapshot, read};
}

function recordAcceptance(contract, proofs, action, before, after, read) {
  if (!contract) return;
  const previous = new Map(before.map(doc=>[doc.frame_id,doc.document_id]));
  const current = new Map(after.map(doc=>[doc.frame_id,doc.document_id]));
  const write = ['navigate','click','fill','select','press','scroll'].includes(action.type);
  for (const [name, proof] of [...proofs]) {
    const source = proof.source_document_id;
    const frame = proof.source_frame_id;
    if (write) {
      proofs.delete(name);
      if (proof.state !== 'active' || previous.get(frame) !== source || !current.has(frame)) continue;
      if (['navigate','click'].includes(action.type) && current.get(frame) !== source) {
        proofs.set(name,{...proof,state:'archived',destination:current.get(frame)});
      } else if (action.type === 'click' && current.get(frame) === source) {
        proofs.set(name,{...proof,state:'pending'});
      }
    } else if (!current.has(frame)) {
      proofs.delete(name);
    } else if (proof.state === 'pending') {
      if (current.get(frame) !== source) proofs.set(name,{...proof,state:'archived',destination:current.get(frame)});
    } else if (current.get(frame) !== (proof.state === 'archived' ? proof.destination : source)) {
      proofs.delete(name);
    }
  }
  if (action.type !== 'read') return;
  const field = contract.fields.find(field=>field.id===action.name);
  if (!field) return;
  proofs.delete(field.id);
  if (action.equals!==field.readback_value || read?.value!==field.readback_value ||
      (action.attribute||'text')!==field.attribute ||
      !Array.isArray(action.target_texts) || !action.target_texts.length ||
      action.target_texts.some(text=>!field.target_texts.includes(text)) ||
      (action.scope_text||null)!==(field.scope_text||null) ||
      !read.source_frame_id || !read.source_document_id ||
      current.get(read.source_frame_id)!==read.source_document_id ||
      !after.some(doc=>doc.frame_id===read.source_frame_id && doc.url===read.source_url)) {
    throw new Error('reviewed input readback mismatch: '+field.id);
  }
  proofs.set(field.id,{...read,state:'active'});
}
function assertAcceptance(contract, proofs, documents) {
  const current = new Map(documents.map(doc=>[doc.frame_id,doc.document_id]));
  if (contract && contract.fields.some(field=>{
    const proof = proofs.get(field.id);
    return !proof || proof.state==='pending' || current.get(proof.source_frame_id)!==(proof.state==='archived' ? proof.destination : proof.source_document_id);
  })) {
    throw new Error('reviewed input proof missing or stale');
  }
}
// This fixed DOM reader admits only an explicit, bounded visible enum binding.
// It never returns arbitrary hidden values or derives state from label presence.
function associatedControlState(nodes, {target = null} = {}) {
  const visible = node => Boolean(node?.getClientRects().length && getComputedStyle(node).visibility !== 'hidden' && !node.closest('[hidden],[inert],[aria-hidden="true"]'));
  const normalize = text => String(text || '').normalize('NFKC').replace(/\s+/g, ' ').trim();
  const sensitive = text => /password|passcode|secret|token|csrf|xsrf|session|auth|credential|otp|one.?time|verification|security|cvv|cvc|card|payment|bank|account|routing|iban|swift|private|api.?key|access.?key|ssn|social.?security|passport|licen[cs]e|pin(?:$|[^a-z]|code)/i.test(text);
  const unique = selector => document.querySelectorAll(selector).length === 1;
  const selectorFor = node => {
    if (node.id && unique(`#${CSS.escape(node.id)}`)) return `#${CSS.escape(node.id)}`;
    for (const attribute of ['data-testid', 'name', 'aria-label']) {
      const value = node.getAttribute(attribute);
      if (!value) continue;
      const candidate = `${node.tagName.toLowerCase()}[${attribute}=${JSON.stringify(value)}]`;
      if (unique(candidate)) return candidate;
    }
    const parts = [];
    for (let element = node; element && element !== document.body; element = element.parentElement) {
      const siblings = [...(element.parentElement?.children || [element])].filter(item => item.tagName === element.tagName);
      parts.unshift(`${element.tagName.toLowerCase()}:nth-of-type(${siblings.indexOf(element) + 1})`);
      if (unique(parts.join(' > '))) return parts.join(' > ');
    }
    return `body > ${parts.join(' > ')}`;
  };
  const state = node => {
    const hidden = node?.matches('input[type="hidden"]');
    const group = hidden ? node.parentElement : node?.closest('[data-name]');
    if (!group || ['BODY','HTML','FORM'].includes(group.tagName) || !visible(group)
      || (!hidden && !visible(node)) || group.querySelectorAll('*').length > 120
      || group.querySelector('[data-name]') || group.parentElement?.closest('[data-name]') || group.closest('[data-secret],[data-private],[data-sensitive]')) return null;
    const controls = [...group.querySelectorAll('input,select,textarea')];
    if (controls.length !== 1) return null;
    const input = controls[0];
    if (input.type !== 'hidden' || input.parentElement !== group || (hidden && input !== node)) return null;
    const name = input.getAttribute('name') || '';
    if (!/^[A-Za-z][A-Za-z0-9_-]{0,79}$/.test(name) || group.getAttribute('data-name') !== name
      || sensitive([name,input.id,input.getAttribute('autocomplete'),input.getAttribute('aria-label')].join(' '))
      || input.matches('[data-secret],[data-private],[data-sensitive]')) return null;
    const attributes = ['data-value','data-option-value','data-optionvalue'];
    const choices = [...group.querySelectorAll(attributes.map(attribute=>`[${attribute}]`).join(','))];
    if (choices.length < 2 || choices.length > 12 || choices.some(choice=>!visible(choice))) return null;
    let valueAttribute;
    const options = [];
    for (const choice of choices) {
      const present = attributes.filter(attribute=>choice.hasAttribute(attribute));
      if (present.length !== 1 || (valueAttribute && valueAttribute !== present[0])) return null;
      valueAttribute = present[0];
      const value = choice.getAttribute(valueAttribute);
      const label = normalize(choice.innerText);
      // Short enum codes only, never opaque payloads, identifiers or credentials.
      if (!/^[A-Za-z0-9][A-Za-z0-9 _.-]{0,31}$/.test(value) || /\d{9,}|[A-Fa-f0-9]{16,}/.test(value)
        || !label || label.length > 100 || sensitive(label)
        || choices.some(other=>other !== choice && choice.contains(other))) return null;
      options.push({label,value});
    }
    if (new Set(options.map(option=>option.value)).size !== options.length
      || new Set(options.map(option=>normalize(option.label).toLowerCase())).size !== options.length) return null;
    const labelled = (group.getAttribute('aria-labelledby') || '').split(/\s+/).filter(Boolean).map(id=>document.getElementById(id));
    const headings = [...group.querySelectorAll('legend,label,p,h1,h2,h3,h4,h5,h6,[role="heading"]')]
      .filter(heading=>visible(heading) && !choices.some(choice=>choice.contains(heading)) && normalize(heading.innerText));
    const label = normalize(group.getAttribute('aria-label')
      || (labelled.length && labelled.every(visible) ? labelled.map(node=>node.innerText).join(' ') : '')
      || (headings.length === 1 ? headings[0].innerText : ''));
    if (!label || label.length > 120 || sensitive(label) || normalize(group.innerText).length > 600) return null;
    // Read the hidden payload only after proving its ordinary visible enum context.
    const value = String(input.value);
    const selected = options.filter(option=>option.value === value);
    if (value !== '' && selected.length !== 1) return null;
    return {tag:'input',type:'hidden',id:input.id,name,selector:selectorFor(input),
      context:'associated_selection_state',label,value,selected_label:selected[0]?.label ?? null,
      options,readonly:true,disabled:Boolean(input.disabled),
      association:{kind:'named_widget_enum',group_selector:selectorFor(group),group_label:label,value_attribute:valueAttribute},
    };
  };
  if (!Array.isArray(nodes)) return state(nodes);
  return nodes.slice(0,200).map(state).filter(Boolean)
    .sort((a,b)=>Number(Boolean(target && document.querySelector(b.association.group_selector)?.contains(target)))
      - Number(Boolean(target && document.querySelector(a.association.group_selector)?.contains(target))))
    .slice(0,12);
}

async function readSelectedLabel(page, action) {
  if (typeof action.equals !== 'string') throw new Error('associated selection requires exact equals');
  const state = await page.locator(action.selector).evaluate(associatedControlState);
  if (!state || state.selected_label === null) throw new Error('no safe associated selected state');
  return state.selected_label;
}

function positiveNumber(value) {
  const numbers=String(value).replace(/,/g,'').match(/-?\d+(?:\.\d+)?/g) || [];
  return numbers.length===1 && Number(numbers[0])>0;
}
async function readValue(page, action) {
  const attribute = action.attribute || 'text';
  const locator = page.locator(action.selector);
  if (action.pattern !== undefined && (typeof action.pattern !== 'string' || action.pattern.length > 200 || /[+*}]\)[+*{]/.test(action.pattern))) throw new Error('invalid bounded read pattern');
  const pattern = action.pattern === undefined ? null : new RegExp(action.pattern, 'u');
  const timeout = Math.min(30000, Math.max(100, Number(action.timeout_ms) || 30000));
  const deadline = Date.now() + timeout;
  let value;
  do {
    // Reject secret controls even when the model asks for their attributes.
    const type = await locator.getAttribute('type', {timeout});
    const autocomplete = await locator.getAttribute('autocomplete', {timeout});
    if (String(type).toLowerCase() === 'password' || /password|one-time-code|cc-number|cc-csc/i.test(autocomplete || '')) throw new Error('sensitive control cannot be extracted');
    if (attribute === 'selected_label') value = await readSelectedLabel(page, action);
    else if (String(type).toLowerCase() === 'hidden') {
      if (attribute !== 'value' || typeof action.equals !== 'string') throw new Error('hidden selection requires exact associated value read');
      const state = await locator.evaluate(associatedControlState);
      if (!state || state.selected_label === null) throw new Error('hidden control has no safe associated selected state');
      value = state.value;
    }
    else if (attribute === 'text') value = await locator.innerText({timeout});
    else if (attribute === 'value') value = await locator.inputValue({timeout});
    else if (attribute === 'checked') value = await locator.evaluate(node => node.getAttribute('aria-checked') ?? String(node.checked));
    else value = await locator.getAttribute(attribute, {timeout});
    if (typeof value !== 'string' || value.length > 16000) throw new Error('DOM value unavailable or too large');
    const equal = action.equals === undefined || value === action.equals;
    const contains = action.contains === undefined || value.includes(action.contains);
    const nonempty = !action.nonempty || value.trim().length > 0;
    const numbers = value.replace(/,/g, '').match(/-?\d+(?:\.\d+)?/g) || [];
    const positive = !action.positive || (numbers.length === 1 && Number(numbers[0]) > 0);
    if (equal && contains && nonempty && positive && (!pattern || pattern.test(value))) return value;
    if (Date.now() >= deadline) throw new Error('Expected DOM value was not observed; actual: ' + value.slice(0,500));
    await new Promise(resolve => setTimeout(resolve,100));
  } while (true);
}

async function guardTarget(page, action) {
  if (!action.target_texts) return; // Older frozen bundles retain their original contract.
  if (!Array.isArray(action.target_texts) || !action.target_texts.length) throw new Error('missing target contract');
  const facts = await page.locator(action.selector).evaluate(node => ({
    labels: [node.id, node.getAttribute('name'), node.getAttribute('aria-label'),
      ...(node.labels ? [...node.labels].map(n=>n.textContent) : []),
      node.innerText, node.textContent, ...(node.getAttribute('aria-labelledby') || '').split(/\s+/).map(id=>document.getElementById(id)?.textContent)].filter(Boolean),
    scopes: [node.closest('label'), node.parentElement, node.parentElement?.parentElement]
      .filter(n=>n && !['BODY','HTML'].includes(n.tagName) && (n.textContent || '').length <= 600)
      .map(n=>(n.innerText || n.textContent).trim())
      .filter(text=>text !== (node.innerText || node.textContent || '').trim()).slice(0,1),
    type: node.getAttribute('type'), autocomplete: node.getAttribute('autocomplete') || '',
  }), {timeout:30000});
  if (String(facts.type).toLowerCase() === 'hidden') {
    const state = await page.locator(action.selector).evaluate(associatedControlState);
    if (!state) throw new Error('hidden target has no safe associated selection state');
    if (action.type && !['read','snapshot'].includes(action.type)) throw new Error('hidden selection state is read-only');
    facts.labels = [state.id,state.name,state.label].filter(Boolean);
    facts.scopes = [state.association.group_label];
  }
  const matches = (text, expected) => {
    const value = String(text).normalize('NFKC').toLowerCase().replace(/\s+/g,' ').trim();
    const term = String(expected).normalize('NFKC').toLowerCase().replace(/\s+/g,' ').trim();
    const escaped = term.replace(/[.*+?^${}()|[\]\\]/g,'\\$&');
    return Boolean(term) && new RegExp('(^|[^\\p{L}\\p{N}])'+escaped+'($|[^\\p{L}\\p{N}])','u').test(value);
  };
  if (String(facts.type).toLowerCase() === 'password' || /password|one-time-code|cc-number|cc-csc/i.test(facts.autocomplete)) throw new Error('sensitive target');
  if (!action.target_texts.some(term=>facts.labels.some(text=>matches(text,term)))) throw new Error('target does not match frozen BDD contract');
  if (action.scope_text && !facts.scopes.some(text=>matches(text,action.scope_text))) throw new Error('scope does not match frozen BDD contract');
}

async function selectValue(page, action) {
  const locator=page.locator(action.selector);
  const facts=await locator.evaluate(n=>({tag:n.tagName,type:n.type,role:n.getAttribute('role'),text:(n.textContent || '').trim(),
    labels:[n.value,n.getAttribute('aria-label'),n.textContent,...[...(n.labels || [])].map(l=>l.textContent)].filter(Boolean).map(s=>s.trim().toLowerCase()),
    options:n.tagName==='SELECT'?[...n.options].map(o=>({label:o.label,value:o.value})):[]}));
  if (facts.tag==='SELECT') {
    const choices=facts.options.filter(o=>o.value===action.value || o.label===action.value);
    if (choices.length!==1) throw new Error('requested option is missing or ambiguous');
    await locator.selectOption(choices[0].value,{timeout:30000}); return;
  }
  if ((['radio','checkbox'].includes(facts.type) || ['radio','checkbox'].includes(facts.role))
      && !facts.labels.includes(action.value.toLowerCase())) throw new Error('requested option does not match actual element');
  if (['radio','checkbox'].includes(facts.type)) { await locator.setChecked(true,{timeout:30000}); return; }
  if (['radio','checkbox'].includes(facts.role)) {
    if (await locator.getAttribute('aria-checked')!=='true') await locator.click({timeout:30000});
    return;
  }
  if (facts.text===action.value) {await locator.click({timeout:30000}); return;}
  if (await locator.getAttribute('aria-expanded') !== 'true') await locator.click({timeout:30000});
  // Observe the opened control's actual frame and associations. Searching the
  // top-level page can click an unrelated option, especially inside iframes.
  const handle=await locator.elementHandle({timeout:30000});
  if (!handle) throw new Error('selection target is no longer available');
  let frame;
  let popupIds;
  try {
    frame=await handle.ownerFrame();
    popupIds=await handle.evaluate(node=>[...new Set(
      ['aria-controls','aria-owns'].flatMap(name=>(node.getAttribute(name)||'').split(/\s+/).filter(Boolean))
    )]);
  } finally { await handle.dispose(); }
  if (!frame) throw new Error('selection target frame is no longer available');
  const scope=popupIds.length
    ? frame.locator(popupIds.map(id=>`[id=${JSON.stringify(id)}]`).join(','))
    : frame;
  let option=scope.getByRole('option',{name:action.value,exact:true}).filter({visible:true});
  if (await option.count()===0) option=scope.getByText(action.value,{exact:true}).filter({visible:true});
  await option.click({timeout:30000}); // Playwright strictness rejects ambiguous options.
}
"""

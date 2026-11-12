// This fixed DOM reader admits only an explicit, bounded visible enum binding.
// It never returns arbitrary hidden values or derives state from label presence.
export function associatedControlState(nodes, {target = null} = {}) {
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

export async function readSelectedLabel(page, action) {
  if (typeof action.equals !== 'string') throw new Error('associated selection requires exact equals');
  const state = await page.locator(action.selector).evaluate(associatedControlState);
  if (!state || state.selected_label === null) throw new Error('no safe associated selected state');
  return state.selected_label;
}

// Browser facts are extracted by Playwright, never authored by the model.
const fieldSelector = 'output,[role="status"],input,select,textarea,button,a[href],[role="combobox"],[role="radio"],[role="checkbox"],[role="option"],[role="slider"],[contenteditable="true"]';

// Runs only our fixed read-only DOM collector; never changes the AI ref map.
function collectFields(nodes, {target = null, selectorOnly = false, scopedSelect = false, discoveryOnly = false} = {}) {
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
      const candidate = parts.join(' > ');
      if (unique(candidate)) return candidate;
    }
    return `body > ${parts.join(' > ')}`;
  };
  if (selectorOnly) return selectorFor(nodes);
  // Independent low-information catalog identity. Never reuse IDs, names,
  // aria-label, editable values, option text or the legacy label collector.
  const discoveryIdentity = (node, frameBoundary = false) => {
    const tags = new Set('html body div span section article main header footer nav aside form fieldset legend label input select textarea button a output ul ol li table thead tbody tfoot tr th td p h1 h2 h3 h4 h5 h6 dialog details summary figure figcaption strong em small b i u br iframe'.split(' '));
    const roles = new Set('button link textbox combobox radio checkbox option slider status dialog alertdialog listbox menu menuitem grid gridcell tab tabpanel heading spinbutton switch searchbox tree treeitem list listitem group radiogroup'.split(' '));
    const protectedNode = 'input[type="hidden" i],input[type="password" i],[autocomplete*="password" i],[autocomplete*="one-time-code" i],[autocomplete*="cc-" i],[autocomplete~="username" i],[autocomplete~="webauthn" i],[data-secret],[data-sensitive],[data-private],[hidden],[inert],[aria-hidden="true" i],object,embed';
    const visible = element => {
      if (!element?.getClientRects().length) return false;
      for (let ancestor = element; ancestor; ancestor = ancestor.parentElement) {
        const style = getComputedStyle(ancestor);
        if (style.visibility !== 'visible' || style.display === 'none' || Number(style.opacity) === 0
          || style.contentVisibility === 'hidden') return false;
      }
      return true;
    };
    const safe = element => element && visible(element) && !element.closest(protectedNode)
      && !element.querySelector(protectedNode + ',iframe');
    if (!node || node.ownerDocument !== document || node.getRootNode() !== document || !safe(node)) return null;
    if (node.tagName === 'IFRAME' && !frameBoundary) return null;
    const parts = [];
    for (let element = node; element; element = element.parentElement) {
      const tag = element.tagName.toLowerCase();
      if (!tags.has(tag) || parts.length >= 64) return null;
      const siblings = [...(element.parentElement?.children || [element])].filter(item => item.tagName === element.tagName);
      parts.unshift(`${tag}:nth-of-type(${siblings.indexOf(element) + 1})`);
    }
    const selector = parts.join(' > ');
    if (new TextEncoder().encode(selector).length > 1000 || document.querySelectorAll(selector).length !== 1
      || document.querySelector(selector) !== node) return null;
    const tag = node.tagName.toLowerCase();
    const role = node.getAttribute('role');
    const result = {selector,tag,...(roles.has(role)?{role}:{})};
    if (frameBoundary) return result;
    const references = (node.getAttribute('aria-labelledby') || '').split(/\s+/).filter(Boolean);
    const sources = references.length ? references.map(id=>document.getElementById(id)) : [...(node.labels || [])];
    if (sources.some(source=>!safe(source))) return null;
    if (!sources.length && (tag === 'button' || tag === 'a' || role === 'button' || role === 'link')) sources.push(node);
    const editable = 'input,textarea,select,option,[contenteditable]:not([contenteditable="false" i])';
    const staticText = source => {
      if (source.matches(editable) || source.isContentEditable) return '';
      const walker = document.createTreeWalker(source, NodeFilter.SHOW_TEXT);
      const pieces = [];let count = 0;
      for (let text = walker.nextNode(); text; text = walker.nextNode()) {
        if (++count > 64) return '';
        const parent = text.parentElement;
        if (!parent || !visible(parent) || parent.closest(editable) || parent.isContentEditable) continue;
        pieces.push(text.textContent);
      }
      return pieces.join(' ').replace(/\s+/g,' ').trim();
    };
    const label = sources.map(staticText).join(' ').replace(/\s+/g,' ').trim();
    if (label && label.length <= 120) result.label = label;
    return result;
  };
  if (discoveryOnly) return discoveryIdentity(nodes, true);
  const visible = node => node?.getClientRects().length && getComputedStyle(node).visibility !== 'hidden';
  const related = new Map();
  const add = (node, relation) => { if (visible(node) && !related.has(node)) related.set(node, relation); };
  const popups = new Set();
  const popupSelector = '[role="dialog"],[role="alertdialog"],dialog,[aria-modal="true"],[role="listbox"],[role="menu"],[role="grid"],[popover]';
  const popup = (node, relation) => {
    if (!visible(node)) return;
    add(node, relation);
    popups.add(node);
  };
  const associate = (node, relation) => {
    if (!visible(node)) return;
    add(node, relation);
    for (const attribute of ['aria-controls', 'aria-owns']) {
      for (const id of (node.getAttribute(attribute) || '').split(/\s+/).filter(Boolean).slice(0, 5)) popup(document.getElementById(id), 'associated_popup');
    }
    const active = document.getElementById(node.getAttribute('aria-activedescendant'));
    add(active, 'active_option');
    popup(node.closest(popupSelector), 'containing_popup');
  };
  associate(target, 'action_target');
  // Observe the element actually receiving pointer events at the target's
  // center. This catches unannotated overlays without guessing site classes.
  if (target && visible(target)) {
    const rect = target.getBoundingClientRect();
    const x = rect.left + rect.width / 2;
    const y = rect.top + rect.height / 2;
    if (x >= 0 && x < innerWidth && y >= 0 && y < innerHeight) {
      const hit = document.elementFromPoint(x, y);
      if (hit && !target.contains(hit) && !hit.contains(target)) {
        let blocker = hit.closest(popupSelector);
        if (!blocker) {
          blocker = hit;
          while (blocker.parentElement && blocker.parentElement !== document.body
            && !blocker.querySelector('button,input,select,textarea,a[href]')) blocker = blocker.parentElement;
        }
        popup(blocker, 'pointer_blocker');
      }
    }
  }
  if (nodes.includes(document.activeElement)) associate(document.activeElement, 'focused');
  for (const node of [...document.querySelectorAll('[aria-expanded="true"]')].filter(visible).slice(0, 10)) associate(node, 'expanded');
  for (const node of [...document.querySelectorAll(popupSelector)].filter(visible).slice(0, 3)) popup(node, 'visible_popup');
  for (const root of popups) {
    for (const node of [...root.querySelectorAll('button,input,select,textarea,[role="option"],[role="gridcell"],[role="menuitem"],a[href],td,li')].filter(visible).slice(0, 30)) add(node, 'popup_item');
  }
  // Some widgets have no popup ARIA. Report nearby native controls as visible
  // facts only, without inventing an association or guessing a widget class.
  const inViewport = node => {
    const rect = node.getBoundingClientRect();
    return visible(node) && rect.bottom > 0 && rect.right > 0 && rect.top < innerHeight && rect.left < innerWidth;
  };
  for (const node of [...document.querySelectorAll('select,input,textarea')].filter(inViewport).slice(0, 8)) add(node, 'visible_control');
  // Scope candidates are read-only observation boundaries, never a widget or
  // business binding. Keep only small, safe owner-document ancestors and do
  // not collect their aggregate text or hidden values.
  const scopesFor = node => {
    const hiddenPath = '[hidden],[inert],[aria-hidden="true"]';
    const protectedNode = 'input[type="hidden"],input[type="password"],[autocomplete*="password" i],[autocomplete*="one-time-code" i],[autocomplete*="cc-" i],[data-secret],[data-sensitive],[data-private],iframe,object,embed';
    const interactive = 'button,input,select,textarea,a[href],[role="button"],[role="option"],[role="gridcell"],[role="menuitem"],[role="checkbox"],[role="radio"],[role="combobox"],[contenteditable="true"],[tabindex]';
    let fallback = null;
    for (let ancestor = node.parentElement, depth = 0; ancestor && depth < 4; ancestor = ancestor.parentElement, depth++) {
      if (['HTML','BODY','MAIN','FORM'].includes(ancestor.tagName)) break;
      if (!inViewport(ancestor) || ancestor.closest(hiddenPath) || ancestor.closest(protectedNode)) continue;
      const walker = document.createTreeWalker(ancestor,NodeFilter.SHOW_ELEMENT);
      const grids = []; let count = 0, controls = Number(ancestor.matches(interactive)), safe = true;
      for (let child = walker.nextNode(); child; child = walker.nextNode()) {
        if (++count > 256 || child.matches(protectedNode) || (child.matches(interactive) && ++controls > 64)) { safe = false; break; }
        if (child.matches('table,[role="grid"]') && inViewport(child) && !child.closest(hiddenPath)) grids.push(child);
      }
      if (!safe) continue;
      const identity = (element,relation) => ({selector:selectorFor(element),tag:element.tagName.toLowerCase(),role:element.getAttribute('role'),relation});
      const scopes = [identity(ancestor,'ancestor_scope')];
      if (grids.length === 1) scopes.push(identity(grids[0],'descendant_table_scope'));
      fallback ||= scopes;
      if (grids.length === 1 || ancestor.matches('table,[role="grid"]')) return scopes;
    }
    return fallback || [];
  };

  const order = context => ['action_target', 'pointer_blocker', 'associated_popup', 'containing_popup', 'visible_popup', 'focused', 'active_option', 'popup_item', 'visible_control', 'expanded'].indexOf(context);
  const ordered = [...related.keys()].sort((a, b) => order(related.get(a)) - order(related.get(b)));
  ordered.push(...nodes.filter(node => !related.has(node) && visible(node))
    .sort((a, b) => Number(a.tagName === 'A') - Number(b.tagName === 'A')));
  return ordered.slice(0, 100).map(node => {
    const popupText = popups.has(node) ? (node.innerText || '').trim() : null;
    const discovery = discoveryIdentity(node);
    return {
    ...(discovery ? {discovery_identity:discovery} : {}),
    tag: node.tagName.toLowerCase(), name: node.getAttribute('name'), id: node.id,
    type: node.getAttribute('type'), role: node.getAttribute('role'), selector: selectorFor(node),
    ...(related.has(node) ? {context: related.get(node)} : {}),
    ...((node === target || (node.tagName === 'SELECT' && related.has(node) && inViewport(node))) ? {scope_candidates:scopesFor(node)} : {}),
    ...(popupText !== null ? {popup_text: popupText.slice(0, 1000), popup_text_truncated: popupText.length > 1000} : {}),
    label: (node.getAttribute('aria-label') || (node.getAttribute('aria-labelledby') || '').split(/\s+/).map(id => document.getElementById(id)?.textContent || '').join(' ').trim() || [...(node.labels || [])].map(label => label.textContent).join(' ') || node.textContent || '').trim().slice(0, 200),
    value: node.type === 'password' ? '[hidden]' : String(node.value ?? '').slice(0, 200),
    checked: node.checked ?? node.getAttribute('aria-checked'), disabled: Boolean(node.disabled),
    readonly: Boolean(node.readOnly || node.getAttribute('aria-readonly') === 'true'),
    expanded: node.getAttribute('aria-expanded'), href: node.getAttribute('href'),
    state_attributes: Object.fromEntries(['aria-checked','aria-selected','aria-pressed','aria-valuenow','data-active','data-selected']
      .filter(name=>node.hasAttribute(name) && (name === 'aria-valuenow'
        ? /^-?\d{1,12}(?:\.\d{1,6})?$/.test(node.getAttribute(name))
        : /^(?:|true|false|mixed|1|0)$/.test(node.getAttribute(name))))
      .map(name=>[name,node.getAttribute(name)])),
    ...(node.tagName === 'SELECT' ? {
      options:[...node.options].slice(0,scopedSelect && node === target ? 200 : 30).map(o=>({label:o.label,value:o.value})),
      options_total:node.options.length,
      options_truncated:node.options.length > (scopedSelect && node === target ? 200 : 30),
    } : {options:[]}),
    };
  });
}

export async function observeFields(page, action = {}) {
  let target;
  try {
    try {
      if (action.selector && await page.locator(action.selector).count() === 1) target = await page.locator(action.selector).elementHandle({timeout:200});
    } catch { /* An invalid or stale target must not suppress the current page facts. */ }
    const targetFrame = await target?.ownerFrame();
    const frames = [...new Set([targetFrame, page.mainFrame()].filter(Boolean))];
    const results = [];
    for (const frame of frames) {
      try {
        // A frame-local CSS selector must include every verified frame boundary.
        const prefixes = [];
        const discoveryPrefixes = [];
        let discoveryFramesSafe = true;
        for (let current = frame; current.parentFrame(); current = current.parentFrame()) {
          const element = await current.frameElement();
          try {
            prefixes.unshift(await element.evaluate(collectFields, {selectorOnly:true}));
            const identity = await element.evaluate(collectFields, {discoveryOnly:true});
            if (!identity) discoveryFramesSafe = false;
            else discoveryPrefixes.unshift(identity.selector);
          }
          finally { await element.dispose(); }
        }
        const prefix = prefixes.map(selector => `${selector} >> internal:control=enter-frame >> `).join('');
        const discoveryPrefix = discoveryPrefixes.map(selector => `${selector} >> internal:control=enter-frame >> `).join('');
        const fields = await frame.locator(fieldSelector).evaluateAll(collectFields, {target:frame === targetFrame ? target : null, scopedSelect:action.type === 'snapshot' && Boolean(action.selector)});
        const states = await frame.locator('input[type="hidden"]').evaluateAll(associatedControlState, {target:frame === targetFrame ? target : null});
        const ordered = [...fields.filter(field=>field.context === 'action_target'), ...states, ...fields.filter(field=>field.context !== 'action_target')];
        results.push(...ordered.map(field => {
          const {discovery_identity, ...legacy} = field;
          const selector = discovery_identity ? discoveryPrefix + discovery_identity.selector : '';
          const discovery = discoveryFramesSafe && discovery_identity && new TextEncoder().encode(selector).length <= 1000
            ? {...discovery_identity,selector} : null;
          return {...legacy, selector:prefix + field.selector,
            ...(discovery ? {discovery_identity:discovery} : {}),
            ...(field.association ? {association:{...field.association,group_selector:prefix + field.association.group_selector}} : {}),
            ...(field.scope_candidates ? {scope_candidates:field.scope_candidates.map(scope=>({...scope,selector:prefix + scope.selector})).filter(scope=>scope.selector.length <= 1000)} : {}),
          };
        }));
      } catch { /* A detached frame has no current observable controls. */ }
    }
    return [...results.filter(field => field.context), ...results.filter(field => !field.context)].slice(0, 100);
  } catch { return []; }
  finally { await target?.dispose().catch(() => {}); }
}

export async function readValue(page, action) {
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

// The caller supplies these constraints from the frozen BDD contract, not the live decision.
export async function guardTarget(page, action) {
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

export async function selectValue(page, action) {
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

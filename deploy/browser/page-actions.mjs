// Browser facts are extracted by Playwright, never authored by the model.
const fieldSelector = 'output,[role="status"],input,select,textarea,button,a[href],[role="combobox"],[role="radio"],[role="checkbox"],[role="option"],[role="slider"],[contenteditable="true"]';

// Runs only our fixed read-only DOM collector; never changes the AI ref map.
function collectFields(nodes, {target = null, selectorOnly = false} = {}) {
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
  const order = context => ['action_target', 'pointer_blocker', 'associated_popup', 'containing_popup', 'visible_popup', 'focused', 'active_option', 'popup_item', 'visible_control', 'expanded'].indexOf(context);
  const ordered = [...related.keys()].sort((a, b) => order(related.get(a)) - order(related.get(b)));
  ordered.push(...nodes.filter(node => !related.has(node) && visible(node))
    .sort((a, b) => Number(a.tagName === 'A') - Number(b.tagName === 'A')));
  return ordered.slice(0, 100).map(node => {
    const popupText = popups.has(node) ? (node.innerText || '').trim() : null;
    return {
    tag: node.tagName.toLowerCase(), name: node.getAttribute('name'), id: node.id,
    type: node.getAttribute('type'), role: node.getAttribute('role'), selector: selectorFor(node),
    ...(related.has(node) ? {context: related.get(node)} : {}),
    ...(popupText !== null ? {popup_text: popupText.slice(0, 1000), popup_text_truncated: popupText.length > 1000} : {}),
    label: (node.getAttribute('aria-label') || (node.getAttribute('aria-labelledby') || '').split(/\s+/).map(id => document.getElementById(id)?.textContent || '').join(' ').trim() || [...(node.labels || [])].map(label => label.textContent).join(' ') || node.textContent || '').trim().slice(0, 200),
    value: node.type === 'password' ? '[hidden]' : String(node.value ?? '').slice(0, 200),
    checked: node.checked ?? node.getAttribute('aria-checked'), disabled: Boolean(node.disabled),
    readonly: Boolean(node.readOnly || node.getAttribute('aria-readonly') === 'true'),
    expanded: node.getAttribute('aria-expanded'), href: node.getAttribute('href'),
    options: node.tagName === 'SELECT' ? [...node.options].slice(0, 30).map(o => ({label:o.label, value:o.value})) : [],
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
        for (let current = frame; current.parentFrame(); current = current.parentFrame()) {
          const element = await current.frameElement();
          try { prefixes.unshift(await element.evaluate(collectFields, {selectorOnly:true})); }
          finally { await element.dispose(); }
        }
        const prefix = prefixes.map(selector => `${selector} >> internal:control=enter-frame >> `).join('');
        const fields = await frame.locator(fieldSelector).evaluateAll(collectFields, {target:frame === targetFrame ? target : null});
        results.push(...fields.map(field => ({...field, selector:prefix + field.selector})));
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
    if (type === 'password' || /password|one-time-code|cc-number|cc-csc/.test(autocomplete || '')) throw new Error('sensitive control cannot be extracted');
    if (attribute === 'text') value = await locator.innerText({timeout});
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
      node.textContent, ...(node.getAttribute('aria-labelledby') || '').split(/\s+/).map(id=>document.getElementById(id)?.textContent)].filter(Boolean),
    scopes: [node.closest('label'), node.parentElement, node.parentElement?.parentElement]
      .filter(n=>n && !['BODY','HTML'].includes(n.tagName) && (n.textContent || '').length <= 600)
      .map(n=>(n.innerText || n.textContent).trim())
      .filter(text=>text !== (node.innerText || node.textContent || '').trim()).slice(0,1),
    type: node.getAttribute('type'), autocomplete: node.getAttribute('autocomplete') || '',
  }), {timeout:30000});
  const matches = (text, expected) => {
    const value = String(text).normalize('NFKC').toLowerCase().replace(/\s+/g,' ').trim();
    const term = String(expected).normalize('NFKC').toLowerCase().replace(/\s+/g,' ').trim();
    const escaped = term.replace(/[.*+?^${}()|[\]\\]/g,'\\$&');
    return Boolean(term) && new RegExp('(^|[^\\p{L}\\p{N}])'+escaped+'($|[^\\p{L}\\p{N}])','u').test(value);
  };
  if (facts.type === 'password' || /password|one-time-code|cc-number|cc-csc/.test(facts.autocomplete)) throw new Error('sensitive target');
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
  let option=page.getByRole('option',{name:action.value,exact:true}).filter({visible:true});
  if (await option.count()===0) option=page.getByText(action.value,{exact:true}).filter({visible:true});
  await option.click({timeout:30000}); // Playwright strictness rejects ambiguous options.
}

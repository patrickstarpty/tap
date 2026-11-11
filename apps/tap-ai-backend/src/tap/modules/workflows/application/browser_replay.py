"""Standalone replay primitives; never invoke a model or access the live session."""

# JavaScript literal shared in behavior with deploy/browser/page-actions.mjs.
# ruff: noqa: E501
REPLAY_HELPERS = r"""
function recordAcceptance(contract, proofs, action, beforeUrl, afterUrl, value) {
  if (!contract) return;
  if (['navigate','click','fill','select','press'].includes(action.type)) {
    const navigation=['navigate','click'].includes(action.type) && beforeUrl!==afterUrl;
    for (const [name, proof] of proofs) {
      if (proof.url===afterUrl || (!navigation && proof.url===beforeUrl)) proofs.delete(name);
    }
  } else if (action.type==='read') {
    const field=contract.fields.find(f=>f.id===action.name);
    if (!field) return;
    proofs.delete(field.id);
    if (action.equals!==field.readback_value || value!==field.readback_value ||
        (action.attribute||'text')!==field.attribute ||
        !Array.isArray(action.target_texts) || !action.target_texts.length ||
        action.target_texts.some(t=>!field.target_texts.includes(t)) ||
        (action.scope_text||null)!==(field.scope_text||null) || beforeUrl!==afterUrl) {
      throw new Error('reviewed input readback mismatch: '+field.id);
    }
    proofs.set(field.id,{url:afterUrl,value});
  }
}
function positiveNumber(value) {
  const numbers=String(value).replace(/,/g,'').match(/-?\d+(?:\.\d+)?/g) || [];
  return numbers.length===1 && Number(numbers[0])>0;
}
async function guardTarget(page, action) {
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

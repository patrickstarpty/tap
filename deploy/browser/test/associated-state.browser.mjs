// Synthetic, task-owned widgets only: no public-site or model calls.
import assert from 'node:assert/strict';
import test from 'node:test';
import {chromium} from '@playwright/test';
import {observeFields, readValue, guardTarget} from '../page-actions.mjs';

const widget = ({name='deliveryChoice', value='', attribute='data-optionvalue', extra=''}={}) => `
  <section id="delivery" data-name="${name}"><input type="hidden" id="choice" name="${name}" value="${value}">
    <div><p>Delivery method</p><div>
      <div id="standard" ${attribute}="S" onclick="document.getElementById('choice').value='S'"><span>Standard</span></div>
      <div id="express" ${attribute}="E" onclick="document.getElementById('choice').value='E'"><span>Express</span></div>
    </div></div>${extra}</section>`;
async function fixture(t, html=widget()) {
  const browser=await chromium.launch({headless:true});t.after(()=>browser.close());
  const page=await browser.newPage();await page.setContent(html);return page;
}
const states=fields=>fields.filter(f=>f.context==='associated_selection_state');

test('visible bound widget is discoverable before selection without claiming its label is selected',async t=>{
  const page=await fixture(t);
  const all=states(await observeFields(page));
  assert.equal(all.length,1);
  assert.equal(all[0].selector,'#choice');
  assert.equal(all[0].label,'Delivery method');
  assert.equal(all[0].selected_label,null);
  assert.equal(all[0].value,'');
  assert.deepEqual(all[0].options.map(({label,value})=>({label,value})),[{label:'Standard',value:'S'},{label:'Express',value:'E'}]);
  assert.equal(all[0].association.group_selector,'#delivery');
  await assert.rejects(readValue(page,{selector:'#choice',attribute:'selected_label',equals:'Express',timeout_ms:100}),/selection|selected|state/);
});

for(const attribute of ['data-optionvalue','data-option-value','data-value']) test(`current enum mapping verifies a changed selection with ${attribute}`,async t=>{
  const page=await fixture(t,widget({attribute}));
  await page.locator('#express').click();
  const field=states(await observeFields(page,{selector:'#express'}))[0];
  assert.equal(field.value,'E');assert.equal(field.selected_label,'Express');
  assert.equal(field.association.value_attribute,attribute);
  await guardTarget(page,{type:'read',selector:field.selector,target_texts:['Delivery method'],scope_text:'Delivery method',attribute:'selected_label',equals:'Express'});
  assert.equal(await readValue(page,{selector:field.selector,attribute:'selected_label',equals:'Express'}),'Express');
  await page.locator('#standard').click();
  assert.equal(await readValue(page,{selector:field.selector,attribute:'selected_label',equals:'Standard'}),'Standard');
  assert.equal(await readValue(page,{selector:field.selector,attribute:'value',equals:'S'}),'S');
  await assert.rejects(readValue(page,{selector:field.selector,attribute:'selected_label',equals:'Express',timeout_ms:100}),/Expected/);
  await assert.rejects(guardTarget(page,{type:'read',selector:field.selector,attribute:'value',equals:'S',target_texts:['Contact choice']}),/target/);
  await assert.rejects(guardTarget(page,{type:'read',selector:field.selector,attribute:'value',equals:'S',target_texts:['Delivery method'],scope_text:'Contact'}),/scope/);
});

test('contact radios and unrelated hidden inputs are never associated with a visible choice',async t=>{
  const page=await fixture(t,widget({value:'E'})+`<form hidden><label>Express<input type="radio" name="deliveryChoice" checked value="E"></label>
    <input type="hidden" id="unrelated" name="preferences" value="private-payload"></form>`);
  const fields=await observeFields(page,{selector:'#express'});
  assert.deepEqual(states(fields).map(f=>f.id),['choice']);
  assert.ok(!JSON.stringify(fields).includes('private-payload'));
  await assert.rejects(readValue(page,{selector:'#unrelated',attribute:'value',equals:'private-payload'}),/hidden|sensitive|associated/);
});

for(const name of ['csrf','sessionId','authToken','password','otp','verificationCode','cardNumber','payment','privatePayload','apiKey','routingNumber','pinCode']) test(`hidden ${name} is excluded even with an apparent enum binding`,async t=>{
  const page=await fixture(t,widget({name,value:'E'}));
  assert.equal(states(await observeFields(page,{selector:'#express'})).length,0);
  await assert.rejects(readValue(page,{selector:'#choice',attribute:'value',equals:'E'}),/hidden|sensitive|associated/);
  await assert.rejects(readValue(page,{selector:'#choice',attribute:'selected_label',equals:'Express'}),/hidden|sensitive|associated/);
  await assert.rejects(guardTarget(page,{type:'read',selector:'#choice',attribute:'value',equals:'E',target_texts:[name]}),/hidden|sensitive|associated/);
});

for(const mutation of ['duplicate-value','duplicate-label','two-controls','unbound-name','unknown-current','long-code','hidden-options','two-attributes','secret-marker','sensitive-label','unrelated-native','duplicate-widget']) test(`ambiguous or unsafe state fails closed: ${mutation}`,async t=>{
  const page=await fixture(t,widget({value:'E'}));
  await page.evaluate(mutation=>{
    const group=document.querySelector('#delivery'),field=document.querySelector('#choice'),express=document.querySelector('#express'),standard=document.querySelector('#standard');
    if(mutation==='duplicate-value')standard.setAttribute('data-optionvalue','E');
    if(mutation==='duplicate-label')standard.textContent='Express';
    if(mutation==='two-controls')group.append(field.cloneNode());
    if(mutation==='unbound-name')group.setAttribute('data-name','other');
    if(mutation==='unknown-current')field.value='NON_PUBLIC';
    if(mutation==='long-code'){field.value='012345678901234567890123456789';express.setAttribute('data-optionvalue',field.value);}
    if(mutation==='hidden-options')express.hidden=true;
    if(mutation==='two-attributes')express.setAttribute('data-value','S');
    if(mutation==='secret-marker')group.setAttribute('data-secret','');
    if(mutation==='sensitive-label')group.querySelector('p').textContent='Payment card';
    if(mutation==='unrelated-native')group.insertAdjacentHTML('beforeend','<label>Other<input type="radio" value="E"></label>');
    if(mutation==='duplicate-widget'){const copy=group.cloneNode(true);group.append(copy);}
  },mutation);
  const fields=await observeFields(page,{selector:'#express'});
  assert.equal(states(fields).length,0);
  assert.ok(!JSON.stringify(fields).includes('NON_PUBLIC'));
  await assert.rejects(readValue(page,{selector:'#choice',attribute:'selected_label',equals:'Express',timeout_ms:100}));
});

test('a hidden enum read must be exact and current; association cannot authorize other attributes',async t=>{
  const page=await fixture(t,widget({value:'E'}));
  for(const action of [
    {attribute:'value'}, {attribute:'selected_label'}, {attribute:'data-private',equals:'E'}, {attribute:'text',equals:''},
  ]) await assert.rejects(readValue(page,{selector:'#choice',...action}),/exact|hidden|associated/);
  await page.locator('#choice').evaluate(n=>n.setAttribute('data-private','not-for-model'));
  await assert.rejects(readValue(page,{selector:'#choice',attribute:'data-private',equals:'not-for-model'}),/hidden|associated/);
});

test('associated state keeps every iframe selector boundary and reads the selected frame',async t=>{
  const page=await fixture(t,'<iframe id="quote"></iframe>'+widget({value:'S'}));
  const frame=page.frames().find(f=>f!==page.mainFrame());await frame.setContent(widget({value:'E'}));
  const fields=states(await observeFields(page,{selector:'#quote >> internal:control=enter-frame >> #express'}));
  const field=fields.find(f=>f.selector.startsWith('#quote >>'));
  assert.ok(field);assert.equal(field.association.group_selector,'#quote >> internal:control=enter-frame >> #delivery');
  assert.equal(await readValue(page,{selector:field.selector,attribute:'selected_label',equals:'Express'}),'Express');
});

test('visible state allowlist exposes empty active attributes distinctly from absent ones',async t=>{
  const page=await fixture(t,'<button id="monthly" data-active data-private="never-expose">Monthly</button><button id="yearly">Yearly</button>');
  const fields=await observeFields(page,{selector:'#monthly'});
  assert.deepEqual(fields.find(f=>f.id==='monthly').state_attributes,{'data-active':''});
  assert.deepEqual(fields.find(f=>f.id==='yearly').state_attributes,{});
  assert.ok(!JSON.stringify(fields).includes('never-expose'));
});

for (const attribute of ['type="PASSWORD"', 'autocomplete="ONE-TIME-CODE"', 'autocomplete="CC-NUMBER"']) test(`secret control matching is case-insensitive: ${attribute}`,async t=>{
  const page=await fixture(t,`<input id="secret" ${attribute} value="not-for-model">`);
  await assert.rejects(readValue(page,{selector:'#secret',attribute:'value',equals:'not-for-model'}),/sensitive/);
  await assert.rejects(guardTarget(page,{type:'read',selector:'#secret',attribute:'value',equals:'not-for-model',target_texts:['secret']}),/sensitive/);
});

test('frozen target matches its visible nested block label without matching unrelated ancestors',async t=>{
  const page=await fixture(t,'<section><h2>Other product</h2><div id="amount"><div>Sum insured</div><div>HK$</div><div>2,000,000</div></div></section>');
  await guardTarget(page,{type:'read',selector:'#amount',attribute:'text',target_texts:['Sum insured']});
  await assert.rejects(guardTarget(page,{type:'read',selector:'#amount',attribute:'text',target_texts:['Other product']}),/target/);
});

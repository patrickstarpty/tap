import assert from 'node:assert/strict';
import test from 'node:test';
import { createRequire } from 'node:module';
import { observeFields, readValue } from '../page-actions.mjs';
const require = createRequire(new URL('../../../apps/tap-ai-frontend/package.json', import.meta.url));
const { chromium } = require('@playwright/test');

test('readonly selection is observed and the actual selected input is read', async () => {
  const browser = await chromium.launch({headless:true});
  try {
    const page = await browser.newPage();
    await page.setContent(`<label>Age<input id="age" role="combobox" readonly value="25"></label>
      <span role="slider" aria-valuenow="2000000"></span><button aria-pressed="true" onclick="document.querySelector('#age').value='30'">30</button>
      <input id="secret" type="password" value="not-for-model">`);
    const before = await observeFields(page);
    assert.equal(before.find(f=>f.id==='age').readonly, true);
    assert.ok(before.some(f=>f.role==='slider'));
    await page.getByRole('button',{name:'30',exact:true}).click();
    assert.equal(await readValue(page,{selector:'#age',attribute:'value'}), '30');
    assert.equal(await readValue(page,{selector:'button',attribute:'aria-pressed'}), 'true');
    await assert.rejects(readValue(page,{selector:'#secret',attribute:'value'}), /sensitive/);
  } finally { await browser.close(); }
});

test('read waits for actual DOM expectation instead of returning an empty quote', async () => {
  const browser = await chromium.launch({headless:true});
  try {
    const page = await browser.newPage();
    await page.setContent(`<output id="quote"></output><script>setTimeout(()=>document.querySelector('#quote').textContent='HK$10.62/month',100)</script>`);
    assert.equal(await readValue(page,{selector:'#quote',attribute:'text',contains:'/month',pattern:'[1-9][0-9]*\\.[0-9]{2}/month'}), 'HK$10.62/month');
    await assert.rejects(readValue(page,{selector:'#quote',attribute:'text',pattern:'NOT_A_QUOTE',timeout_ms:100}), /Expected/);
    await assert.rejects(readValue(page,{selector:'#quote',attribute:'text',equals:'wrong',timeout_ms:100}), /Expected|expect|Timed out/);
  } finally { await browser.close(); }
});

test('frozen target guards reject wrong controls before mutating the page', async () => {
  const { guardTarget, selectValue } = await import('../page-actions.mjs');
  assert.equal(typeof guardTarget, 'function');
  assert.equal(typeof selectValue, 'function');
  const browser = await chromium.launch({headless:true});
  try {
    const page = await browser.newPage();
    await page.setContent(`<button id="female" onclick="document.body.dataset.clicked='yes'">Female</button>
      <section><h2>Basic</h2><button id="basic">Buy now</button></section>
      <section><h2>Plus</h2><button id="plus">Buy now</button></section>
      <label>Age<input id="age" readonly role="combobox" value="25" onclick="document.querySelector('#options').hidden=false"></label>
      <div id="options" hidden><button role="option" onclick="document.querySelector('#age').value='30'">30</button></div>`);
    await assert.rejects(guardTarget(page, {selector:'#female',target_texts:['Male']}), /target/);
    assert.equal(await page.locator('body').getAttribute('data-clicked'), null);
    await assert.rejects(guardTarget(page, {selector:'#basic',target_texts:['Buy now'],scope_text:'Plus'}), /scope/);
    await guardTarget(page, {selector:'#plus',target_texts:['Buy now'],scope_text:'Plus'});
    const action={selector:'#age',target_texts:['Age'],value:'30'};
    await guardTarget(page, action);
    await selectValue(page, action);
    assert.equal(await page.locator('#age').inputValue(), '30');
  } finally { await browser.close(); }
});

test('positive assertion waits for one actual nonzero monetary value', async () => {
  const browser = await chromium.launch({headless:true});
  try {
    const page=await browser.newPage();
    await page.setContent('<output id="premium">HK$0.00/month</output>');
    await assert.rejects(readValue(page,{selector:'#premium',positive:true,timeout_ms:100}), /Expected/);
    await page.locator('#premium').evaluate(n=>n.textContent='10% discount HK$12.50');
    await assert.rejects(readValue(page,{selector:'#premium',positive:true,timeout_ms:100}), /Expected/);
    await page.locator('#premium').evaluate(n=>n.textContent='HK$12.50/month');
    assert.equal(await readValue(page,{selector:'#premium',positive:true}), 'HK$12.50/month');
  } finally {await browser.close();}
});

test('selection cannot substitute another option in the same field or another card scope', async () => {
  const {guardTarget,selectValue}=await import('../page-actions.mjs');
  const browser=await chromium.launch({headless:true});
  try {
    const page=await browser.newPage();
    await page.setContent(`<label>Female<input type="radio" name="gender" id="female" value="f"></label>
      <label>Male<input type="radio" name="gender" id="male" value="m"></label>
      <div><section>Basic<button id="basic">Buy now</button></section><section>Plus<button>Buy now</button></section></div>`);
    await guardTarget(page,{selector:'#female',target_texts:['gender']});
    await assert.rejects(selectValue(page,{selector:'#female',value:'Male'}),/option/);
    assert.equal(await page.locator('#female').isChecked(),false);
    await selectValue(page,{selector:'#male',value:'Male'});
    assert.equal(await page.locator('#male').isChecked(),true);
    await assert.rejects(guardTarget(page,{selector:'#basic',target_texts:['Buy now'],scope_text:'Plus'}),/scope/);
  } finally {await browser.close();}
});


test('select preserves an already open combobox and selects the exact option', async () => {
  const {selectValue} = await import('../page-actions.mjs');
  const browser = await chromium.launch({headless:true});
  try {
    const page = await browser.newPage();
    await page.setContent(`<input id="age" role="combobox" readonly value="25" aria-expanded="true"
      onclick="this.setAttribute('aria-expanded',this.getAttribute('aria-expanded')==='true'?'false':'true'); document.querySelector('#options').hidden=this.getAttribute('aria-expanded')!=='true'">
      <div id="options" role="listbox">
        <button role="option" onclick="document.querySelector('#age').value='130'">130</button>
        <button role="option" onclick="document.querySelector('#age').value='30'">30</button>
      </div>`);
    await selectValue(page,{selector:'#age',value:'30'});
    assert.equal(await readValue(page,{selector:'#age',attribute:'value',equals:'30'}),'30');
    assert.equal(await page.locator('#age').getAttribute('aria-expanded'),'true');
  } finally { await browser.close(); }
});

test('select ignores hidden exact text duplicates when only one option is visible', async () => {
  const {selectValue} = await import('../page-actions.mjs');
  const browser = await chromium.launch({headless:true});
  try {
    const page = await browser.newPage();
    await page.setContent(`<input id="age" readonly value="25" onclick="document.querySelector('#options').hidden=false">
      <span hidden>30</span><div id="options" hidden>
        <button onclick="document.querySelector('#age').value='130'">130</button>
        <button onclick="document.querySelector('#age').value='30'">30</button>
      </div>`);
    await selectValue(page,{selector:'#age',value:'30'});
    assert.equal(await readValue(page,{selector:'#age',attribute:'value',equals:'30'}),'30');
  } finally { await browser.close(); }
});

test('select rejects two visible exact text matches without choosing either', async () => {
  const {selectValue} = await import('../page-actions.mjs');
  const browser = await chromium.launch({headless:true});
  try {
    const page = await browser.newPage();
    await page.setContent(`<input id="age" readonly value="25" onclick="document.querySelector('#options').hidden=false">
      <div id="options" hidden>
        <button onclick="document.querySelector('#age').value='30'">30</button>
        <button onclick="document.querySelector('#age').value='30'">30</button>
      </div>`);
    await assert.rejects(selectValue(page,{selector:'#age',value:'30'}), /strict mode violation/);
    assert.equal(await readValue(page,{selector:'#age',attribute:'value'}),'25');
  } finally { await browser.close(); }
});

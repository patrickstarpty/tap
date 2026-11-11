// Exercise real inspector pre-dispatch checks against task-owned Chromium pages.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import {chromium} from '@playwright/test';
import {createDocumentProvenance} from '../document-provenance.mjs';
import {observeFields,readValue,guardTarget,selectValue} from '../page-actions.mjs';
import {modelReceipt} from '../browser-tools.mjs';

async function fixture(t,html,{afterFill, beforeFill}={}) {
  const browser=await chromium.launch({headless:true});
  const context=await browser.newContext();const page=await context.newPage();
  const root=fs.mkdtempSync(path.join(os.tmpdir(),'tap-fill-preflight-'));
  t.after(async()=>{await browser.close();fs.rmSync(root,{recursive:true,force:true});});
  await page.setContent('<h1>Available controls</h1>'+html);
  await page.evaluate(()=>{
    window.controlWrites=0;
    for(const name of ['input','change','focus'])document.addEventListener(name,()=>window.controlWrites++,true);
  });
  const source=fs.readFileSync(new URL('../inspector.mjs',import.meta.url),'utf8')
    .replace(/^import .*;\n/gm,'').split('try { fs.unlinkSync(socketPath); }')[0];
  let guardCalls=0;
  const observedGuard=async(...args)=>{guardCalls++;return guardTarget(...args);};
  const observe=await new (Object.getPrototypeOf(async function(){}).constructor)(
    'fs','net','chromium','observeFields','readValue','guardTarget','selectValue','process','createDocumentProvenance',source+'\nreturn observe;'
  )(fs,{}, {launchPersistentContext:async()=>context},observeFields,readValue,observedGuard,selectValue,
    {env:{TAP_BROWSER_LOCAL_DIR:root}},createDocumentProvenance);
  let fills=0;
  const locator=page.locator.bind(page);
  page.locator=(...args)=>{
    const found=locator(...args);const fill=found.fill.bind(found);
    found.fill=async(value,options)=>{
      fills++;await beforeFill?.(page);
      await fill(value,{...options,timeout:200});
      await afterFill?.(page);
    };
    return found;
  };
  const run=async(action)=>{
    const receipt=await observe({type:'fill',value:'30',...action});
    const projected=JSON.parse(modelReceipt(receipt,{type:'fill'},root));
    assert.equal(projected.write_started,receipt.write_started);
    return receipt;
  };
  return {page,run,fills:()=>fills,guardCalls:()=>guardCalls};
}

for(const [name,html] of [
  ['ordinary div','<div id="target">Age</div>'],
  ['combobox container','<div id="target" role="combobox" aria-label="Age" tabindex="0"><input id="child" value="25"></div>'],
  ['label container','<label id="target">Age<input id="child" value="25"></label>'],
  ['native select','<select id="target"><option>25</option><option>30</option></select>'],
  ['contenteditable false','<div contenteditable="true"><span id="target" contenteditable="false">25</span></div>'],
  ...['button','checkbox','file','hidden','image','radio','reset','submit'].map(type=>[`input ${type}`,`<input id="target" type="${type}" value="25">`]),
]) test(`fill rejects ${name} before dispatch without retargeting`,async t=>{
  const {page,run,fills}=await fixture(t,html);
  let selector='#target';
  if(name==='combobox container') {
    const snapshot=await page.ariaSnapshot({mode:'ai'});
    selector='aria-ref='+snapshot.split('\n').find(line=>line.includes('combobox "Age"')).match(/\[ref=([^\]]+)\]/)[1];
  }
  const result=await run({selector});
  assert.equal(result.write_started,false);
  assert.match(result.error,/before write.*snapshot|before write.*inspect/);
  assert.equal(fills(),0,'unsupported fill must never enter Playwright dispatch');
  assert.equal(await page.evaluate(()=>window.controlWrites),0);
  if(await page.locator('#child').count())assert.equal(await page.locator('#child').inputValue(),'25');
  assert.ok(result.bounded_snapshot || result.fields.length,'replanning retains current evidence');
});

for(const [name,html] of [
  ['readonly input','<input id="target" readonly value="25">'],
  ['readonly textarea','<textarea id="target" readonly>25</textarea>'],
  ['ARIA readonly editor','<div id="target" role="textbox" contenteditable="true" aria-readonly="true">25</div>'],
]) test(`fill rejects ${name} before dispatch`,async t=>{
  const {page,run,fills}=await fixture(t,html);
  const result=await run({selector:'#target'});
  assert.equal(result.write_started,false);
  assert.match(result.error,/readonly.*before write.*select|readonly.*before write.*inspect/);
  assert.equal(fills(),0);assert.equal(await page.evaluate(()=>window.controlWrites),0);
});

for(const [type,value] of [
  ['', '30'],['text','30'],['email','test@example.test'],['number','30'],['password','synthetic-test-value'],
  ['search','30'],['tel','30'],['url','https://example.test/'],['color','#123456'],['date','2026-11-11'],
  ['time','12:30'],['datetime-local','2026-11-11T12:30'],['month','2026-11'],['range','30'],['week','2026-W46'],
]) test(`fill preserves supported native input ${type || 'default text'}`,async t=>{
  const {page,run,fills}=await fixture(t,`<input id="target" ${type?`type="${type}"`:''}>`);
  const result=await run({selector:'#target',value});
  assert.equal(result.error,null);assert.equal(result.write_started,true);assert.equal(fills(),1);
  assert.equal(await page.locator('#target').inputValue(),value);
});

for(const [name,html] of [
  ['textarea','<textarea id="target"></textarea>'],
  ['contenteditable true','<div id="target" contenteditable="true"></div>'],
  ['contenteditable empty','<div id="target" contenteditable></div>'],
  ['contenteditable plaintext','<div id="target" contenteditable="plaintext-only"></div>'],
]) test(`fill preserves ${name}`,async t=>{
  const {page,run,fills}=await fixture(t,html);
  const result=await run({selector:'#target',value:'30'});
  assert.equal(result.error,null);assert.equal(result.write_started,true);assert.equal(fills(),1);
  const actual=await page.locator('#target').evaluate(node=>node.value ?? node.innerText);
  assert.equal(actual,'30');
});

test('a failure after a real fill remains a started write even with an editability-looking message',async t=>{
  const {page,run,fills}=await fixture(t,'<input id="target" value="25">',{
    afterFill:async()=>{throw new Error('Element is not an <input>, <textarea> or [contenteditable] element');},
  });
  const result=await run({selector:'#target'});
  assert.match(result.error,/not an <input>/);assert.equal(result.write_started,true);
  assert.equal(fills(),1);assert.equal(await page.locator('#target').inputValue(),'30');
});

test('a target changed after preflight remains a started write rather than a safe retry',async t=>{
  const {page,run,fills}=await fixture(t,'<input id="target" value="25">',{
    beforeFill:async page=>page.locator('#target').evaluate(node=>node.type='checkbox'),
  });
  const result=await run({selector:'#target'});
  assert.ok(result.error);assert.equal(result.write_started,true);assert.equal(fills(),1);
  assert.equal(await page.locator('#target').inputValue(),'25');
});


for(const type of ['click','fill','select','press']) test(`${type} missing target is rejected before the target contract evaluates`,async t=>{
  const {page,run,fills,guardCalls}=await fixture(t,'<input id="available" aria-label="Age" value="25">');
  const start=Date.now();
  const result=await run({type,selector:'#missing',target_texts:['Age'],key:'Enter'});
  assert.equal(guardCalls(),0,'missing direct-write targets must not enter the auto-waiting contract evaluator');
  assert.ok(Date.now()-start<3000,'a missing target must not wait for the 30-second guard timeout');
  assert.equal(result.write_started,false);assert.match(result.error,/no target.*before write.*fresh.*observation|no target.*before write.*fresh snapshot/i);
  assert.equal(fills(),0);assert.equal(await page.evaluate(()=>window.controlWrites),0);
  assert.ok(result.bounded_snapshot.includes('Age'));
});

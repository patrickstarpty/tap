import assert from 'node:assert/strict';
import test from 'node:test';
import {chromium} from '@playwright/test';
import {observeFields} from '../page-actions.mjs';

async function fixture(t, html) {
  const browser=await chromium.launch({headless:true});
  t.after(()=>browser.close());
  const page=await browser.newPage();await page.setContent(html);return page;
}
const identities=fields=>fields.flatMap(field=>field.discovery_identity?[field.discovery_identity]:[]);

test('discovery identity never copies identifier, aria label, value or options',async t=>{
  const page=await fixture(t,'<label>Age<input id="SECRET_ID" name="SECRET_NAME" aria-label="SECRET_ARIA" value="SECRET_VALUE"></label><select id="SECRET_SELECT"><option>SECRET_OPTION</option></select><button id="SECRET_BUTTON">Calculate</button>');
  const fields=await observeFields(page);
  assert.equal(fields.length,3);
  const safe=identities(fields);
  assert.equal(safe.length,3);
  assert.equal(safe.find(i=>i.tag==='input').label,'Age');
  assert.equal(safe.find(i=>i.tag==='select').label,undefined);
  assert.ok(!JSON.stringify(safe).includes('SECRET'));
  for(const identity of safe){
    assert.equal(await page.locator(identity.selector).count(),1);
    assert.ok(!identity.selector.includes('#'));
    assert.ok(Object.keys(identity).every(key=>['selector','tag','role','label'].includes(key)));
  }
});

test('protected controls, ancestry, descendants and referenced labels fail closed',async t=>{
  const page=await fixture(t,`<input type="password" id="password"><input type="hidden" id="hidden"><div data-private><button id="parent">SECRET</button></div><button id="child">Safe<span data-secret>SECRET</span></button><label data-sensitive id="secretLabel">SECRET</label><input id="ref" aria-labelledby="secretLabel"><input id="cc" autocomplete="SECTION-PAYMENT billing CC-NUMBER"><input id="otp" autocomplete="one-time-code"><button id="editable"><span contenteditable="true">SECRET_EDIT</span></button><button id="ok" role="SECRET_ROLE">Visible</button>`);
  const fields=await observeFields(page);
  for(const name of ['password','hidden','parent','child','ref','cc','otp']) assert.equal(fields.find(f=>f.id===name)?.discovery_identity,undefined,name);
  const editable=fields.find(f=>f.id==='editable').discovery_identity;
  assert.equal(editable?.label,undefined);
  assert.equal(fields.find(f=>f.id==='ok').discovery_identity.role,undefined);
  assert.ok(!JSON.stringify(identities(fields)).includes('SECRET'));
});

test('frame discovery identities use independent structural owner chain',async t=>{
  const page=await fixture(t,'<iframe id="SECRET_FRAME" name="SECRET_NAME"></iframe>');
  const frame=page.frames()[1];await frame.setContent('<button id="SECRET_BUTTON">Continue</button>');
  const fields=await observeFields(page,{type:'snapshot',selector:'iframe >> internal:control=enter-frame >> button'});
  const identity=fields.find(f=>f.tag==='button').discovery_identity;
  assert.ok(identity);
  assert.ok(identity.selector.includes('internal:control=enter-frame'));
  assert.ok(!JSON.stringify(identity).includes('SECRET'));
  assert.equal(await page.locator(identity.selector).count(),1);
  await page.locator('iframe').evaluate(node=>node.setAttribute('data-secret',''));
  const hidden=await observeFields(page,{type:'snapshot',selector:'iframe >> internal:control=enter-frame >> button'});
  assert.equal(hidden.find(f=>f.tag==='button').discovery_identity,undefined);
});

test('deep unrepresentable path is omitted rather than truncated; field cap/order unchanged',async t=>{
  const page=await fixture(t,'<div>'.repeat(90)+'<button id="deep">Deep</button>'+'</div>'.repeat(90)+Array.from({length:110},(_,i)=>`<button id="b${i}">B${i}</button>`).join(''));
  const fields=await observeFields(page);
  assert.equal(fields.length,100);
  assert.equal(fields[0].id,'deep');
  assert.equal(fields[0].discovery_identity,undefined);
  assert.equal(fields[1].id,'b0');
});

test('invisible descendant text and invisible labelledby sources never become labels',async t=>{
  const page=await fixture(t,`<button id="mixed">Safe<span style="opacity:0">SECRET_OPACITY</span><span style="display:none">SECRET_DISPLAY</span><span style="visibility:hidden">SECRET_VISIBILITY</span><span style="content-visibility:hidden">SECRET_CONTENT</span></button><div style="opacity:0"><span id="invisibleLabel">SECRET_PARENT_OPACITY</span></div><input id="ref" aria-labelledby="invisibleLabel"><button id="aria">Visible<span aria-hidden="true">SECRET_ARIA_HIDDEN</span></button><button id="long">${'Long '.repeat(40)}</button>`);
  const fields=await observeFields(page);
  assert.equal(fields.find(f=>f.id==='mixed').discovery_identity.label,'Safe');
  assert.equal(fields.find(f=>f.id==='ref').discovery_identity,undefined);
  assert.equal(fields.find(f=>f.id==='aria').discovery_identity,undefined);
  assert.equal(fields.find(f=>f.id==='long').discovery_identity.label,undefined);
  assert.ok(!JSON.stringify(identities(fields)).includes('SECRET'));
});

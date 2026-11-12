// Real target resolution/actuation and inspector catch; only socket transport is in-memory.
import assert from 'node:assert/strict';
import test from 'node:test';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {EventEmitter} from 'node:events';
import {chromium} from '@playwright/test';
import * as live from '../live-control.mjs';
import {createDocumentProvenance} from '../document-provenance.mjs';
import {observeFields,readValue,guardTarget,selectValue} from '../page-actions.mjs';

async function fixture(t, {fakeCountError=false, failAfterActuation=false}={}) {
  const browser=await chromium.launch({headless:true});
  const context=await browser.newContext();
  const page=await context.newPage();
  await page.setContent('<button class="dup" onclick="window.writes++">Buy online</button><button class="dup" onclick="window.writes++">Buy online</button><button id="single" onclick="window.writes++">Only</button>');
  await page.evaluate(()=>{window.writes=0;});
  const root=fs.mkdtempSync(path.join(os.tmpdir(),'tap-target-proof-'));
  t.after(async()=>{await browser.close();fs.rmSync(root,{recursive:true,force:true});});
  if(fakeCountError) {
    const locator=page.locator.bind(page);
    page.locator=(selector)=>{
      const target=locator(selector);
      target.count=async()=>{throw Object.assign(new Error('target missing or ambiguous'),{pre_actuation_failure:{phase:'target_resolution',category:'target_missing'},write_started:false});};
      return target;
    };
  }
  let connection;
  const net={createServer:handler=>{connection=handler;return {listen:(socket,ready)=>{fs.writeFileSync(socket,'');ready();}};}};
  const source=fs.readFileSync(new URL('../inspector.mjs',import.meta.url),'utf8')
    .replace(/^import .*;\n/gm,'').split("if (localRoot) process.once('SIGTERM'")[0];
  const createLiveControl=options=>live.createLiveControl({...options,
    observe:failAfterActuation?async(action,actuate)=>options.observe(action,actuate?async()=>{await actuate();throw Object.assign(new Error('target missing or ambiguous'),{pre_actuation_failure:{phase:'target_resolution',category:'target_missing'},write_started:false});}:undefined):options.observe,
  });
  await new (Object.getPrototypeOf(async function(){}).constructor)(
    'fs','net','chromium','observeFields','readValue','guardTarget','selectValue','process',
    'createDocumentProvenance','createLiveControl','serialDispatcher','preActuationFailure',source
  )(fs,net,{launchPersistentContext:async()=>context},observeFields,readValue,guardTarget,selectValue,
    {env:{TAP_BROWSER_CONTROLLED:'1',TAP_BROWSER_LOCAL_DIR:root,TAP_BROWSER_SOCKET:path.join(root,'socket'),TAP_BROWSER_SESSION:'owned'}},
    createDocumentProvenance,createLiveControl,live.serialDispatcher,live.preActuationFailure);
  const dispatch=action=>new Promise((resolve,reject)=>{
    const client=new EventEmitter();client.end=raw=>resolve(JSON.parse(raw));client.destroy=()=>reject(Error('socket rejected'));
    connection(client);client.emit('data',JSON.stringify(action)+'\n');
  });
  return {page,dispatch};
}

for(const [selector,category] of [['#absent','target_missing'],['text=Buy online','target_ambiguous']]) {
  test(`controlled ${category} has structural pre-actuation proof and no mutation`,async t=>{
    const {page,dispatch}=await fixture(t);
    const receipt=await dispatch({type:'click',selector,actor_kind:'agent',control_epoch:0});
    assert.equal(receipt.write_started,false);
    assert.deepEqual(receipt.extracted.pre_actuation_failure,{phase:'target_resolution',category});
    assert.equal(await page.evaluate(()=>window.writes),0);
    assert.ok(!JSON.stringify(receipt).includes(selector));
    // A failed resolution did not dispatch or block the controller.
    const next=await dispatch({type:'click',selector:'#single',actor_kind:'agent',control_epoch:0});
    assert.equal(next.error,null);
    assert.equal(await page.evaluate(()=>window.writes),1);
  });
}
test('forged equal message and error properties cannot mint pre-actuation proof',async t=>{
  const {page,dispatch}=await fixture(t,{fakeCountError:true});
  const receipt=await dispatch({type:'click',selector:'#single',actor_kind:'agent',control_epoch:0});
  assert.equal(receipt.write_started,undefined);
  assert.equal(receipt.extracted?.pre_actuation_failure,undefined);
  assert.equal(await page.evaluate(()=>window.writes),0);
});
test('failure after actual actuator mutation remains unknown without prewrite proof',async t=>{
  const {page,dispatch}=await fixture(t,{failAfterActuation:true});
  const receipt=await dispatch({type:'click',selector:'#single',actor_kind:'agent',control_epoch:0});
  assert.match(receipt.error,/outcome unknown/);
  assert.equal(receipt.write_started,undefined);
  assert.equal(receipt.extracted?.pre_actuation_failure,undefined);
  assert.equal(live.preActuationFailure(Object.assign(new Error('target missing or ambiguous'),{pre_actuation_failure:{phase:'target_resolution',category:'target_missing'},write_started:false})),null);
  assert.equal(await page.evaluate(()=>window.writes),1);
});

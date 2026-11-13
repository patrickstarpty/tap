// Owned Chromium differential: diagnoses native-vs-controlled behavior, not AA acceptance.
import assert from 'node:assert/strict';
import test from 'node:test';
import {chromium} from '@playwright/test';
import {createLiveControl} from '../live-control.mjs';

async function fixture(t) {
  const browser=await chromium.launch({headless:true});
  t.after(()=>browser.close());
  const page=await browser.newPage();
  await page.setContent(`<button id="open">Choose product</button><div id="options" hidden><button id="choice">Premium</button></div><output id="chosen"></output><input id="text"><div id="editor" contenteditable="true"></div><script>
    window.events=[];
    document.querySelector('#open').addEventListener('pointerdown',e=>{events.push({type:e.type,trusted:e.isTrusted});document.querySelector('#options').hidden=false;});
    document.querySelector('#choice').addEventListener('click',()=>document.querySelector('#chosen').textContent='Premium');
    document.querySelector('#text').addEventListener('keydown',e=>{if(e.key==='Enter')document.querySelector('#chosen').textContent=e.target.value;});
  </script>`);
  let sequence=0;
  const control=createLiveControl({page,observe:async(action,actuate)=>{
    if(actuate)await actuate();return {sequence:++sequence,error:null};
  }});
  return {page,dispatch:action=>control.dispatch({...action,actor_kind:'agent',control_epoch:0})};
}

test('native pointer click opens a real widget that controlled DOM click silently leaves closed',async t=>{
  const {page,dispatch}=await fixture(t);
  const receipt=await dispatch({type:'click',selector:'#open'});
  assert.equal(receipt.error,null);
  assert.equal(await page.locator('#options').isVisible(),false);
  assert.deepEqual(await page.evaluate(()=>window.events),[]);
  await page.locator('#open').click();
  assert.equal(await page.locator('#options').isVisible(),true);
  assert.deepEqual(await page.evaluate(()=>window.events),[{type:'pointerdown',trusted:true}]);
  await page.locator('#choice').click();
  assert.equal(await page.locator('#chosen').innerText(),'Premium');
});

test('native contenteditable fill works where controlled fill is unsupported',async t=>{
  const {page,dispatch}=await fixture(t);
  await assert.rejects(dispatch({type:'fill',selector:'#editor',value:'owned text'}),/controlled fill target unsupported/);
  assert.equal(await page.locator('#editor').innerText(),'');
  await page.locator('#editor').fill('owned text');
  assert.equal(await page.locator('#editor').innerText(),'owned text');
});

test('native keyboard input works where controlled press is unsupported',async t=>{
  const {page,dispatch}=await fixture(t);
  await page.locator('#text').fill('owned result');
  await assert.rejects(dispatch({type:'press',selector:'#text',key:'Enter'}),/controlled press unsupported/);
  assert.equal(await page.locator('#chosen').innerText(),'');
  await page.locator('#text').press('Enter');
  assert.equal(await page.locator('#chosen').innerText(),'owned result');
});

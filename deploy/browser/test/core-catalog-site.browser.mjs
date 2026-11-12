import assert from 'node:assert/strict';
import test from 'node:test';
import {spawn} from 'node:child_process';
import {createServer} from 'node:net';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {chromium} from '@playwright/test';
import {observeFields} from '../page-actions.mjs';
import {createLiveControl} from '../live-control.mjs';
import {modelReceipt} from '../browser-tools.mjs';

async function ownedSite(t, catalog) {
  const socket=createServer();await new Promise(resolve=>socket.listen(0,'127.0.0.1',resolve));
  const port=socket.address().port;await new Promise(resolve=>socket.close(resolve));
  const child=spawn(process.execPath,[fileURLToPath(new URL('../../../scripts/browser_core_site.mjs',import.meta.url))],{
    env:{...process.env,TAP_CORE_SITE_HOST:'127.0.0.1',TAP_CORE_SITE_PORT:String(port),TAP_CORE_SITE_CATALOG:catalog?'1':'0'},stdio:'ignore'});
  t.after(async()=>{if(child.exitCode===null){child.kill('SIGTERM');await new Promise(resolve=>child.once('exit',resolve));}});
  const origin=`http://127.0.0.1:${port}`;
  for(let attempt=0;attempt<40;attempt++){
    try {if((await fetch(origin)).ok)return origin;}catch{}
    await new Promise(resolve=>setTimeout(resolve,25));
  }
  throw new Error('owned fixture not ready');
}

async function agentControl(page) {
  let sequence=0, completedWrites=0;
  const control=createLiveControl({page,observe:async (_action,actuate)=>{
    if(actuate){await actuate();completedWrites++;}
    return {sequence:++sequence,error:null};
  }});
  await control.dispatch({type:'control_sync',controller_kind:'agent',control_epoch:1});
  return {dispatch:action=>control.dispatch({actor_kind:'agent',control_epoch:1,...action}),completedWrites:()=>completedWrites};
}

test('default review remains three controls and one submit',async t=>{
  const origin=await ownedSite(t,false);const html=await(await fetch(origin+'/review')).text();
  assert.equal((html.match(/<input /g)||[]).length,2);
  assert.equal((html.match(/<button /g)||[]).length,1);
  assert.ok(!html.includes('Ask a question'));
});

test('filled catalog form has eight neutral identities and correct scroll/click result',async t=>{
  const origin=await ownedSite(t,true);
  const browser=await chromium.launch({headless:true});t.after(()=>browser.close());
  const page=await browser.newPage({viewport:{width:900,height:600}});await page.goto(origin+'/review');
  for (const name of ['Account','Amount']) {
    const input=page.getByLabel(name,{exact:true});
    assert.equal(await input.isVisible(),true);
    const box=await input.boundingBox();
    assert.ok(box && box.x+box.width/2>0 && box.x+box.width/2<900 && box.y+box.height/2>0 && box.y+box.height/2<600, `${name} center must initially be in viewport`);
  }
  const initial=await page.getByRole('button',{name:'Check request',exact:true}).boundingBox();
  assert.ok(initial.y>600);
  const control=await agentControl(page);
  for (const [name,value] of [['account','AC-42'],['amount','120']]) {
    const selector=`[name=${name}]`;
    const preflight=await control.dispatch({type:'preflight',operation:'fill',selector});
    assert.equal(preflight.extracted.preflight.safe,true);
    const receipt=await control.dispatch({type:'fill',selector,value});
    assert.equal(receipt.error,null);
    assert.equal(receipt.extracted.control_receipt.args.value,value);
    assert.equal(await page.locator(selector).inputValue(),value);
  }
  assert.equal(control.completedWrites(),2);
  const fields=await observeFields(page,{type:'read',selector:'[name=amount]'});
  const directory=fs.mkdtempSync(path.join(os.tmpdir(),'core-catalog-site-'));t.after(()=>fs.rmSync(directory,{recursive:true,force:true}));
  const view=JSON.parse(modelReceipt({sequence:3,url:page.url(),fields,bounded_snapshot:await page.locator('body').innerText(),error:null},{type:'read',selector:'[name=amount]'},directory));
  assert.equal(view.control_catalog.catalog_count,8);assert.equal(view.control_catalog.omitted,0);
  assert.ok(!JSON.stringify({related_fields:view.related_fields,discovery_candidates:view.discovery_candidates}).includes('Check request'));
  const rows=fs.readFileSync(view.control_catalog.file,'utf8').trim().split('\n').map(line=>JSON.parse(line));
  assert.deepEqual(rows.map(row=>row.label),['Amount','Account','Ask a question','Option 2','Option 3','Option 4','Option 5','Check request']);
  assert.ok(!JSON.stringify(rows).includes('AC-42'));assert.ok(!JSON.stringify(rows).includes('120'));
  assert.equal(await page.locator('body').getAttribute('data-wrong-action'),null);
  const clicked=await control.dispatch({type:'click',selector:rows[7].selector});
  assert.equal(clicked.error,null);
  assert.equal(control.completedWrites(),3);
  assert.equal(await page.locator('#result').innerText(),'AC-42 / 120 / MATCH');
});

test('old background-before-input layout rejects controlled fill without mutation',async t=>{
  const origin=await ownedSite(t,true);
  const browser=await chromium.launch({headless:true});t.after(()=>browser.close());
  const page=await browser.newPage({viewport:{width:900,height:600}});await page.goto(origin+'/review');
  await page.locator('form p').evaluate(node=>document.querySelector('h1').after(node));
  const control=await agentControl(page);
  const preflight=await control.dispatch({type:'preflight',operation:'fill',selector:'[name=account]'});
  assert.equal(preflight.extracted.preflight.safe,true);
  await assert.rejects(control.dispatch({type:'fill',selector:'[name=account]',value:'AC-42'}),/target obscured or outside viewport/);
  assert.equal(control.completedWrites(),0);
  assert.equal(await page.locator('[name=account]').inputValue(),'');
});

import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import {chromium} from '@playwright/test';
import plugin from '../browser-tools.mjs';

test('live role/ref aliases click and fill the observed main-frame and iframe targets once', async t => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(),'tap-live-alias-'));
  for (const name of ['requests','responses','acks']) fs.mkdirSync(path.join(root,name));
  const oldMailbox = process.env.TAP_CODING_MAILBOX;
  process.env.TAP_CODING_MAILBOX = root;
  const browser = await chromium.launch({headless:true});
  t.after(async()=>{
    await browser.close();
    if (oldMailbox===undefined) delete process.env.TAP_CODING_MAILBOX;
    else process.env.TAP_CODING_MAILBOX=oldMailbox;
    fs.rmSync(root,{recursive:true,force:true});
  });
  const page = await browser.newPage();
  await page.setContent('<button onclick="this.dataset.writes=Number(this.dataset.writes || 0)+1">Continue</button><iframe></iframe>');
  const frame = page.frames().find(frame=>frame!==page.mainFrame());
  await frame.setContent('<input aria-label="Age" value="25" oninput="this.dataset.writes=Number(this.dataset.writes || 0)+1">');
  const snapshot = await page.ariaSnapshot({mode:'ai'});
  const ref = label=>snapshot.split('\n').find(line=>line.includes(`"${label}"`)).match(/\[ref=([^\]]+)\]/)[1];
  const tool = (await plugin()).tool.tap_browser;
  let dispatched = 0;
  for (const action of [
    {type:'click',step:'continue',selector:`button[ref='${ref('Continue')}']`},
    {type:'fill',step:'age',selector:`textbox[ref='${ref('Age')}']`,value:'30'},
  ]) {
    const pending = tool.execute({action},{directory:root,abort:new AbortController().signal,ask:async()=>{}});
    let files=[];
    for (let i=0;i<100&&!files.length;i++) {
      files=fs.readdirSync(path.join(root,'requests')).filter(name=>name.endsWith('.json'));
      if (!files.length) await new Promise(resolve=>setTimeout(resolve,5));
    }
    assert.equal(files.length,1);
    const file=files[0];
    const {payload}=JSON.parse(fs.readFileSync(path.join(root,'requests',file),'utf8'));
    dispatched++;
    let failure;
    try {
      if (payload.type==='click') await page.locator(payload.selector).click({timeout:1000});
      else await page.locator(payload.selector).fill(payload.value,{timeout:1000});
    } catch(error) {failure=error;}
    fs.writeFileSync(path.join(root,'responses',file),JSON.stringify({status:failure?'failed':'completed'}),{mode:0o600});
    await pending;
    if (failure) throw failure;
  }
  assert.equal(dispatched,2);
  assert.equal(await page.locator('button').getAttribute('data-writes'),'1');
  assert.equal(await frame.locator('input').inputValue(),'30');
  assert.equal(await frame.locator('input').getAttribute('data-writes'),'1');
});

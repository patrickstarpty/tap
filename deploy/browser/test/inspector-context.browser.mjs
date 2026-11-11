import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import {chromium} from '@playwright/test';
import {observeFields, readValue, guardTarget, selectValue} from '../page-actions.mjs';
import plugin from '../browser-tools.mjs';

async function fixture(t, iframe, expandedBackground = false) {
  const browser = await chromium.launch({headless:true});
  const context = await browser.newContext();
  const page = await context.newPage();
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'tap-observation-context-'));
  for (const name of ['requests','responses','acks']) fs.mkdirSync(path.join(root,name));
  const oldMailbox = process.env.TAP_CODING_MAILBOX;
  process.env.TAP_CODING_MAILBOX = root;
  t.after(async()=>{
    await browser.close();
    if (oldMailbox === undefined) delete process.env.TAP_CODING_MAILBOX;
    else process.env.TAP_CODING_MAILBOX = oldMailbox;
    fs.rmSync(root,{recursive:true,force:true});
  });
  let frame = page.mainFrame();
  if (iframe) {
    await page.setContent('<h1>Main page</h1><iframe id="application" style="height:700px"></iframe>');
    frame = page.frames().find(frame=>frame!==page.mainFrame());
  }
  await frame.setContent(
    Array.from({length:120},(_,i)=>`<button ${expandedBackground && i < 10 ? 'aria-expanded="true"' : ''}>Background ${i} ${'irrelevant content '.repeat(12)}</button>`).join('')
    + '<label for="age">Applicant age</label><input id="age" readonly role="combobox" aria-expanded="false" aria-controls="ages"'
    + ' onclick="this.dataset.writes=Number(this.dataset.writes || 0)+1;this.setAttribute(\'aria-expanded\',\'true\');document.querySelector(\'#ages\').hidden=false">'
    + '<div id="ages" role="listbox" aria-label="Available ages" hidden><div id="age30" role="option">30 years</div><div role="option">31 years</div></div>'
  );
  const source = fs.readFileSync(new URL('../inspector.mjs',import.meta.url),'utf8')
    .replace(/^import .*;\n/gm,'').split('try { fs.unlinkSync(socketPath); }')[0];
  const observe = await new (Object.getPrototypeOf(async function(){}).constructor)(
    'fs','net','chromium','observeFields','readValue','guardTarget','selectValue','process', source+'\nreturn observe;'
  )(fs,{}, {launchPersistentContext:async()=>context},observeFields,readValue,guardTarget,selectValue,
    {env:{TAP_BROWSER_LOCAL_DIR:root}});
  const tool = (await plugin()).tool.tap_browser;
  async function execute(action) {
    const pending = tool.execute({action},{directory:root,abort:new AbortController().signal,ask:async()=>{}});
    let file;
    for (let i=0;i<100&&!file;i++) {
      file=fs.readdirSync(path.join(root,'requests')).find(name=>name.endsWith('.json'));
      if (!file) await new Promise(resolve=>setTimeout(resolve,5));
    }
    assert.ok(file);
    const {payload}=JSON.parse(fs.readFileSync(path.join(root,'requests',file),'utf8'));
    const receipt=await observe(payload);
    fs.writeFileSync(path.join(root,'responses',file),JSON.stringify(receipt),{mode:0o600});
    return JSON.parse(await pending);
  }
  return {page, frame, execute};
}

for (const [iframe, expandedBackground] of [[false,false],[true,false],[false,true]]) {
  test(`${iframe?'iframe':'main frame'}${expandedBackground?' with other expanded controls':''}: tail popup and readonly facts survive a long page without another write`, async t=>{
    const {page,frame,execute}=await fixture(t,iframe,expandedBackground);
    const initial=await page.ariaSnapshot({mode:'ai'});
    assert.ok(initial.length>16000,'fixture must exceed both observation prefixes');
    const target=initial.split('\n').find(line=>line.includes('combobox "Applicant age"')).match(/\[ref=([^\]]+)\]/)[1];
    const outside=initial.match(/\[ref=([^\]]+)\]/)[1];
    const result=await execute({type:'click',step:'open_age',selector:`aria-ref=${target}`});
    assert.equal(result.error,null);
    assert.doesNotMatch(result.bounded_snapshot,/Available ages/,'the tail must not accidentally fit in the page prefix');
    assert.ok(JSON.stringify(result).length<8000,'action context must replace part of the existing page budget');
    const facts=result.related_fields || [];
    const control=facts.find(field=>field.id==='age');
    assert.ok(control,'model must see its actual target even after 100 earlier fields');
    assert.equal(control.readonly,true);
    assert.equal(control.expanded,'true');
    const popup=facts.find(field=>field.id==='ages');
    assert.ok(popup,'model must see the visible associated popup beyond the snapshot prefix');
    assert.equal(await page.locator(popup.selector).innerText({timeout:1000}),'30 years\n31 years');
    assert.equal(await page.locator(control.selector).getAttribute('id'),'age');
    const option=facts.find(field=>field.id==='age30');
    assert.ok(option);
    assert.equal(await page.locator(option.selector).innerText({timeout:1000}),'30 years');
    assert.equal(await frame.locator('#age').getAttribute('data-writes'),'1');
    assert.equal(await page.locator(`aria-ref=${outside}`).count(),1,'context collection must not replace the full AI ref map');
    const evidence=JSON.parse(fs.readFileSync(result.evidence_file,'utf8'));
    assert.ok(evidence.fields.some(field=>field.id==='ages'));
    assert.ok(evidence.bounded_snapshot.length>result.bounded_snapshot.length);
  });
}


test('an invalid action selector preserves the error and still exposes the visible popup', async t=>{
  const {execute}=await fixture(t,false);
  await execute({type:'click',step:'open_age',selector:'#age'});
  const result=await execute({type:'click',step:'missing',selector:'css=['});
  assert.match(result.error,/Unexpected token/);
  assert.ok(result.related_fields?.some(field=>field.id==='ages'));
});


test('an external native calendar without popup ARIA exposes visible selects without claiming association', async t=>{
  const {page,frame,execute}=await fixture(t,false);
  await frame.locator('#age').evaluate(node=>{
    node.removeAttribute('role');
    node.removeAttribute('aria-controls');
    node.removeAttribute('aria-expanded');
    node.onclick=()=>{
      node.dataset.writes=Number(node.dataset.writes || 0)+1;
      document.querySelector('#calendar').hidden=false;
    };
    const calendar=document.createElement('div');
    calendar.id='calendar';
    calendar.hidden=true;
    calendar.style='position:fixed;top:100px;left:100px;background:white';
    calendar.innerHTML='<select id="month" aria-label="Month"><option value="11">November</option><option value="12">December</option></select><select id="year" aria-label="Year"><option>2026</option><option>2027</option></select>';
    document.body.append(calendar);
  });
  const result=await execute({type:'click',step:'date',selector:'#age'});
  assert.equal(result.error,null);
  assert.ok(result.related_fields?.find(field=>field.id==='age')?.readonly);
  const month=result.related_fields?.find(field=>field.id==='month');
  assert.ok(month,'visible native calendar controls must not disappear just because the widget lacks popup ARIA');
  assert.equal(month.context,'visible_control');
  assert.deepEqual(month.options,[{label:'November',value:'11'},{label:'December',value:'12'}]);
  assert.equal(await page.locator(month.selector).inputValue(),'11');
  assert.ok(result.related_fields.some(field=>field.id==='year'));
  assert.ok(JSON.stringify(result).length<8000);
  assert.equal(await frame.locator('#age').getAttribute('data-writes'),'1');
});

test('a pointer-blocking overlay beyond the page prefix reaches the model with its controls', async t=>{
  const {page,frame,execute}=await fixture(t,false);
  await frame.locator('body').evaluate(body=>{
    const overlay=document.createElement('div');
    overlay.id='blocking-overlay';
    overlay.style='position:fixed;inset:0;z-index:1000;background:white';
    overlay.innerHTML='<p>Review this notice before continuing</p><button id="dismiss-notice">Close notice</button>';
    body.append(overlay);
  });
  const result=await execute({type:'click',step:'blocked_age',selector:'#age'});
  assert.match(result.error,/intercepts pointer events/);
  assert.equal(result.write_started,true);
  assert.doesNotMatch(result.bounded_snapshot,/Review this notice/);
  const blocker=result.related_fields?.find(field=>field.context==='pointer_blocker');
  assert.ok(blocker,'the model must see the actual blocking element');
  assert.equal(await page.locator(blocker.selector).getAttribute('id'),'blocking-overlay');
  const close=result.related_fields?.find(field=>field.id==='dismiss-notice');
  assert.ok(close,'the model must see a usable control on the blocker');
  assert.equal(await page.locator(close.selector).innerText(),'Close notice');
  assert.ok(JSON.stringify(result).length<8000);
});

test('long expanded popup keeps late options in bounded model context', async t=>{
  const {frame,execute}=await fixture(t,false);
  await frame.locator('#ages').evaluate(root=>{
    root.innerHTML=Array.from({length:48},(_,i)=>`<div role="option" id="age${i+18}">${i+18} years</div>`).join('');
  });
  const result=await execute({type:'click',step:'open_age',selector:'#age'});
  assert.equal(result.error,null);
  assert.doesNotMatch(result.bounded_snapshot,/30 years/);
  const popup=result.related_fields?.find(field=>field.id==='ages');
  assert.ok(popup);
  assert.match(popup.popup_text,/30 years/);
  assert.match(popup.popup_text,/65 years/);
  assert.ok(JSON.stringify(result).length<8000);
});

test('a semantic modal exposes only its visible close control', async t=>{
  const {page,frame,execute}=await fixture(t,false);
  await frame.locator('body').evaluate(body=>{
    const hidden=document.createElement('button');
    hidden.setAttribute('aria-label','Close dialog'); hidden.hidden=true; body.append(hidden);
    const modal=document.createElement('div');
    modal.id='notice-dialog'; modal.setAttribute('role','dialog'); modal.setAttribute('aria-modal','true');
    modal.style='position:fixed;inset:0;z-index:1000;background:white';
    modal.innerHTML='<p>Read this before proceeding</p><button aria-label="Close dialog">Dismiss</button>';
    body.append(modal);
  });
  const result=await execute({type:'click',step:'blocked_age',selector:'#age'});
  assert.match(result.error,/intercepts pointer events/);
  assert.equal(result.write_started,true);
  const modal=result.related_fields?.find(field=>field.id==='notice-dialog');
  assert.ok(modal);
  assert.match(modal.popup_text,/Read this before proceeding/);
  const closes=result.related_fields?.filter(field=>field.label==='Close dialog') || [];
  assert.equal(closes.length,1);
  assert.equal(await page.locator(closes[0].selector).isVisible(),true);
});

test('normal target descendants and offscreen targets are not reported as pointer blockers', async t=>{
  const {page,frame}=await fixture(t,false);
  await frame.locator('body').evaluate(body=>{
    const button=document.createElement('button');
    button.id='nested-target'; button.style='position:fixed;top:10px;left:10px';
    button.innerHTML='<span id="target-child">Press here</span>';
    body.append(button);
  });
  const nested=await observeFields(page,{selector:'#nested-target'});
  assert.equal(nested.some(field=>field.context==='pointer_blocker'),false);
  const offscreen=await observeFields(page,{selector:'#age'});
  assert.equal(offscreen.some(field=>field.context==='pointer_blocker'),false);
});

test('pointer blocker in an iframe keeps a frame-qualified usable selector', async t=>{
  const {page,frame}=await fixture(t,true);
  await frame.locator('body').evaluate(body=>{
    const overlay=document.createElement('div');
    overlay.id='frame-blocker';
    overlay.style='position:fixed;inset:0;z-index:1000;background:white';
    overlay.innerHTML='<button id="frame-close">Close</button>';
    body.append(overlay);
  });
  await frame.locator('#age').scrollIntoViewIfNeeded();
  const fields=await observeFields(page,{selector:'#application >> internal:control=enter-frame >> #age'});
  const blocker=fields.find(field=>field.id==='frame-blocker');
  assert.equal(blocker?.context,'pointer_blocker');
  assert.equal(await page.locator(blocker.selector).getAttribute('id'),'frame-blocker');
  const close=fields.find(field=>field.id==='frame-close');
  assert.equal(await page.locator(close.selector).innerText(),'Close');
});

test('popup text over the DOM cap reports truncation to the model', async t=>{
  const {frame,execute}=await fixture(t,false);
  await frame.locator('body').evaluate(body=>{
    const dialog=document.createElement('div');
    dialog.id='long-notice'; dialog.setAttribute('role','dialog');
    dialog.textContent='Notice '.repeat(180);
    body.append(dialog);
  });
  const result=await execute({type:'snapshot',step:'inspect_notice'});
  const popup=result.related_fields?.find(field=>field.id==='long-notice');
  assert.ok(popup);
  assert.equal(popup.popup_text_truncated,true);
  assert.ok(popup.popup_text.length<=1000);
});

test('long popup selector survives the model budget by shortening text', async t=>{
  const {page,frame,execute}=await fixture(t,false);
  await frame.locator('body').evaluate(body=>{
    let parent=body;
    for(let i=0;i<45;i++){const child=document.createElement('div');parent.append(child);parent=child;}
    const dialog=document.createElement('div'); dialog.setAttribute('role','dialog');
    dialog.textContent='Notice '.repeat(140); parent.append(dialog);
  });
  const raw=await observeFields(page,{});
  const popup=raw.find(field=>field.role==='dialog');
  assert.ok(popup.selector.length>700 && popup.selector.length<1000,'fixture needs a long but usable selector');
  const result=await execute({type:'snapshot',step:'inspect_deep_notice'});
  const projected=result.related_fields?.find(field=>field.role==='dialog');
  assert.ok(projected,'popup identity and selector must survive text pressure');
  assert.equal(projected.selector,popup.selector);
  assert.equal(await page.locator(projected.selector).getAttribute('role'),'dialog');
  assert.equal(projected.popup_text_truncated,true);
  assert.ok(result.related_fields_omitted>0 || projected.popup_text.length<popup.popup_text.length);
  assert.ok(JSON.stringify(result.related_fields).length<=2200);
});

test('an unaffordable context reports omission and points to full evidence', async t=>{
  const {page,frame,execute}=await fixture(t,false);
  await frame.locator('body').evaluate(body=>{
    let parent=body;
    for(let i=0;i<140;i++){const child=document.createElement('div');parent.append(child);parent=child;}
    const dialog=document.createElement('div'); dialog.setAttribute('role','dialog');
    dialog.textContent='Deep notice'; parent.append(dialog);
  });
  const popup=(await observeFields(page,{})).find(field=>field.role==='dialog');
  assert.ok(popup.selector.length>2200,'fixture needs a selector that cannot fit');
  const result=await execute({type:'snapshot',step:'inspect_deep'});
  assert.ok(result.related_fields_omitted>0);
  assert.match(result.observation_detail,/evidence_file|scoped snapshot/);
  assert.ok(fs.existsSync(result.evidence_file));
  assert.ok(JSON.stringify(result.related_fields || []).length<=2200);
});

test('malformed selector is rejected before a write is started with recoverable evidence', async t=>{
  const {execute} = await fixture(t,false);
  const result=await execute({type:'fill',step:'operate',selector:'[',value:'30'});
  assert.match(result.error,/selector/);
  assert.equal(result.write_started,false);
  assert.ok(result.bounded_snapshot);
});

test('readonly fill is rejected before dispatch so the agent can choose select', async t=>{
  const {execute,page} = await fixture(t,false);
  const result=await execute({type:'fill',step:'operate',selector:'#age',value:'30'});
  assert.match(result.error,/readonly.*select/i);
  assert.equal(result.write_started,false);
  assert.equal(await page.locator('#age').getAttribute('data-writes'),null);
});

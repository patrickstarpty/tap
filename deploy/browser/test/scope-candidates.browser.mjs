import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import {chromium} from '@playwright/test';
import {observeFields,readValue,guardTarget,selectValue} from '../page-actions.mjs';
import {createDocumentProvenance} from '../document-provenance.mjs';
import {modelReceipt} from '../browser-tools.mjs';

async function fixture(t,html,{iframe=false}={}) {
  const browser=await chromium.launch({headless:true});
  const context=await browser.newContext();const page=await context.newPage();
  const root=fs.mkdtempSync(path.join(os.tmpdir(),'tap-scope-candidates-'));
  t.after(async()=>{await browser.close();fs.rmSync(root,{recursive:true,force:true});});
  let frame=page.mainFrame();
  if(iframe){await page.setContent('<iframe aria-label="Application" style="width:1000px;height:700px"></iframe>');frame=page.frames().find(item=>item!==page.mainFrame());}
  await frame.setContent(html);
  await frame.evaluate(()=>{window.observationWrites=0;for(const event of ['focus','click','input','change'])document.addEventListener(event,()=>window.observationWrites++,true);});
  const source=fs.readFileSync(new URL('../inspector.mjs',import.meta.url),'utf8').replace(/^import .*;\n/gm,'').split('try { fs.unlinkSync(socketPath); }')[0];
  const observe=await new(Object.getPrototypeOf(async function(){}).constructor)(
    'fs','net','chromium','observeFields','readValue','guardTarget','selectValue','process','createDocumentProvenance',source+'\nreturn observe;'
  )(fs,{}, {launchPersistentContext:async()=>context},observeFields,readValue,guardTarget,selectValue,{env:{TAP_BROWSER_LOCAL_DIR:root}},createDocumentProvenance);
  const execute=async action=>JSON.parse(modelReceipt(await observe(action),action,root));
  return {page,frame,execute};
}

const widget='<div class="calendar"><div><select aria-label="Month"><option>March</option></select><select aria-label="Year"><option>1996</option></select></div><table><tbody><tr><td><button>11</button></td></tr></tbody></table></div>';

for(const iframe of [false,true]) test(`${iframe?'iframe':'main'}: safe scopes are owner-local identities and observation writes nothing`,async t=>{
  const {page,frame}=await fixture(t,widget,{iframe});
  const selector=iframe?'iframe >> internal:control=enter-frame >> select[aria-label="Month"]':'select[aria-label="Month"]';
  const fields=await observeFields(page,{selector});
  const target=fields.find(field=>field.context==='action_target');
  assert.equal(target.scope_candidates.length,2);
  assert.ok(target.scope_candidates.some(scope=>scope.tag==='table'));
  for(const scope of target.scope_candidates){
    assert.deepEqual(Object.keys(scope).sort(),['relation','role','selector','tag']);
    assert.ok(scope.selector.length<=1000);assert.equal(await page.locator(scope.selector).count(),1);
    if(iframe)assert.match(scope.selector,/enter-frame/);
  }
  assert.equal(await frame.evaluate(()=>window.observationWrites),0);
  assert.ok(!JSON.stringify(target.scope_candidates).includes('1996'),'scope identity must not aggregate ancestor text or selection values');
});

for(const [name,html] of [
  ['body/form scope',`<form><select aria-label="Month"><option>March</option></select></form>`],
  ['large descendant tree',`<div><select aria-label="Month"><option>March</option></select>${'<span></span>'.repeat(257)}</div>`],
  ['too many interactive controls',`<div><select aria-label="Month"><option>March</option></select>${'<button>day</button>'.repeat(65)}</div>`],
  ['protected hidden subtree',`<div><select aria-label="Month"><option>March</option></select><input type="hidden" data-sensitive value="SCOPE_SECRET_SENTINEL"></div>`],
  ['hidden payload',`<div><select aria-label="Month"><option>March</option></select><input type="hidden" name="session" value="SCOPE_SECRET_SENTINEL"></div>`],
  ['password control',`<div><select aria-label="Month"><option>March</option></select><input type="password" value="SCOPE_SECRET_SENTINEL"></div>`],
  ['compound payment autocomplete',`<div><select aria-label="Month"><option>March</option></select><input autocomplete="section-payment billing cc-number" value="SCOPE_SECRET_SENTINEL"></div>`],
  ['compound verification autocomplete',`<div><select aria-label="Month"><option>March</option></select><input autocomplete="section-login one-time-code" value="SCOPE_SECRET_SENTINEL"></div>`],
  ['mixed-case protected autocomplete',`<div><select aria-label="Month"><option>March</option></select><input autocomplete="SECTION-PAYMENT BILLING CC-NUMBER" value="SCOPE_SECRET_SENTINEL"></div>`],
  ['aria-hidden ancestor',`<div aria-hidden="true"><select aria-label="Month"><option>March</option></select></div>`],
]) test(`unsafe observation scope is not offered: ${name}`,async t=>{
  const {page,frame}=await fixture(t,html);
  const fields=await observeFields(page,{selector:'select'});
  assert.ok(fields.every(field=>!field.scope_candidates?.length));
  assert.equal(await frame.evaluate(()=>window.observationWrites),0);
});

test('two calendars never manufacture a shared widget or unique grid binding',async t=>{
  const {page}=await fixture(t,`<div>${widget}${widget}</div>`);
  const fields=await observeFields(page,{selector:'select[aria-label="Month"] >> nth=0'});
  const scopes=fields.find(field=>field.context==='action_target').scope_candidates;
  assert.equal(scopes.length,2);
  for(const scope of scopes)assert.equal(await page.locator(scope.selector).locator('table').count()+(scope.tag==='table'?1:0),1);
  assert.ok(scopes.every(scope=>!('association' in scope)));
});

test('long-page calendar uses only returned scopes and current scoped refs after native-select redraw',async t=>{
  const {page,frame,execute}=await fixture(t,Array.from({length:150},(_,i)=>`<button>Background ${i} ${'filler '.repeat(25)}</button>`).join('')
    +'<label style="position:fixed;top:5px;left:5px">Date of birth<input readonly aria-label="Date of birth" value="01/01/2000"></label><div hidden style="position:fixed;top:80px;left:10px;background:white"></div>');
  await frame.evaluate(()=>{
    const input=document.querySelector('input'),calendar=document.querySelector('body > div');let month='11',year='2026';
    const draw=()=>{
      calendar.innerHTML='<div><select aria-label="Month">'+Array.from({length:12},(_,i)=>`<option value="${i+1}" ${month===String(i+1)?'selected':''}>Month ${i+1}</option>`).join('')+'</select>'
        +'<select aria-label="Year">'+Array.from({length:127},(_,i)=>`<option ${year===String(1900+i)?'selected':''}>${1900+i}</option>`).join('')+'</select></div>'
        +'<table><tbody><tr>'+Array.from({length:31},(_,i)=>`<td><button>${i+1}</button></td>`).join('')+'</tr></tbody></table>';
      const [months,years]=calendar.querySelectorAll('select');months.onchange=()=>{month=months.value;draw();};years.onchange=()=>{year=years.value;draw();};
      for(const button of calendar.querySelectorAll('button'))button.onclick=()=>{input.value=`${button.textContent.padStart(2,'0')}/${month.padStart(2,'0')}/${year}`;calendar.hidden=true;};
    };input.onclick=()=>{calendar.hidden=false;draw();};
  });
  const initial=await execute({type:'snapshot'});const dob=initial.related_fields.find(field=>field.label==='Date of birth');
  const opened=await execute({type:'click',selector:dob.selector});
  assert.doesNotMatch(opened.bounded_snapshot,/button "11"/);
  const year=opened.related_fields.find(field=>field.label==='Year');
  const yearScope=await execute({type:'snapshot',selector:year.selector});
  assert.ok(yearScope.bounded_snapshot.includes('1996') || yearScope.related_fields.find(field=>field.context==='action_target').options.some(option=>option.value==='1996'));
  const years=await execute({type:'read',selector:year.selector,name:'available_years',attribute:'text',contains:'1996'});assert.equal(years.error,null);
  const changedYear=await execute({type:'select',selector:year.selector,value:'1996'});
  const month=changedYear.related_fields.find(field=>field.label==='Month');
  const changedMonth=await execute({type:'select',selector:month.selector,value:'3'});
  assert.doesNotMatch(changedMonth.bounded_snapshot,/button "11"/);
  const scope=changedMonth.scope_candidates.find(candidate=>candidate.tag==='table');assert.ok(scope);
  const writes=await frame.evaluate(()=>window.observationWrites);
  const scoped=await execute({type:'snapshot',selector:scope.selector});
  assert.equal(await frame.evaluate(()=>window.observationWrites),writes);
  const line=scoped.bounded_snapshot.split('\n').find(item=>/button "11"/.test(item));assert.ok(line,'scoped snapshot must keep the current day ref ahead of unrelated option detail');
  const selected=await execute({type:'click',selector:'aria-ref='+line.match(/\[ref=([^\]]+)\]/)[1]});assert.equal(selected.error,null);
  const verified=await execute({type:'read',selector:dob.selector,name:'dob',attribute:'value',equals:'11/03/1996'});
  assert.equal(verified.extracted.value,'11/03/1996');assert.equal(verified.error,null);
  assert.ok(JSON.stringify(scoped).length<8000);
});

for(const length of [1000,1001]) test(`iframe scope selectors enforce the ${length}-character limit after prefixing`,async t=>{
  const {page,frame}=await fixture(t,widget,{iframe:true});
  await frame.locator('table').evaluate(node=>node.id='day-table');
  const action={selector:'iframe >> internal:control=enter-frame >> select[aria-label="Month"]'};
  const before=(await observeFields(page,action)).find(field=>field.context==='action_target');
  const prefix=before.selector.slice(0,before.selector.indexOf('select['));
  await frame.locator('.calendar').evaluate((node,size)=>node.id='x'.repeat(size),length-prefix.length-1);
  const target=(await observeFields(page,action)).find(field=>field.context==='action_target');
  const ancestor=target.scope_candidates.find(scope=>scope.relation==='ancestor_scope');
  assert.equal(Boolean(ancestor),length===1000);
  if(ancestor){assert.equal(ancestor.selector.length,1000);assert.equal(await page.locator(ancestor.selector).count(),1);}
  assert.ok(target.scope_candidates.every(scope=>scope.selector.length<=1000));
  assert.equal(await frame.evaluate(()=>window.observationWrites),0);
});

test('two visible grids are observation context, never a claimed unique descendant grid',async t=>{
  const {page}=await fixture(t,'<div><select aria-label="Month"><option>March</option></select><table><tr><td>First</td></tr></table><table><tr><td>Second</td></tr></table></div>');
  const target=(await observeFields(page,{selector:'select'})).find(field=>field.context==='action_target');
  assert.ok(target.scope_candidates.every(scope=>scope.relation==='ancestor_scope'));
});

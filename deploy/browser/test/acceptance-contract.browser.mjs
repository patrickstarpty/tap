// Owned Chromium fixture: no model, third-party site or middleware.
import assert from 'node:assert/strict';
import test from 'node:test';
import {spawnSync} from 'node:child_process';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
const require=createRequire(new URL('../../../apps/tap-ai-frontend/package.json',import.meta.url));
const {chromium,expect}=require('@playwright/test');
const root=fileURLToPath(new URL('../../../',import.meta.url));
const contract={version:1,reviewed:true,fields:[{id:'dob',value:'1996-11-08',readback_value:'1996-11-08',attribute:'value',target_texts:['Birth date']}]};
const base=[
  {type:'navigate',url:'https://owned.test/form',step:'open'},
  {type:'fill',selector:'#dob',value:'1996-11-08',target_texts:['Birth date'],step:'set'},
  {type:'read',selector:'#dob',attribute:'value',equals:'1996-11-08',name:'dob',target_texts:['Birth date'],step:'verify'},
  {type:'click',selector:'#next',target_texts:['Calculate'],step:'next'},
  {type:'read',selector:'#quote',attribute:'text',positive:true,name:'quote',target_texts:['Quote'],step:'result'},
];
function compile(actions) {
  const python=`import sys,json
from tap.modules.workflows.application.direct_browser import DirectBrowserResult,compile_replay
from tap.modules.workflows.domain.acceptance_contract import BrowserAcceptanceContract
p=json.load(sys.stdin);a=p['actions']
steps={x['step']:{'id':x['step'],'keyword':'Then' if x['type']=='read' else 'Given' if x['type']=='navigate' else 'When','title':x['step']} for x in a}
print(compile_replay(DirectBrowserResult('passed',{},a,steps,acceptance_contract=BrowserAcceptanceContract.model_validate(p['contract']))))`;
  return spawnSync(root+'.venv/bin/python',['-c',python],{cwd:root,input:JSON.stringify({actions,contract}),encoding:'utf8'});
}
for (const variant of ['correct', 'default-value', 'changed-expected', 'omitted', 'wrong-target', 'wrong-selector', 'mutated', 'refreshed', 'unknown']) {
  test(`reviewed input replay fails closed: ${variant}`,async()=>{
    const actions=structuredClone(base);
    if(variant==='default-value') actions.splice(1,1);
    if(variant==='changed-expected') actions[2].equals='1990-01-01';
    if(variant==='omitted') actions.splice(2,1);
    if(variant==='wrong-target') actions[2].target_texts=['Quote'];
    if(variant==='wrong-selector') actions[2].selector='#unrelated';
    if(variant==='mutated') actions.splice(3,0,{...actions[1],value:'1990-01-01',step:'mutate'});
    if(variant==='refreshed') actions.splice(3,0,{...actions[1],step:'rewrite'},{...actions[2],step:'recheck'});
    const compiled=compile(actions);
    if(['changed-expected','omitted','wrong-target'].includes(variant)) {
      assert.notEqual(compiled.status,0);assert.match(compiled.stderr,/reviewed input readback/);return;
    }
    assert.equal(compiled.status,0,compiled.stderr);
    assert.ok(compiled.stdout.includes('1996-11-08'));
    const browser=await chromium.launch({headless:true});
    try {
      const page=await browser.newPage();
      await page.route('https://owned.test/**',route=>route.fulfill({contentType:'text/html',body:route.request().url().endsWith('/form')?
        '<label>Birth date<input id="dob" value="1990-01-01"></label><label>Other date<input id="unrelated" value="1996-11-08"></label><button id="next" onclick="location.href=\'/result\'">Calculate</button>':
        '<output id="quote" aria-label="Quote">HK$35.00</output>'}));
      let completed,attached;
      const run=(_name,body)=>{completed=body({page},{attach:async(_name,data)=>{attached=JSON.parse(data.body.toString());}});};
      run.setTimeout=()=>{};
      run.step=async(name,body)=>{if(variant==='unknown' && name==='Then result')await page.close();return body();};
      const source=compiled.stdout.replace("import { test, expect } from '@playwright/test';",'').replace(': Record<string, string | null>','');
      new Function('test','expect',source)(run,expect.configure({timeout:200}));
      if(['correct','refreshed'].includes(variant)) {
        await completed;assert.deepEqual(attached,{dob:'1996-11-08',quote:'HK$35.00'});
      } else {
        await assert.rejects(completed);assert.equal(attached,undefined);
      }
    } finally {await browser.close();}
  });
}

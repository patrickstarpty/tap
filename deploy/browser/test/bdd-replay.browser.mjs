import assert from 'node:assert/strict';
import test from 'node:test';
import {spawnSync} from 'node:child_process';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
import {guardTarget, selectValue, readValue} from '../page-actions.mjs';
const require=createRequire(new URL('../../../apps/tap-ai-frontend/package.json',import.meta.url));
const {chromium,expect}=require('@playwright/test');
const root=fileURLToPath(new URL('../../../',import.meta.url));

for (const variant of ['closed', 'already-open', 'hidden-duplicate']) {
test(`confirmed BDD actions replay ${variant} readonly selection and dynamic results`,async()=>{
  const browser=await chromium.launch({headless:true});
  const open=variant==='already-open';
  const optionRole=variant==='hidden-duplicate'?'':'role="option"';
  const html=`<label>Age<input id="age" role="combobox" readonly value="25" aria-expanded="${open}"
      onclick="this.setAttribute('aria-expanded',this.getAttribute('aria-expanded')==='true'?'false':'true');document.querySelector('#options').hidden=this.getAttribute('aria-expanded')!=='true'"></label>
    ${variant==='hidden-duplicate'?'<span hidden>30</span>':''}
    <div id="options" ${open?'':'hidden'}>
      <button ${optionRole} onclick="document.querySelector('#age').value='130'">130</button>
      <button ${optionRole} onclick="document.querySelector('#age').value='30';document.querySelector('#options').hidden=true">30</button>
    </div>
    <button id="quote" onclick="setTimeout(()=>document.querySelector('#premium').textContent='HK$'+(Number(document.querySelector('#age').value)*2).toFixed(2)+'/month',100)">Get quote</button>
    <output id="premium">HK$0.00/month</output>`;
  const actions=[
    {type:'navigate',url:'https://example.test/quote',step:'open'},
    {type:'select',selector:'#age',value:'30',target_texts:['Age'],step:'age'},
    {type:'read',selector:'#age',attribute:'value',equals:'30',name:'age',target_texts:['Age'],step:'check-age'},
    {type:'click',selector:'#quote',target_texts:['Get quote'],step:'quote'},
    {type:'read',selector:'#premium',attribute:'text',positive:true,name:'premium',target_texts:['Premium'],step:'check-premium'},
  ];
  try {
    const original=await browser.newPage();
    await original.route('https://example.test/quote',route=>route.fulfill({contentType:'text/html',body:html}));
    const facts={};
    for(const action of actions){
      if(action.type==='navigate') await original.goto(action.url);
      else {
        await guardTarget(original,action);
        if(action.type==='select') await selectValue(original,action);
        if(action.type==='click') await original.locator(action.selector).click();
        if(action.type==='read') facts[action.name]=await readValue(original,action);
      }
    }
    assert.deepEqual(facts,{age:'30',premium:'HK$60.00/month'});
    const python=`import sys,json
from tap.modules.workflows.application.direct_browser import DirectBrowserResult,compile_replay
a=json.load(sys.stdin)
steps={x['step']:{'id':x['step'],'keyword':'Then' if x['type']=='read' else 'Given' if x['type']=='navigate' else 'When','title':x['step']} for x in a}
print(compile_replay(DirectBrowserResult('passed',{'premium':'DO NOT EMBED'},a,steps)))`;
    const compiled=spawnSync(root+'.venv/bin/python',['-c',python],{cwd:root,input:JSON.stringify(actions),encoding:'utf8'});
    assert.equal(compiled.status,0,compiled.stderr);
    assert.ok(!compiled.stdout.includes('DO NOT EMBED'));
    const replay=await browser.newPage();
    await replay.route('https://example.test/quote',route=>route.fulfill({contentType:'text/html',body:html}));
    const sequence=[];let completed,attached;
    const run=(name,body)=>{completed=body({page:replay},{attach:async(name,data)=>{attached=JSON.parse(data.body.toString());}});};
    run.setTimeout=()=>{};
    run.step=async(name,body)=>{sequence.push(name);return body();};
    const script=compiled.stdout.replace("import { test, expect } from '@playwright/test';",'').replace(': Record<string, string | null>','');
    new Function('test','expect',script)(run,expect);
    await completed;
    assert.deepEqual(attached,facts);
    assert.deepEqual(sequence,['Given open','When age','Then check-age','When quote','Then check-premium']);
    assert.equal(await replay.locator('#age').inputValue(),'30');
  } finally {await browser.close();}
});
}

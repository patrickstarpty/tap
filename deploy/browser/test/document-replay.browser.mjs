// Deterministic compiled replay, routed owned documents only; no model or Docker.
import assert from 'node:assert/strict';
import test from 'node:test';
import {spawnSync} from 'node:child_process';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
const require = createRequire(new URL('../../../apps/tap-ai-frontend/package.json',import.meta.url));
const {chromium,expect} = require('@playwright/test');
const root = fileURLToPath(new URL('../../../',import.meta.url));
const contract = {version:1,reviewed:true,fields:[{id:'age',value:'30',readback_value:'30',attribute:'value',target_texts:['Age']}]};
function compile(actions, acceptance=contract) {
  const python = `import sys,json
from tap.modules.workflows.application.direct_browser import DirectBrowserResult,compile_replay
from tap.modules.workflows.domain.acceptance_contract import BrowserAcceptanceContract
p=json.load(sys.stdin);a=p['actions']
steps={x['step']:{'keyword':'Then','title':x['step']} for x in a if 'step' in x}
print(compile_replay(DirectBrowserResult('passed',{},a,steps,acceptance_contract=BrowserAcceptanceContract.model_validate(p['contract']))))`;
  const result = spawnSync(root+'.venv/bin/python',['-c',python],{cwd:root,input:JSON.stringify({actions,contract:acceptance}),encoding:'utf8'});
  assert.equal(result.status,0,result.stderr);
  return result.stdout.replace("import { test, expect } from '@playwright/test';",'').replace(': Record<string, string | null>','');
}
for (const iframe of [false,true]) for (const variant of ['immediate','delayed','delayed-reread','mutated-delayed','same-document','same-document-ajax','detached','intervening-write','passive-navigation','return-form','second-passive-navigation']) {
  if (!iframe && variant==='detached') continue;
  test(`document-bound compiled replay ${iframe?'iframe':'main'}: ${variant}`, async () => {
    const prefix = iframe ? '#application >> internal:control=enter-frame >> ' : '';
    const actions = [
      {type:'navigate',url:'https://fixture.test/',step:'open'},
      {type:'read',selector:prefix+'#age',attribute:'value',equals:'30',name:'age',target_texts:['Age'],step:'verify'},
      {type:'click',selector:prefix+'#next',step:'next'},
      {type:'wait',selector:prefix+'#quote',step:'wait'},
      {type:'snapshot'},
      {type:'read',selector:prefix+'#quote',attribute:'text',equals:'42',name:'quote',step:'result'},
    ];
    if (variant==='delayed-reread') actions.splice(3,0,{...actions[1],step:'recheck'});
    if (variant==='passive-navigation') actions.splice(2,1);
    if (variant==='intervening-write') actions.splice(3,0,{type:'press',selector:prefix+'body',key:'Tab',step:'intervene'});
    if (variant==='return-form') actions.splice(4,0,{type:'navigate',url:'https://fixture.test/',step:'return'});
    if (variant==='detached' && iframe) for (const action of actions.slice(3)) if(action.selector) action.selector='#quote';
    const source = compile(actions);
    const browser = await chromium.launch({headless:true});
    try {
      const page = await browser.newPage();
      const script = variant==='mutated-delayed' ? "document.querySelector('#age').value='31';setTimeout(()=>location.href='/result',450)" : variant==='same-document' ? "history.pushState({},'', '/same'); document.querySelector('#age').value='31'; document.querySelector('#quote').hidden=false" :
        variant==='same-document-ajax' ? "document.querySelector('#quote').hidden=false" :
        ['delayed','delayed-reread','intervening-write'].includes(variant) ? "setTimeout(()=>location.href='/result',450)" : "location.href='/result'";
      const form = `<label>Age<input id="age" value="30"></label><button id="next" onclick="${script}">Next</button><output id="quote" hidden>42</output>`;
      await page.route('https://fixture.test/**', route=>{
        const path = new URL(route.request().url()).pathname;
        const body = path==='/result' || path==='/later' ? '<output id="quote">42</output>' :
          iframe && path==='/' ? '<iframe id="application" src="/form"></iframe><output id="quote" hidden>42</output>' : form;
        return route.fulfill({contentType:'text/html',body});
      });
      let completed, attached;
      const run = (_name,body)=>{completed=body({page},{attach:async(_name,data)=>{attached=JSON.parse(data.body.toString());}});};
      run.setTimeout = ()=>{};
      run.step = async (name,body)=>{
        if (variant==='detached' && name==='Then wait') await page.evaluate(()=>{document.querySelector('#application').remove();document.querySelector('#quote').hidden=false;});
        // Own fixtures force changes at exact boundaries, not wall-clock races.
        if (variant==='passive-navigation' && name==='Then wait') await page.frames().find(frame=>iframe ? frame!==page.mainFrame() : frame===page.mainFrame()).goto('https://fixture.test/result');
        if (variant==='second-passive-navigation' && name==='Then result') await page.frames().find(frame=>iframe ? frame!==page.mainFrame() : frame===page.mainFrame()).goto('https://fixture.test/later');
        if (variant==='return-form' && name==='Then result') {
          const frame=iframe?page.frames().find(frame=>frame!==page.mainFrame()):page.mainFrame();
          await frame.locator('#quote').evaluate(node=>node.hidden=false);
        }
        return body();
      };
      new Function('test','expect',source)(run,expect.configure({timeout:500}));
      if (['immediate','delayed','delayed-reread','same-document-ajax'].includes(variant)) {
        await completed;
        assert.deepEqual(attached,{age:'30',quote:'42'});
        if (iframe) assert.equal(page.url(),'https://fixture.test/');
      } else {
        await assert.rejects(completed, /reviewed input proof missing or stale/);
        assert.equal(attached,undefined);
      }
    } finally { await browser.close(); }
  });
}

for (const attribute of ['selected_label','value']) test(`compiled ${attribute} reads only safe associated selection state`, async()=>{
  const expected = attribute === 'selected_label' ? 'Male' : 'M';
  const acceptance={version:1,reviewed:true,fields:[{id:'gender',value:expected,readback_value:expected,attribute,target_texts:['Gender']}]};
  const source=compile([
    {type:'navigate',url:'https://fixture.test/',step:'open'},
    {type:'read',selector:'#gender',attribute,equals:expected,name:'gender',target_texts:['Gender'],step:'verify'},
  ],acceptance);
  const browser=await chromium.launch({headless:true});
  try {
    for (const value of ['M','F']) {
      const page=await browser.newPage();
      await page.route('https://fixture.test/', route=>route.fulfill({contentType:'text/html',body:`<div data-name="gender"><p>Gender</p><input id="gender" type="hidden" name="gender" value="${value}"><span data-value="M">Male</span><span data-value="F">Female</span></div>`}));
      let completed, attached;
      const run=(_name,body)=>{completed=body({page},{attach:async(_name,data)=>{attached=JSON.parse(data.body.toString());}});};
      run.setTimeout=()=>{};run.step=async(_name,body)=>body();
      new Function('test','expect',source)(run,expect.configure({timeout:200}));
      if (value==='M') { await completed; assert.deepEqual(attached,{gender:expected}); }
      else { await assert.rejects(completed); assert.equal(attached,undefined); }
      await page.close();
    }
  } finally {await browser.close();}
});

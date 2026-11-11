// Real generated LCA replay, owned routed documents, no model or Docker.
import assert from 'node:assert/strict';
import test from 'node:test';
import {spawnSync} from 'node:child_process';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
const require = createRequire(new URL('../../../apps/tap-ai-frontend/package.json',import.meta.url));
const {chromium,expect} = require('@playwright/test');
const root = fileURLToPath(new URL('../../../',import.meta.url));
const field = {id:'age',value:'30',readback_value:'30',attribute:'value',target_texts:['Age']};
function python(code,input) {
  const result=spawnSync(root+'.venv/bin/python',['-c',code],{cwd:root,input:JSON.stringify(input),encoding:'utf8'});
  assert.equal(result.status,0,result.stderr); return result.stdout;
}
function compile(iframe, variant) {
  const targets=['age','next','quote','body'].map(id=>({id,kind:'css',value:id==='body'?'body':'#'+id,review_required:true,...(iframe?{frame_target_id:'frame'}:{})}));
  if(iframe) targets.push({id:'frame',kind:'css',value:'#application',review_required:true});
  const steps=[
    {id:'open',operation:'navigate',arguments:{url:'https://fixture.test/'}},
    {id:'verify',operation:'assert_read',target_id:'age',target_contract:{target_texts:['Age']},arguments:{attribute:'value',equals:'30',name:'age'}},
    {id:'next',operation:'click',target_id:'next',arguments:{}},
    {id:'wait',operation:'wait_for',target_id:'quote',arguments:{state:'visible'}},
    {id:'observe',operation:'observe',arguments:{}},
    {id:'result',operation:'assert_read',target_id:'quote',arguments:{attribute:'text',equals:'42',name:'quote'}},
  ];
  if(variant==='passive-navigation') steps.splice(2,1);
  if(variant==='intervening-write') steps.splice(3,0,{id:'intervene',operation:'press',target_id:'body',arguments:{key:'Tab'}});
  if(variant==='return-form') steps.splice(4,0,{id:'return',operation:'navigate',arguments:{url:'https://fixture.test/'}});
  if(iframe && variant==='detached') targets.find(t=>t.id==='quote').frame_target_id=undefined;
  const input={schema_version:'lca-debug-v1',allowed_origins:['https://fixture.test'],parameters:[],targets,steps:steps.map(s=>({...s,timeout_ms:1500})),source_acceptance:{source:{task_id:'task',execution_id:'execution',bundle_id:'bundle',input_sha256:'a'.repeat(64)},input_sha256:'a'.repeat(64),review_status:'reviewed',acceptance_contract:{version:1,reviewed:true,fields:[field]}}};
  return python("import sys,json\nfrom tap_platform.lca.generator import generate_playwright\nprint(generate_playwright(json.load(sys.stdin)).source)",input).replace("import { test, expect } from '@playwright/test';",'');
}
for(const iframe of [false,true]) for(const variant of ['immediate','delayed','mutated-delayed','same-document','pending-no-navigation','detached','intervening-write','passive-navigation','return-form','second-passive-navigation']) {
  if(!iframe && variant==='detached') continue;
  test(`current LCA document proof ${iframe?'iframe':'main'} ${variant}`,async()=>{
    const source=compile(iframe,variant), browser=await chromium.launch({headless:true});
    try {
      const page=await browser.newPage();
      const script=variant==='mutated-delayed'?"document.querySelector('#age').value='31';setTimeout(()=>location.href='/result',450)":variant==='same-document'?"history.pushState({},'', '/same');document.querySelector('#age').value='31';document.querySelector('#quote').hidden=false":variant==='pending-no-navigation'?"document.querySelector('#quote').hidden=false":['delayed','intervening-write'].includes(variant)?"setTimeout(()=>location.href='/result',450)":"location.href='/result'";
      const form=`<label>Age<input id="age" value="30"></label><button id="next" onclick="${script}">Next</button><output id="quote" hidden>42</output>`;
      await page.route('https://fixture.test/**',route=>{const path=new URL(route.request().url()).pathname;return route.fulfill({contentType:'text/html',body:path==='/result'||path==='/later'?'<output id="quote">42</output>':iframe&&path==='/'?'<iframe id="application" src="/form"></iframe><output id="quote" hidden>42</output>':form});});
      let completed,attached;
      const run=(_name,fn)=>{completed=fn({page});};run.info=()=>({attach:async(_name,data)=>{attached=JSON.parse(data.body.toString());}});
      run.step=async(name,fn)=>{
        const frame=()=>page.frames().find(f=>iframe?f!==page.mainFrame():f===page.mainFrame());
        if(variant==='detached'&&name==='lca:wait')await page.evaluate(()=>{document.querySelector('#application').remove();document.querySelector('#quote').hidden=false;});
        if(variant==='passive-navigation'&&name==='lca:wait')await frame().goto('https://fixture.test/result');
        if(variant==='second-passive-navigation'&&name==='lca:result')await frame().goto('https://fixture.test/later');
        if(variant==='return-form'&&name==='lca:result')await frame().locator('#quote').evaluate(n=>n.hidden=false);
        return fn();
      };
      new Function('test','expect',source)(run,expect.configure({timeout:500}));
      await completed;
      assert.ok(attached.steps.every(s=>s.status==='passed'),'technical execution remains independently passed');
      const proven=JSON.parse(python("import sys,json\nfrom tap_platform.lca.document_acceptance import current_field_proven\np=json.load(sys.stdin);print(json.dumps(current_field_proven({'result':{'outputs':[p['result']]}},p['field'],'verify')))",{result:attached,field}));
      assert.equal(proven,['immediate','delayed','pending-no-navigation'].includes(variant));
      if(iframe && variant!=='return-form')assert.equal(page.url(),'https://fixture.test/');
    } finally {await browser.close();}
  });
}

// Merge boundary: real page/file behavior must not retain unrelated input proof.
import assert from 'node:assert/strict';
import test from 'node:test';
import {spawnSync} from 'node:child_process';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
import {mkdtempSync,writeFileSync,rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {createHash} from 'node:crypto';
import {createServer} from 'node:http';
const require=createRequire(new URL('../../../apps/tap-ai-frontend/package.json',import.meta.url));
const {chromium,expect}=require('@playwright/test');
const root=fileURLToPath(new URL('../../../',import.meta.url));
const bytes=Buffer.from('owned merge fixture'),sha=createHash('sha256').update(bytes).digest('hex');
function python(code,input){const result=spawnSync(root+'.venv/bin/python',['-c',code],{cwd:root,input:JSON.stringify(input),encoding:'utf8'});assert.equal(result.status,0,result.stderr);return result.stdout;}
for(const operation of ['read_second_page','open_page','switch_page','close_page','click_popup','click_dialog','upload_file','download_file'])test(`page/file contract boundary ${operation}`,async()=>{
  const expected=operation==='read_second_page'?'31':'30';
  const field={id:'age',value:expected,readback_value:expected,attribute:'value',target_texts:['Age']};
  const steps=[{id:'open',operation:'navigate',arguments:{url:'https://fixture.test/main'}}];
  if(['read_second_page','switch_page','close_page'].includes(operation))steps.push({id:'create',operation:'open_page',arguments:{page_id:'second',url:'https://fixture.test/other'}});
  if(operation==='read_second_page')steps.push({id:'switch',operation:'switch_page',arguments:{page_id:'second'}});
  steps.push({id:'verify',operation:'assert_read',target_id:'age',target_contract:{target_texts:['Age']},arguments:{attribute:'value',name:'age',equals:expected}});
  if(operation!=='read_second_page'){
    const action={id:'after',operation,arguments:{}};
    if(operation==='open_page')action.arguments={page_id:'second',url:'https://fixture.test/other'};
    if(['switch_page','close_page','click_popup'].includes(operation))action.arguments={page_id:'second'};
    if(['click_popup','click_dialog','upload_file','download_file'].includes(operation))action.target_id=operation;
    if(operation==='click_dialog')action.arguments={type:'confirm',message:'Proceed?',action:'dismiss'};
    if(operation==='upload_file')action.arguments={asset_id:'a'.repeat(32),sha256:sha};
    steps.push(action);
  }
  const definition={schema_version:'lca-debug-v1',allowed_origins:['https://fixture.test'],parameters:[],targets:['age','click_popup','click_dialog','upload_file','download_file'].map(id=>({id,kind:'test_id',value:id})),steps:steps.map(s=>({...s,timeout_ms:3000})),source_acceptance:{source:{task_id:'task',execution_id:'execution',bundle_id:'bundle',input_sha256:'a'.repeat(64)},input_sha256:'a'.repeat(64),review_status:'reviewed',acceptance_contract:{version:1,reviewed:true,fields:[field]}}};
  const server=createServer((request,response)=>{
    if(request.url==='/download'){response.writeHead(200,{'content-type':'application/octet-stream','content-disposition':'attachment; filename="fixture.txt"'});response.end(bytes);return;}
    response.writeHead(200,{'content-type':'text/html'});
    response.end(`<label>Age<input data-testid="age" value="${request.url==='/other'?'31':'30'}"></label><button data-testid="click_popup" onclick="window.open('/other')">Open</button><button data-testid="click_dialog" onclick="confirm('Proceed?')">Confirm</button><input data-testid="upload_file" type="file"><a data-testid="download_file" href="/download" download>Download</a>`);
  });
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  const origin=`http://127.0.0.1:${server.address().port}`;
  const ownedDefinition=JSON.parse(JSON.stringify(definition).replaceAll('https://fixture.test',origin));
  const source=python("import sys,json\nfrom tap_platform.lca.generator import generate_playwright\nprint(generate_playwright(json.load(sys.stdin)).source)",ownedDefinition).replace("import { test, expect } from '@playwright/test';",'');
  const directory=mkdtempSync(join(tmpdir(),'lca-page-contract-')),oldInputs=process.env.TAP_BROWSER_INPUTS_PATH;
  process.env.TAP_BROWSER_INPUTS_PATH=join(directory,'inputs.json');
  writeFileSync(process.env.TAP_BROWSER_INPUTS_PATH,JSON.stringify({__lca_assets:{['a'.repeat(32)]:{filename:'fixture.txt',content_type:'text/plain',sha256:sha,content_base64:bytes.toString('base64')}}}));
  const browser=await chromium.launch({headless:true});
  try{
    const context=await browser.newContext({acceptDownloads:true});

    const page=await context.newPage();let completed,attached,downloads=0;
    const run=(_name,fn)=>{completed=fn({page});};run.step=async(_name,fn)=>fn();run.info=()=>({attach:async(name,data)=>{if(name==='tapper-result')attached=JSON.parse(data.body.toString());else if(name.startsWith('lca-download:')){assert.deepEqual(data.body,bytes);downloads++;}}});
    new Function('test','expect',source)(run,expect);await completed;
    assert.ok(attached.steps.every(step=>step.status==='passed'));
    assert.equal(attached.steps.find(step=>step.step_id==='verify').output.value,expected);
    const proven=JSON.parse(python("import sys,json\nfrom tap_platform.lca.document_acceptance import current_field_proven\np=json.load(sys.stdin);print(json.dumps(current_field_proven({'result':{'outputs':[p['result']]}},p['field'],'verify')))",{result:attached,field}));
    assert.equal(proven,operation==='read_second_page');
    if(operation==='upload_file')assert.equal(await page.getByTestId('upload_file').evaluate(node=>node.files[0].name),'fixture.txt');
    if(operation==='download_file')assert.equal(downloads,1);
  }finally{await browser.close();await new Promise(resolve=>server.close(resolve));if(oldInputs===undefined)delete process.env.TAP_BROWSER_INPUTS_PATH;else process.env.TAP_BROWSER_INPUTS_PATH=oldInputs;rmSync(directory,{recursive:true,force:true});}
});

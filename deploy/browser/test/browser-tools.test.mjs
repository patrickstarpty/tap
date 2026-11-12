import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';

async function setup(t) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'tap-native-tool-'));
  for (const name of ['requests', 'responses', 'acks']) fs.mkdirSync(path.join(root, name));
  const old = process.env.TAP_CODING_MAILBOX;
  process.env.TAP_CODING_MAILBOX = root;
  t.after(() => {
    if (old === undefined) delete process.env.TAP_CODING_MAILBOX;
    else process.env.TAP_CODING_MAILBOX = old;
    fs.rmSync(root, {recursive:true, force:true});
  });
  const {default: plugin} = await import('../browser-tools.mjs');
  const loaded = await plugin();
  return {root, tool: loaded.tool.tap_browser};
}

test('native browser tool preserves failure context and complete current controls', async t => {
  const {root, tool} = await setup(t);
  const asks = [];
  const action = {type:'read', step:'verify', selector:'#quote', name:'quote', positive:true};
  const context = {directory:root, abort:new AbortController().signal, ask:async item => asks.push(item)};
  const pending = tool.execute({action}, context);
  let file;
  for (let i=0; i<100 && !file; i++) {
    file = fs.readdirSync(path.join(root, 'requests')).find(name => name.endsWith('.json'));
    if (!file) await new Promise(resolve => setTimeout(resolve, 10));
  }
  assert.ok(file, 'native tool must dispatch to existing mailbox');
  const envelope = JSON.parse(fs.readFileSync(path.join(root, 'requests', file)));
  assert.equal(envelope.kind, 'browser');
  assert.deepEqual(envelope.payload, action);
  const observation = {action_id:'a1', sequence:12, error:'Expected DOM value was not observed; actual: 0',
    bounded_snapshot:'page '.repeat(2200), fields:Array.from({length:80}, (_, i)=>({selector:`#field-${i}`}))};
  fs.writeFileSync(path.join(root, 'responses', file), JSON.stringify(observation), {mode:0o600});
  const compact = JSON.parse(await pending);
  assert.equal(compact.error, observation.error);
  assert.equal(compact.action_id, 'a1');
  assert.equal(compact.sequence, 12);
  assert.ok(JSON.stringify(compact).length < 8000);
  assert.equal(compact.fields, undefined);
  assert.deepEqual(JSON.parse(fs.readFileSync(compact.evidence_file, 'utf8')), observation);
  assert.match(fs.readFileSync(compact.evidence_file, 'utf8'), /field-79/);
  assert.equal(fs.statSync(compact.evidence_file).mode & 0o077, 0);
  assert.equal(asks[0].permission, 'tap_browser');
  assert.deepEqual(asks[0].patterns, ['read']);
});

test('native tool rejects unsupported actions and canceled calls before dispatch', async t => {
  const {root, tool} = await setup(t);
  const controller = new AbortController();
  const context = {abort:controller.signal, ask:async()=>{}};
  await assert.rejects(tool.execute({action:{type:'evaluate', code:'x'}}, context), /unsupported/);
  controller.abort();
  await assert.rejects(tool.execute({action:{type:'snapshot', step:'observe'}}, context), /abort/i);
  assert.deepEqual(fs.readdirSync(path.join(root,'requests')), []);
});

test('permission refusal does not dispatch a browser action', async t => {
  const {root, tool} = await setup(t);
  await assert.rejects(tool.execute({action:{type:'click',selector:'#next',step:'next'}}, {
    abort:new AbortController().signal, ask:async()=>{throw new Error('permission denied');},
  }), /permission denied/);
  assert.deepEqual(fs.readdirSync(path.join(root,'requests')), []);
});

test('successful reads keep actual values without repeating the page', async t => {
  const {root, tool} = await setup(t);
  const responder = setInterval(() => {
    for (const file of fs.readdirSync(path.join(root, 'requests')).filter(x => x.endsWith('.json'))) {
      fs.writeFileSync(path.join(root, 'responses', file), JSON.stringify({
        sequence:2, error:null, extracted:{quote:'123.45'}, bounded_snapshot:'irrelevant '.repeat(2000),
        fields:[{selector:'#quote',value:'123.45'}],
      }), {mode:0o600});
    }
  }, 5);
  try {
    const result = JSON.parse(await tool.execute({action:{type:'read',step:'price',selector:'#quote',name:'quote',positive:true}}, {
      directory:root, abort:new AbortController().signal, ask:async()=>{},
    }));
    assert.deepEqual(result.extracted, {quote:'123.45'});
    assert.ok(JSON.stringify(result).length < 1000);
    assert.match(fs.readFileSync(result.evidence_file, 'utf8'), /irrelevant/);
  } finally { clearInterval(responder); }
});

test('evidence storage failure preserves a completed action receipt instead of inviting retry', async t => {
  const {root, tool} = await setup(t);
  fs.writeFileSync(path.join(root,'.tap-browser-observations'),'occupied');
  let dispatched = 0;
  const receipt = {sequence:5,error:null,extracted:{accepted:'yes'},bounded_snapshot:'Confirmation',fields:[]};
  const responder = setInterval(() => {
    for (const name of fs.readdirSync(path.join(root,'requests')).filter(x=>x.endsWith('.json'))) {
      dispatched++;
      fs.writeFileSync(path.join(root,'responses',name),JSON.stringify(receipt),{mode:0o600});
      fs.unlinkSync(path.join(root,'requests',name));
    }
  },5);
  try {
    const result = JSON.parse(await tool.execute({action:{type:'click',step:'submit',selector:'#submit'}},{directory:root,abort:new AbortController().signal,ask:async()=>{}}));
    assert.equal(result.sequence,5);
    assert.equal(result.error,null);
    assert.deepEqual(result.extracted,{accepted:'yes'});
    assert.equal(result.bounded_snapshot,'Confirmation');
    assert.ok(result.evidence_storage_error);
    assert.equal(dispatched,1);
  } finally { clearInterval(responder); }
});

async function exchange(tool, root, action) {
  const pending = tool.execute({action}, {
    directory:root, abort:new AbortController().signal, ask:async()=>{},
  });
  let files = [];
  for (let i=0; i<100 && !files.length; i++) {
    files = fs.readdirSync(path.join(root,'requests')).filter(name=>name.endsWith('.json'));
    if (!files.length) await new Promise(resolve=>setTimeout(resolve,5));
  }
  assert.equal(files.length,1, 'one tool call must dispatch exactly one browser action');
  const file = files[0];
  const request = JSON.parse(fs.readFileSync(path.join(root,'requests',file),'utf8'));
  fs.writeFileSync(path.join(root,'responses',file),JSON.stringify({status:'completed'}),{mode:0o600});
  await pending;
  assert.equal(fs.readdirSync(path.join(root,'requests')).filter(name=>name.endsWith('.json')).length,0);
  assert.equal(fs.existsSync(path.join(root,'acks',file)),true);
  return request.payload;
}

test('explicit live snapshot aliases dispatch one canonical action without mutating caller input', async t => {
  const {root,tool} = await setup(t);
  for (const [action,canonical] of [
    [{type:'fill',step:'age',selector:"textbox[ref='f6e53']",value:'30'},'aria-ref=f6e53'],
    [{type:'click',step:'quote',selector:'link[ref="e98"]'},'aria-ref=e98'],
    [{type:'snapshot',step:'scope',selector:"generic[ref='f4e46']"},'aria-ref=f4e46'],
  ]) {
    const original = structuredClone(action);
    const sent = await exchange(tool,root,action);
    assert.deepEqual(sent,{...original,selector:canonical});
    assert.deepEqual(action,original);
  }
});

test('CSS opt-out, compound selectors, and non-alias selectors pass through byte-for-byte', async t => {
  const {root,tool} = await setup(t);
  for (const selector of [
    "css=textbox[ref='f6e53']",
    "textbox[ref='f6e53'] > span",
    "section textbox[ref='f6e53']",
    "[ref='f6e53']",
    "input[ref='f6e53']",
    "textbox[ref='f6e53'] ",
    "textbox[ref='f6e53']\n",
    "textbox[ref='f6e53']\r\n",
    "textbox[ref='f6e53\"]",
    "textbox[ref='e0']",
    "textbox[ref='other']",
    'aria-ref=f6e53',
    '#age',
  ]) {
    const sent = await exchange(tool,root,{type:'click',step:'target',selector});
    assert.equal(sent.selector,selector);
  }
});

for (const [type, terms] of [
  ['fill', [/editable/i, /readonly/i]],
  ['select', [/native/i, /radio/i, /checkbox/i, /custom/i, /exact/i, /read/i]],
]) {
  test(`native tool describes ${type} control semantics and readback`, async t => {
    const {tool} = await setup(t);
    const action = tool.args.action.oneOf.find(item => item.properties.type.const === type);
    const guidance = `${action.description || ''} ${action.properties.value.description || ''}`;
    for (const term of terms) assert.match(guidance, term);
    assert.match(action.properties.value.description || '', /value/i);
  });
}

test('native read schema distinguishes control state from visible label text', async t => {
  const {tool} = await setup(t);
  const read = tool.args.action.oneOf.find(item => item.properties.type.const === 'read');
  const guidance = read.properties.attribute.description || '';
  for (const term of [/text.*default/i, /value.*input.*select/i, /checked.*radio.*checkbox/i,
    /true.*false/i, /aria-valuenow.*slider/i, /label/i]) assert.match(guidance, term);
});

test('model receipt preserves bounded associated selection and explicit state facts', async t => {
  const {root, tool}=await setup(t);
  const state={context:'associated_selection_state',selector:'#choice',tag:'input',type:'hidden',id:'choice',
    name:'deliveryChoice',label:'Delivery method',value:'E',selected_label:'Express',readonly:true,
    options:[{label:'Standard',value:'S'},{label:'Express',value:'E'}],
    association:{kind:'named_widget_enum',group_selector:'#delivery',group_label:'Delivery method',value_attribute:'data-optionvalue'}};
  const responder=setInterval(()=>{
    for (const file of fs.readdirSync(path.join(root,'requests')).filter(name=>name.endsWith('.json'))) {
      fs.writeFileSync(path.join(root,'responses',file),JSON.stringify({sequence:1,error:null,
        bounded_snapshot:'Delivery method Standard Express',
        documents:[{frame_id:'frame-1',document_id:'doc-1',url:'https://owned.test/'}],
        fields:[state,{context:'action_target',selector:'#monthly',tag:'button',label:'Monthly',state_attributes:{'data-active':''}}],
      }),{mode:0o600});
    }
  },5);
  try {
    const result=JSON.parse(await tool.execute({action:{type:'snapshot',step:'observe'}},{directory:root,abort:new AbortController().signal,ask:async()=>{}}));
    const observed=result.related_fields.find(field=>field.selector==='#choice');
    assert.equal(observed.selected_label,'Express');
    assert.deepEqual(observed.association,state.association);
    assert.deepEqual(observed.options,state.options);
    assert.deepEqual(result.related_fields.find(field=>field.selector==='#monthly').state_attributes,{'data-active':''});
    assert.deepEqual(result.documents,[{frame_id:'frame-1',document_id:'doc-1',url:'https://owned.test/'}]);
    assert.ok(JSON.stringify(result).length<8000);
  } finally {clearInterval(responder);}
});

function freezeTree(value) {
  if (value && typeof value === 'object') {
    Object.freeze(value);
    for (const item of Object.values(value)) freezeTree(item);
  }
  return value;
}

for (const mode of ['evidence','storage-fallback','unbounded-response']) test(`model source URLs are redacted without altering proof or business values: ${mode}`,async t=>{
  const {root}=await setup(t);
  const {modelReceipt}=await import('../browser-tools.mjs');
  if (mode === 'storage-fallback') fs.writeFileSync(path.join(root,'.tap-browser-observations'),'occupied');
  const source='https://MODEL_USER_SENTINEL:MODEL_PASSWORD_SENTINEL@owned.test/quote?access_token=ACCESS_TOKEN_SENTINEL&session=SESSION_SENTINEL&key=KEY_SENTINEL#FRAGMENT_SENTINEL';
  const business='https://catalog.test/product?color=blue#specifications';
  const observation=freezeTree({
    sequence:7,error:null,url:source,source_frame_url:source,
    ...(mode === 'unbounded-response' ? {} : {bounded_snapshot:'Quote ready',fields:[{tag:'a',selector:'#continue',href:source,value:'Leave unchanged'}]}),
    documents:[{frame_id:'frame-1',document_id:'document-1',url:source}],
    extracted:{name:'product_link',value:business,source_url:source,source_frame_url:source,source_frame_id:'frame-1',source_document_id:'document-1'},
    provenance:{quote:{source_url:source,source_frame_url:source,source_url_sha256:'unchanged-proof-hash'}},
    outputs:[{product_link:business,final_url:source,provenance:{product_link:{source_url:source}}}],
    result_data:{outputs:[{quote:'HK$23.45',url:business,final_url:source,
      provenance:{quote:{source_url:source,source_frame_url:source,source_url_sha256:'unchanged-proof-hash'}}}]},
  });
  const original=structuredClone(observation);
  const encoded=modelReceipt(observation,{type:'read',step:'price',selector:'#quote',name:'quote'},root);
  const result=JSON.parse(encoded);
  for(const sentinel of ['MODEL_USER_SENTINEL','MODEL_PASSWORD_SENTINEL','ACCESS_TOKEN_SENTINEL','SESSION_SENTINEL','KEY_SENTINEL','FRAGMENT_SENTINEL']) assert.ok(!encoded.includes(sentinel));
  assert.equal(result.url,'https://owned.test/quote');
  assert.equal(result.source_frame_url,'https://owned.test/quote');
  assert.deepEqual(result.documents,[{frame_id:'frame-1',document_id:'document-1',url:'https://owned.test/quote'}]);
  assert.equal(result.extracted.value,business);
  assert.equal(result.extracted.source_url,'https://owned.test/quote');
  assert.equal(result.extracted.source_frame_url,'https://owned.test/quote');
  assert.equal(result.extracted.source_document_id,'document-1');
  assert.equal(result.provenance.quote.source_url,'https://owned.test/quote');
  assert.equal(result.provenance.quote.source_url_sha256,'unchanged-proof-hash');
  assert.equal(result.outputs[0].final_url,'https://owned.test/quote');
  assert.equal(result.outputs[0].product_link,business);
  assert.equal(result.outputs[0].provenance.product_link.source_url,'https://owned.test/quote');
  assert.equal(result.result_data.outputs[0].quote,'HK$23.45');
  assert.equal(result.result_data.outputs[0].url,business);
  assert.equal(result.result_data.outputs[0].final_url,'https://owned.test/quote');
  assert.equal(result.result_data.outputs[0].provenance.quote.source_frame_url,'https://owned.test/quote');
  assert.deepEqual(observation,original,'raw proof receipt must remain byte-for-byte equivalent');
  if(mode === 'evidence') {
    const evidence=fs.readFileSync(result.evidence_file,'utf8');
    assert.ok(!evidence.includes('SENTINEL'));
    const saved=JSON.parse(evidence);
    assert.equal(saved.fields[0].href,'https://owned.test/quote');
    assert.equal(saved.fields[0].value,'Leave unchanged');
    assert.equal(saved.extracted.source_url,'https://owned.test/quote');
    assert.equal(saved.extracted.value,business);
    assert.equal(saved.provenance.quote.source_url_sha256,'unchanged-proof-hash');
  } else if(mode === 'storage-fallback') {
    assert.ok(result.evidence_storage_error);
    assert.equal(result.evidence_file,undefined);
  }
});

for(const source of [
  'data:text/html,OPAQUE_SOURCE_SENTINEL',
  'javascript:OPAQUE_SOURCE_SENTINEL',
  'https://[invalid?access_token=OPAQUE_SOURCE_SENTINEL',
  '/relative?access_token=OPAQUE_SOURCE_SENTINEL',
  'about:blank?access_token=OPAQUE_SOURCE_SENTINEL#OPAQUE_SOURCE_SENTINEL',
]) test(`unparseable or opaque source URL does not leak its payload: ${source.split(':')[0]}`,async t=>{
  const {root}=await setup(t);
  const {modelReceipt}=await import('../browser-tools.mjs');
  const observation=freezeTree({url:source,documents:[{frame_id:'f',document_id:'d',url:source}],extracted:{value:'Approved',source_url:source},bounded_snapshot:'',fields:[]});
  const result=JSON.parse(modelReceipt(observation,{type:'snapshot'},root));
  assert.ok(!JSON.stringify(result).includes('OPAQUE_SOURCE_SENTINEL'));
  assert.ok(!fs.readFileSync(result.evidence_file,'utf8').includes('OPAQUE_SOURCE_SENTINEL'));
  assert.equal(result.extracted.value,'Approved');
  assert.equal(observation.url,source);
});

test('compact identities survive two large selection states before calendar details',async t=>{
  const {root}=await setup(t);
  const {modelReceipt}=await import('../browser-tools.mjs');
  const state=index=>({context:'associated_selection_state',selector:`#state-${index}`,tag:'input',type:'hidden',
    id:`state-${index}`,name:`choice${index}`,label:`Choice ${index}`,value:'A',readonly:true,disabled:false,
    selected_label:'A'.repeat(100),options:Array.from({length:12},(_,i)=>({label:`${i} ${'associated choice '.repeat(6)}`,value:String(i)})),
    association:{kind:'named_widget_enum',group_selector:`#group-${index}`,group_label:'Associated choice '.repeat(7),value_attribute:'data-optionvalue'}});
  const fields=[{context:'action_target',selector:'#dob',id:'dob',tag:'input',role:null,label:'Date of birth',value:'',readonly:true,disabled:false},
    state(1),state(2),
    {context:'visible_control',selector:'#calendar-month',tag:'select',label:'Month',options:Array.from({length:12},(_,i)=>({label:`Month ${i+1}`,value:String(i+1)})),options_total:12,options_truncated:false},
    {context:'visible_control',selector:'#calendar-year',tag:'select',label:'Year',options:Array.from({length:30},(_,i)=>({label:String(1900+i),value:String(1900+i)})),options_total:127,options_truncated:true},
    {context:'popup_item',selector:'#day-11',tag:'button',label:'11'},
    {context:'visible_popup',selector:'#calendar',tag:'div',label:'Calendar',popup_text:'Calendar day '.repeat(300),popup_text_truncated:true}];
  const receipt={sequence:9,error:null,bounded_snapshot:'Background '.repeat(2000),fields,
    documents:Array.from({length:3},(_,i)=>({frame_id:`frame-${i}`,document_id:`document-${i}`,url:`https://owned.test/page-${i}`}))};
  const encoded=modelReceipt(receipt,{type:'click',selector:'#dob'},root);
  const result=JSON.parse(encoded);
  assert.ok(encoded.length<8000);
  assert.deepEqual(new Set(result.related_fields.map(field=>field.selector)),new Set(fields.map(field=>field.selector)));
  assert.equal(result.related_fields.find(field=>field.selector==='#dob').readonly,true);
  assert.equal(Object.hasOwn(result.related_fields.find(field=>field.selector==='#dob'),'role'),false,'null identity fields waste the shared budget');
  const year=result.related_fields.find(field=>field.selector==='#calendar-year');
  assert.equal(year.options_total,127);assert.equal(year.options_truncated,true);
  assert.ok(result.related_fields.some(field=>field.details_omitted),'model-budget omissions must be explicit');
  for (const index of [1,2]) assert.deepEqual(result.related_fields.find(field=>field.selector===`#state-${index}`).association,state(index).association);
  assert.deepEqual(result.documents,receipt.documents);
  assert.deepEqual(JSON.parse(fs.readFileSync(result.evidence_file,'utf8')).fields,fields);
});

test('native schema explains exact refs, scoped snapshots, current receipts and readonly dates',async t=>{
  const {tool}=await setup(t);
  const snapshot=tool.args.action.oneOf.find(item=>item.properties.type.const==='snapshot');
  const guidance=tool.description+' '+snapshot.properties.selector.description;
  for(const term of [/aria\/Label.*invalid/i,/aria-ref=Label.*invalid/i,/existing element/i,/not.*text search/i,
    /receipts.*observations/i,/readonly.*date.*open/i,/year.*month.*day/i,/sibling popup/i]) assert.match(guidance,term);
});

test('native select identities survive more popup items than the field limit',async t=>{
  const {root}=await setup(t);const {modelReceipt}=await import('../browser-tools.mjs');
  const result=JSON.parse(modelReceipt({error:null,bounded_snapshot:'Calendar',fields:[
    {context:'action_target',selector:'#dob',tag:'input',readonly:true},
    ...Array.from({length:30},(_,i)=>({context:'popup_item',selector:`#day-${i}`,tag:'button',label:String(i+1)})),
    {context:'popup_item',selector:'#month',tag:'select',label:'Month'},
    {context:'popup_item',selector:'#year',tag:'select',label:'Year'},
  ]},{type:'click',selector:'#dob'},root));
  assert.ok(result.related_fields.some(field=>field.selector==='#month'));
  assert.ok(result.related_fields.some(field=>field.selector==='#year'));
  assert.ok(result.related_fields_omitted>0);
});

test('an observed empty native select does not claim that options were truncated',async t=>{
  const {root}=await setup(t);const {modelReceipt}=await import('../browser-tools.mjs');
  const result=JSON.parse(modelReceipt({error:null,bounded_snapshot:'Empty choices',fields:[
    {context:'action_target',selector:'#empty',tag:'select',options:[],options_total:0,options_truncated:false},
  ]},{type:'snapshot',selector:'#empty'},root));
  assert.equal(result.related_fields[0].options_total,0);
  assert.equal(result.related_fields[0].options_truncated,false);
});

for (const [name,selector,admitted] of [
  ['1000-character boundary','#'+'x'.repeat(999),true],
  ['1001-character boundary','#'+'x'.repeat(1000),false],
  ['140-layer observed hierarchy',Array(140).fill('div:nth-of-type(1)').join(' > '),false],
]) test(`model receipt admits only schema-usable selectors: ${name}`,async t=>{
  const {root}=await setup(t);const {modelReceipt}=await import('../browser-tools.mjs');
  const {validateAction}=await import('../browserctl.mjs');
  const receipt={error:null,bounded_snapshot:'Deep notice',fields:[
    {context:'visible_popup',selector,tag:'div',role:'dialog',label:'Deep notice',popup_text:'Deep notice'},
  ]};
  const result=JSON.parse(modelReceipt(receipt,{type:'snapshot',step:'inspect'},root));
  if(admitted) {
    assert.equal(result.related_fields[0].selector,selector);
    assert.doesNotThrow(()=>validateAction({type:'snapshot',step:'inspect',selector:result.related_fields[0].selector}));
    assert.equal(result.related_fields_omitted,undefined);
  } else {
    assert.equal(result.related_fields?.length || 0,0,'never advertise a selector that the next browser call cannot accept');
    assert.equal(result.related_fields_omitted,1);
    assert.match(result.observation_detail,/evidence_file/);
  }
  assert.equal(JSON.parse(fs.readFileSync(result.evidence_file,'utf8')).fields[0].selector,selector,'full evidence retains the exact unshortened selector');
  assert.ok(JSON.stringify(result).length<8000);
});

test('model receipt prioritizes deduplicated safe scope identities and a requested scope snapshot',async t=>{
  const {root}=await setup(t);const {modelReceipt}=await import('../browser-tools.mjs');
  const scopes=[{selector:'#calendar',tag:'div',role:null,relation:'ancestor_scope'},{selector:'#calendar-table',tag:'table',role:null,relation:'descendant_table_scope'}];
  const snapshot='Current month\n'+'day detail '.repeat(300)+'\n- button "11" [ref=e31]';
  const observation={error:null,bounded_snapshot:snapshot,fields:[
    {context:'action_target',selector:'#calendar-table',tag:'table',label:'',scope_candidates:scopes},
    ...Array.from({length:6},(_,i)=>({context:'visible_control',selector:`#select-${i}`,tag:'select',label:'Choices',scope_candidates:scopes,
      options:Array.from({length:200},(_,j)=>({label:`Very long option label ${j}`,value:String(j)})),options_total:200,options_truncated:false})),
  ]};
  const result=JSON.parse(modelReceipt(observation,{type:'snapshot',selector:'#calendar-table'},root));
  assert.equal(result.bounded_snapshot,snapshot,'requested scope evidence precedes unrelated option details');
  assert.equal(result.related_fields[0].selector,'#calendar-table');
  assert.deepEqual(result.scope_candidates,[scopes[1],scopes[0]]);
  assert.ok(JSON.stringify(result).length<8000);
  assert.match(result.observation_detail,/observation.*not.*binding/i);
});

test('advertising-frame document metadata cannot crowd out the requested observation',async t=>{
  const {root}=await setup(t);const {modelReceipt}=await import('../browser-tools.mjs');
  const documents=Array.from({length:40},(_,i)=>({frame_id:`frame-${i}`,document_id:`document-${i}`,url:`https://owned.test/${String(i).padStart(2,'0')}${'advertisement/'.repeat(20)}`}));
  const observation={error:null,bounded_snapshot:'- button "11" [ref=e31]',documents,fields:[{context:'action_target',selector:'#calendar',tag:'table',scope_candidates:[
    {selector:'#'+'x'.repeat(999),tag:'div',role:null,relation:'ancestor_scope'},
    {selector:'#day-table',tag:'table',role:null,relation:'descendant_table_scope'},
  ]}]};
  const result=JSON.parse(modelReceipt(observation,{type:'snapshot',selector:'#calendar'},root));
  assert.equal(result.bounded_snapshot,observation.bounded_snapshot);
  assert.ok(result.documents_omitted>0);assert.ok(JSON.stringify(result).length<8000);
  assert.equal(result.scope_candidates.length,2,'display-only documents cannot crowd out the returned observation scopes');
  assert.deepEqual(JSON.parse(fs.readFileSync(result.evidence_file,'utf8')).documents,documents,'document proof/evidence must remain complete');
});

for(const length of [1000,1001]) test(`scope selector admission matches the ${length}-character action boundary`,async t=>{
  const {root}=await setup(t);const {modelReceipt}=await import('../browser-tools.mjs');
  const scope={selector:'#'+'x'.repeat(length-1),tag:'div',role:null,relation:'ancestor_scope'};
  const observation={error:null,bounded_snapshot:'Choices',fields:[{context:'action_target',selector:'#month',tag:'select',scope_candidates:[scope]}]};
  const result=JSON.parse(modelReceipt(observation,{type:'click',selector:'#month'},root));
  assert.equal(result.scope_candidates?.length || 0,length===1000?1:0);
  if(length===1000)assert.equal(result.scope_candidates[0].selector,scope.selector);
  else assert.ok(result.scope_candidates_omitted>0);
  assert.deepEqual(JSON.parse(fs.readFileSync(result.evidence_file,'utf8')).fields[0].scope_candidates,[scope]);
});

test('an unusable generated target selector cannot reserve away an aria-ref scoped snapshot',async t=>{
  const {root}=await setup(t);const {modelReceipt}=await import('../browser-tools.mjs');
  const observation={error:null,bounded_snapshot:'- button "Current day 11" [ref=e31]',documents:[{frame_id:'frame-1',document_id:'document-1',url:'https://owned.test/'}],
    fields:[{context:'action_target',selector:Array(450).fill('div:nth-of-type(1)').join(' > '),tag:'div',label:'Current days'}]};
  assert.ok(observation.fields[0].selector.length>8000);
  const result=JSON.parse(modelReceipt(observation,{type:'snapshot',selector:'aria-ref=e1'},root));
  assert.equal(result.bounded_snapshot,observation.bounded_snapshot);
  assert.deepEqual(result.documents,observation.documents);
  assert.equal(result.related_fields_omitted,1);
  assert.deepEqual(JSON.parse(fs.readFileSync(result.evidence_file,'utf8')),observation);
});

test('only discovery navigation and snapshot may omit a planned step', async t => {
  const {root, tool} = await setup(t);
  for (const type of ['navigate','snapshot']) {
    const schema = tool.args.action.oneOf.find(item => item.properties.type.const === type);
    assert.ok(!schema.required.includes('step'));
  }
  await assert.rejects(tool.execute({action:{type:'click',selector:'#go'}}, {
    directory:root,abort:new AbortController().signal,ask:async()=>{},
  }), /step required/);
  const action={type:'snapshot'};
  const pending=tool.execute({action},{directory:root,abort:new AbortController().signal,ask:async()=>{}});
  let file;
  for(let i=0;i<100&&!file;i++) {
    file=fs.readdirSync(path.join(root,'requests')).find(name=>name.endsWith('.json'));
    if(!file) await new Promise(resolve=>setTimeout(resolve,10));
  }
  assert.ok(file);
  fs.writeFileSync(path.join(root,'responses',file),JSON.stringify({error:'server policy decides discovery'}),{mode:0o600});
  assert.equal(JSON.parse(await pending).error,'server policy decides discovery');
});

const safeDiscoveryField = (i, label=`Choice ${i}`) => ({selector:`#old-${i}`,label:'PRIVATE_OLD_LABEL',value:'PRIVATE_VALUE',href:'https://private.test',
  discovery_identity:{selector:`html > body > button:nth-of-type(${i+1})`,tag:'button',role:'button',label}});

test('discovery catalog contains only producer identities in source order with four-line pages', async t=>{
  const {root}=await setup(t); const {modelReceipt}=await import('../browser-tools.mjs');
  const fields=Array.from({length:110},(_,i)=>safeDiscoveryField(i));
  const encoded=modelReceipt({status:'completed',sequence:9,error:null,bounded_snapshot:'Page',fields},{type:'snapshot'},root);
  const result=JSON.parse(encoded), catalog=result.control_catalog;
  assert.equal(catalog.observed_fields_count,110); assert.equal(catalog.eligible_count,100);
  assert.equal(catalog.catalog_count,100); assert.equal(catalog.line_count,100); assert.equal(catalog.omitted,10);
  assert.equal(catalog.page_size,4); assert.equal(catalog.source_limit,100); assert.equal(catalog.source_bounded,true);
  const raw=fs.readFileSync(catalog.file,'utf8'), lines=raw.trimEnd().split('\n');
  assert.equal(lines.length,100); assert.ok(Buffer.byteLength(raw)<=100*1024);
  assert.equal(fs.statSync(catalog.file).mode & 0o077,0);
  assert.deepEqual(lines.map(line=>JSON.parse(line).selector),fields.slice(0,100).map(field=>field.discovery_identity.selector));
  assert.ok(!raw.includes('PRIVATE')); assert.ok(!raw.includes('href'));
  assert.ok(result.discovery_candidates.length<=3); assert.ok(Buffer.byteLength(encoded)<=8000);
});

test('catalog byte limits preserve complete unicode JSON lines and never shorten selectors',async t=>{
  const {root}=await setup(t); const {modelReceipt}=await import('../browser-tools.mjs');
  const fields=[safeDiscoveryField(0,'漢😀"\\'.repeat(400)),safeDiscoveryField(1),safeDiscoveryField(2)];
  fields[1].discovery_identity.selector='漢'.repeat(500);
  fields[2].discovery_identity.selector='[data-x="'+ '\\'.repeat(600)+'"]';
  const encoded=modelReceipt({error:null,bounded_snapshot:'😀'.repeat(6000),fields},{type:'snapshot'},root);
  const result=JSON.parse(encoded),lines=fs.readFileSync(result.control_catalog.file,'utf8').trimEnd().split('\n');
  assert.equal(lines.length,1); assert.ok(lines.every(line=>Buffer.byteLength(line+'\n')<=1024));
  const first=JSON.parse(lines[0]); assert.equal(first.selector,fields[0].discovery_identity.selector);
  assert.equal(first.label_truncated,true); assert.ok(Buffer.byteLength(encoded)<=8000);
});

test('legacy fields never become discovery identities and contextual targets retain priority',async t=>{
  const {root}=await setup(t); const {modelReceipt}=await import('../browser-tools.mjs');
  const fields=[{tag:'a',selector:'#legacy',label:'PRIVATE_LEGACY'},...Array.from({length:20},(_,i)=>safeDiscoveryField(i)),
    {context:'action_target',selector:'#current',tag:'input',label:'Date',readonly:true,scope_candidates:[{selector:'#calendar',tag:'table',relation:'labelled_popup'}]}];
  const result=JSON.parse(modelReceipt({error:null,bounded_snapshot:'Calendar',fields},{type:'click',selector:'#current'},root));
  const raw=fs.readFileSync(result.control_catalog.file,'utf8');
  assert.ok(!raw.includes('PRIVATE_LEGACY')); assert.equal(result.control_catalog.catalog_count,20);
  assert.equal(result.related_fields[0].selector,'#current'); assert.equal(result.scope_candidates[0].selector,'#calendar');
});

for(const failure of ['blocked','symlink']) test(`storage ${failure} cannot leak giant fields or change known outcome`,async t=>{
  const {root}=await setup(t);const {modelReceipt}=await import('../browser-tools.mjs');
  const folder=path.join(root,'.tap-browser-observations');
  if(failure==='blocked') fs.writeFileSync(folder,'occupied'); else fs.symlinkSync(root,folder);
  const encoded=modelReceipt({status:'completed',error:null,sequence:3,bounded_snapshot:'漢'.repeat(9000),fields:Array.from({length:100},(_,i)=>safeDiscoveryField(i)),extracted:{name:'quote',value:'😀'.repeat(5000)}},{type:'read'},root);
  const result=JSON.parse(encoded); assert.ok(Buffer.byteLength(encoded)<=8000);
  assert.equal(result.status,'completed');assert.equal(result.error,null);assert.equal(result.sequence,3);
  assert.ok(result.evidence_storage_error);assert.equal(result.control_catalog.unavailable,true);
  assert.equal(result.control_catalog.file,undefined);assert.equal(result.fields,undefined);
  assert.equal(result.read_result_omitted,true);assert.ok(!encoded.includes('PRIVATE'));
});

test('oversized exact read is kept intact in evidence and model status uses bounded pointer',async t=>{
  const {root}=await setup(t);const {modelReceipt}=await import('../browser-tools.mjs');
  const receipt={status:'completed',error:null,sequence:7,extracted:{name:'quote',value:'😀'.repeat(5000)}};
  const encoded=modelReceipt(receipt,{type:'read'},root), result=JSON.parse(encoded);
  assert.ok(Buffer.byteLength(encoded)<=8000);assert.equal(result.status,'completed');assert.equal(result.read_result_omitted,true);
  assert.deepEqual(JSON.parse(fs.readFileSync(result.evidence_file,'utf8')),receipt);
});

test('long quote page preserves generic candidate source order without CTA promotion',async t=>{
  const {root}=await setup(t);const {modelReceipt}=await import('../browser-tools.mjs');
  const fields=['Get in touch','Learn about cover','Policy information','Terms','Buy Now'].map((label,i)=>safeDiscoveryField(i,label));
  const snapshot='Get in touch '+ 'Neutral informational content '.repeat(600)+' Buy Now';
  const result=JSON.parse(modelReceipt({sequence:10,error:null,bounded_snapshot:snapshot,fields},{type:'snapshot'},root));
  assert.ok(result.snapshot_truncated);assert.ok(!result.bounded_snapshot.includes('Buy Now'));
  const rows=fs.readFileSync(result.control_catalog.file,'utf8').trimEnd().split('\n').map(line=>JSON.parse(line));
  assert.deepEqual(rows.map(row=>row.label),fields.map(field=>field.discovery_identity.label));
  assert.equal(rows[4].label,'Buy Now');assert.equal(Boolean(result.discovery_candidates?.some(row=>row.label==='Buy Now')),false);
});

test('catalog-only write failure advertises unavailable without old-field fallback',async t=>{
  const {root}=await setup(t);const {modelReceipt}=await import('../browser-tools.mjs');
  const write=fs.writeFileSync;
  t.mock.method(fs,'writeFileSync',(filename,...args)=>{
    if(String(filename).endsWith('.jsonl')) throw new Error('private storage reason');
    return write(filename,...args);
  });
  const result=JSON.parse(modelReceipt({status:'completed',error:null,bounded_snapshot:'Page',fields:[safeDiscoveryField(0)]},{type:'snapshot'},root));
  assert.ok(fs.existsSync(result.evidence_file));assert.equal(result.control_catalog.unavailable,true);
  assert.equal(result.control_catalog.file,undefined);assert.equal(result.discovery_candidates,undefined);
  assert.ok(!JSON.stringify(result).includes('PRIVATE'));assert.ok(!JSON.stringify(result).includes('private storage reason'));
});


test('small evidence failure also omits raw fields and marks unavailable catalog',async t=>{
  const {root}=await setup(t);const {modelReceipt}=await import('../browser-tools.mjs');
  fs.writeFileSync(path.join(root,'.tap-browser-observations'),'occupied');
  const result=JSON.parse(modelReceipt({status:'failed',error:'Known failure',sequence:8,bounded_snapshot:'Page',fields:[safeDiscoveryField(0)]},{type:'click'},root));
  assert.equal(result.fields,undefined);assert.equal(result.fields_omitted,true);
  assert.equal(result.control_catalog.observation_sequence,8);
  assert.equal(result.control_catalog.unavailable,true);assert.equal(result.status,'failed');
  assert.ok(!JSON.stringify(result).includes('PRIVATE'));
});

test('untrusted oversized metadata cannot escape final UTF8 cap',async t=>{
  const {root}=await setup(t);const {modelReceipt}=await import('../browser-tools.mjs');
  const receipt={status:'unknown',error:'failed',control_catalog:{anything:'漢'.repeat(12000),file:'x'.repeat(12000)},extracted:{value:'😀'.repeat(5000)}};
  const encoded=modelReceipt(receipt,{type:'read'},root);
  assert.ok(Buffer.byteLength(encoded)<=8000);assert.equal(JSON.parse(encoded).status,'unknown');
});


test('catalog guidance distinguishes static file discovery from browser progress',async t=>{
  const {root}=await setup(t);const {modelReceipt}=await import('../browser-tools.mjs');
  const result=JSON.parse(modelReceipt({sequence:1,error:null,bounded_snapshot:'Page',fields:[safeDiscoveryField(0)]},{type:'snapshot'},root));
  assert.match(result.discovery_detail,/Read\/grep needed records/);
  assert.match(result.discovery_detail,/not every page turn by turn/);
  assert.match(result.discovery_detail,/not browser progress/);
  assert.match(result.discovery_detail,/no-progress model-call limit still applies/);
  assert.equal(result.control_catalog.page_size,4);
});

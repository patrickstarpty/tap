import assert from 'node:assert/strict';
import test from 'node:test';
import {chromium} from '@playwright/test';
const module = await import('../live-control.mjs').catch(() => ({}));
test('live control exports its production dispatcher', () => assert.equal(typeof module.createLiveControl, 'function'));
async function fixture(t, now, beforeAction) {
  const browser = await chromium.launch({headless:true});
  t.after(() => browser.close());
  const page = await browser.newPage({viewport:{width:800,height:600}});
  await page.setContent('<label>Name<input data-testid="name"></label><button data-testid="submit" onclick="document.body.dataset.count=Number(document.body.dataset.count||0)+1;this.remove()">Submit</button><input type="password" id="password"><div style="height:1500px">End</div>');
  let sequence=0;
  const control=module.createLiveControl({page, now, observe:async (action,actuate)=>{
    if(beforeAction) await beforeAction(page,action);
    if(actuate) { await actuate(); return {sequence:++sequence,error:null}; }
    if(action.type==='click') await page.locator(action.selector).click();
    if(action.type==='fill') await page.locator(action.selector).fill(action.value);
    if(action.type==='scroll') await page.mouse.wheel(action.delta_x,action.delta_y);
    return {sequence:++sequence,error:null};
  }});
  await control.dispatch({type:'control_sync',controller_kind:'human',control_epoch:1});
  const frame=await control.dispatch({type:'live_frame'});
  const request={actor_expires_at:new Date(Date.now()+60000).toISOString(),actor_kind:'human',control_epoch:1,frame_sequence:frame.frame_sequence,viewport:frame.viewport};
  return {page,control,frame,request};
}
test('captures pre-navigation identity and rejects duplicate old-frame clicks',async t=>{
  const {page,control,request}=await fixture(t);
  const prepared=await control.dispatch({type:'human_preflight',action:{...request,type:'click',selector:'[data-testid="submit"]'}});
  assert.deepEqual(prepared.target,{kind:'test_id',value:'submit',fallback:false});
  await control.dispatch({...request,type:'click',...prepared});
  assert.equal(await page.locator('body').getAttribute('data-count'),'1');
  await assert.rejects(control.dispatch({...request,type:'click',...prepared}),/stale frame/);
  assert.equal(await page.locator('body').getAttribute('data-count'),'1');
});
test('password preflight refuses before input is admitted',async t=>{
  const {page,control,request}=await fixture(t);
  await assert.rejects(control.dispatch({type:'human_preflight',action:{...request,type:'fill',selector:'#password'}}),/sensitive/);
  assert.equal(await page.locator('#password').inputValue(),'');
});
test('handback fences human requests and untagged writes with fresh observation',async t=>{
  const {page,control,request}=await fixture(t);
  const returned=await control.dispatch({type:'control_sync',controller_kind:'agent',control_epoch:2});
  assert.ok(returned.sequence>1);
  await assert.rejects(control.dispatch({...request,type:'fill',selector:'[data-testid="name"]',value:'synthetic'}),/control/);
  await assert.rejects(control.dispatch({type:'fill',selector:'[data-testid="name"]',value:'synthetic'}),/control/);
  assert.equal(await page.locator('[data-testid="name"]').inputValue(),'');
});
test('frames are bounded ephemeral and reject viewport mismatch',async t=>{
  const {control,frame,request}=await fixture(t);
  assert.equal(frame.media_type,'image/jpeg');
  assert.ok(Buffer.from(frame.image_base64,'base64').length<500000);
  assert.equal(Object.hasOwn(frame,'evidence_ref'),false);
  await assert.rejects(control.dispatch({type:'human_preflight',action:{...request,type:'click',selector:'[data-testid="submit"]',viewport:{width:400,height:300}}}),/viewport/);
});
test('agent value-free preflight rejects password and binds safe target',async t=>{
  const {page,control}=await fixture(t);
  await control.dispatch({type:'control_sync',controller_kind:'agent',control_epoch:2});
  const actor={actor_kind:'agent',control_epoch:2};
  await assert.rejects(control.dispatch({...actor,type:'preflight',operation:'fill',selector:'#password'}),/sensitive/);
  await assert.rejects(control.dispatch({...actor,type:'preflight',operation:'fill',selector:'[data-testid="name"]',value:'must-not-forward'}),/value-free/);
  const result=await control.dispatch({...actor,type:'preflight',operation:'fill',selector:'[data-testid="name"]'});
  assert.equal(result.extracted.preflight.safe,true);
  assert.equal(result.extracted.preflight.target.value,'name');
  await page.locator('[data-testid="name"]').evaluate(n=>n.type='password');
  await assert.rejects(control.dispatch({...actor,type:'fill',selector:'[data-testid="name"]',value:'synthetic'}),/sensitive/);
});
test('control transition runs behind in-flight action and before new stale action',async t=>{
  const {control,request}=await fixture(t);
  const bound=await control.dispatch({type:'human_preflight',action:{...request,type:'click',selector:'[data-testid="submit"]'}});
  const results=await Promise.allSettled([
    control.dispatch({...request,...bound,type:'click'}),
    control.dispatch({type:'control_sync',controller_kind:'agent',control_epoch:2}),
    control.dispatch({...request,...bound,type:'click'}),
  ]);
  assert.deepEqual(results.map(r=>r.status),['fulfilled','fulfilled','rejected']);
});

test('expired or DOM-changed frames cannot dispatch inputs',async t=>{
  let clock=1000;
  const {page,control,request}=await fixture(t,()=>clock);
  clock=6001;
  await assert.rejects(control.dispatch({type:'human_preflight',action:{...request,type:'click',selector:'[data-testid="submit"]'}}),/stale frame/);
  const next=await control.dispatch({type:'live_frame'});
  await page.locator('[data-testid="submit"]').evaluate(n=>n.textContent='Changed');
  await assert.rejects(control.dispatch({type:'human_preflight',action:{...request,frame_sequence:next.frame_sequence,type:'click',selector:'[data-testid="submit"]'}}),/stale frame/);
  assert.equal(await page.locator('body').getAttribute('data-count'),null);
});
test('coordinate click records the semantic button before removing it',async t=>{
  const {page,control,request}=await fixture(t);
  const box=await page.locator('[data-testid="submit"]').boundingBox();
  const result=await control.dispatch({...request,type:'click',x:box.x+box.width/2,y:box.y+box.height/2});
  assert.equal(result.extracted.control_receipt.target.value,'submit');
  assert.equal(await page.locator('body').getAttribute('data-count'),'1');
});
test('frame-qualified human targets are refused rather than rewritten into main-frame CSS',async t=>{
  const {page,control,request}=await fixture(t);
  await page.locator('body').evaluate(n=>n.insertAdjacentHTML('beforeend','<iframe id="other"></iframe>'));
  await page.frames()[1].setContent('<input data-testid="name">');
  const frame=await control.dispatch({type:'live_frame'});
  await assert.rejects(control.dispatch({type:'human_preflight',action:{...request,frame_sequence:frame.frame_sequence,type:'fill',selector:'#other >> internal:control=enter-frame >> input'}}),/frame target/);
});
test('coordinate fill preflight screens the actual field and writes only that target',async t=>{
  const {page,control,request}=await fixture(t);
  const box=await page.locator('[data-testid="name"]').boundingBox();
  const location={x:box.x+box.width/2,y:box.y+box.height/2};
  const proof=await control.dispatch({...request,...location,type:'preflight',operation:'fill'});
  assert.equal(proof.extracted.preflight.target.value,'name');
  const result=await control.dispatch({...request,...location,type:'fill',value:'Synthetic Person'});
  assert.equal(result.extracted.control_receipt.args.value,'Synthetic Person');
  assert.equal(await page.locator('[data-testid="name"]').inputValue(),'Synthetic Person');
});
test('a newer unchanged frame does not invalidate the frame the user clicked',async t=>{
  const {page,control,request}=await fixture(t);
  await control.dispatch({type:'live_frame'});
  const result=await control.dispatch({...request,type:'click',selector:'[data-testid="submit"]'});
  assert.equal(result.error,null);
  assert.equal(await page.locator('body').getAttribute('data-count'),'1');
});

for(const mutation of ['type','replace']) test(`atomic fill rejects ${mutation} after semantic binding`,async t=>{
  const {page,control,request}=await fixture(t,undefined,async (page,action)=>{
    if(action.type==='fill') await page.locator('[data-testid="name"]').evaluate((n,mutation)=>{
      if(mutation==='type') n.type='password';
      else n.outerHTML='<input data-testid="name" type="password">';
    },mutation);
  });
  await assert.rejects(control.dispatch({...request,type:'fill',selector:'[data-testid="name"]',value:'NEVER_PERSIST_MARKER'}));
  assert.equal(await page.locator('[data-testid="name"]').inputValue(),'');
});
test('expiry after target binding prevents actual write',async t=>{
  const {page,control,request}=await fixture(t,undefined,async (_page,action)=>{
    if(action.type==='fill') await new Promise(resolve=>setTimeout(resolve,150));
  });
  await assert.rejects(control.dispatch({...request,actor_expires_at:new Date(Date.now()+100).toISOString(),type:'fill',selector:'[data-testid="name"]',value:'EXPIRED_MARKER'}),/expir/);
  assert.equal(await page.locator('[data-testid="name"]').inputValue(),'');
});
test('synchronous sensitivity change during input prevents success value projection',async t=>{
  const {page,control,request}=await fixture(t);
  await page.locator('[data-testid="name"]').evaluate(n=>n.addEventListener('input',()=>n.type='password'));
  const frame=await control.dispatch({type:'live_frame'});
  await assert.rejects(control.dispatch({...request,frame_sequence:frame.frame_sequence,type:'fill',selector:'[data-testid="name"]',value:'POSTWRITE_MARKER'}),/outcome unknown/);
});
test('failed controlled write cannot resume or finalize the same runtime',async t=>{
  const {page,control,request}=await fixture(t);
  await page.locator('[data-testid="name"]').evaluate(n=>n.addEventListener('input',()=>n.type='password'));
  const frame=await control.dispatch({type:'live_frame'});
  await assert.rejects(control.dispatch({...request,frame_sequence:frame.frame_sequence,type:'fill',selector:'[data-testid="name"]',value:'UNSAFE_MARKER'}));
  await assert.rejects(control.dispatch({type:'control_sync',controller_kind:'agent',control_epoch:2}),/blocked/);
  await assert.rejects(control.dispatch({...request,type:'finish'}));
});
test('human native select records actual option value rather than a display label',async t=>{
  const {page,control,request}=await fixture(t);
  await page.setContent('<label>Plan<select data-testid="plan"><option value="basic">Basic</option><option value="pro">Professional</option></select></label>');
  const frame=await control.dispatch({type:'live_frame'});
  const result=await control.dispatch({...request,frame_sequence:frame.frame_sequence,type:'select',selector:'select',value:'Professional'});
  assert.equal(await page.locator('select').inputValue(),'pro');
  assert.deepEqual(result.extracted.control_receipt.args,{value:'pro'});
  assert.equal(result.extracted.control_receipt.target.value,'plan');
});
test('disabled option group cannot be selected by a human',async t=>{
  const {page,control,request}=await fixture(t);
  await page.setContent('<select><option value="safe">Safe</option><optgroup label="Blocked" disabled><option value="blocked">Blocked</option></optgroup></select>');
  const frame=await control.dispatch({type:'live_frame'});
  await assert.rejects(control.dispatch({...request,frame_sequence:frame.frame_sequence,type:'select',selector:'select',value:'blocked'}),/missing or ambiguous/);
  assert.equal(await page.locator('select').inputValue(),'safe');
});
test('explicit human navigation has a targetless typed receipt and invalidates old frames',async t=>{
  const {page,control,request}=await fixture(t);
  await page.route('https://fixture.test/**',route=>route.fulfill({body:'<h1>Entry</h1>',contentType:'text/html'}));
  const result=await control.dispatch({...request,type:'navigate',url:'https://fixture.test/entry'});
  assert.equal(page.url(),'https://fixture.test/entry');
  assert.deepEqual(result.extracted.control_receipt.args,{url:'https://fixture.test/entry'});
  assert.equal(result.extracted.control_receipt.target,null);
  assert.equal(result.extracted.control_receipt.pre_observation_sequence,1);
  await assert.rejects(control.dispatch({...request,type:'navigate',url:'https://fixture.test/entry'}),/stale frame/);
});
test('human navigation expired during pre-dispatch await never initiates',async t=>{
  let initiated=0;
  const {page,control,request}=await fixture(t,undefined,async(_page,action)=>{
    if(action.type==='navigate') await new Promise(resolve=>setTimeout(resolve,150));
  });
  await page.route('https://fixture.test/**',route=>{initiated++;return route.fulfill({body:'Done'});});
  await assert.rejects(control.dispatch({...request,actor_expires_at:new Date(Date.now()+100).toISOString(),type:'navigate',url:'https://fixture.test/entry'}),/expired/);
  assert.equal(initiated,0);
  assert.equal(page.url(),'about:blank');
});
for(const tag of ['select','textarea']) test(`marked ${tag} cannot pass sensitive input preflight`,async t=>{
  const {page,control,request}=await fixture(t);
  await page.setContent(tag==='select'?'<select autocomplete="cc-number"><option value="safe">Safe</option></select>':'<textarea autocomplete="one-time-code"></textarea>');
  const frame=await control.dispatch({type:'live_frame'});
  await assert.rejects(control.dispatch({...request,frame_sequence:frame.frame_sequence,type:'preflight',operation:tag==='select'?'select':'fill',selector:tag}),/sensitive/);
});
for(const marker of ['data-secret','data-sensitive']) test(`${marker} is rejected and masked on ordinary fields`,async t=>{
  const {page,control,request}=await fixture(t);
  await page.setContent(`<label>Protected<input ${marker} value="FIRST_SECRET"></label>`);
  const first=await control.dispatch({type:'live_frame'});
  await assert.rejects(control.dispatch({...request,frame_sequence:first.frame_sequence,type:'preflight',operation:'fill',selector:'input'}),/sensitive/);
  await page.locator('input').evaluate(n=>n.value='DIFFERENT_SECRET');
  const second=await control.dispatch({type:'live_frame'});
  assert.equal(first.image_base64,second.image_base64);
});
test('handoff waits for one in-flight navigation and never reinitiates it',async t=>{
  const {page,control,request}=await fixture(t);
  let release,started,initiated=0,handedBack=false;
  const ready=new Promise(resolve=>started=resolve);
  const response=new Promise(resolve=>release=resolve);
  await page.route('https://fixture.test/**',async route=>{initiated++;started();await response;await route.fulfill({body:'<h1>Entry</h1>'});});
  const navigation=control.dispatch({...request,type:'navigate',url:'https://fixture.test/entry'});
  await ready;
  const handback=control.dispatch({type:'control_sync',controller_kind:'agent',control_epoch:2}).then(result=>{handedBack=true;return result;});
  await new Promise(resolve=>setTimeout(resolve,20));
  assert.equal(handedBack,false);
  release();
  const result=await navigation;
  assert.equal(result.extracted.control_receipt.args.url,'https://fixture.test/entry');
  await handback;
  assert.equal(initiated,1);
});

for(const event of ['focus','click']) test(`readonly click opens a ${event} datepicker without editing the field`,async t=>{
  const {page,control,request}=await fixture(t);
  await page.setContent('<label>Date of birth<input id="dob" data-testid="dob" readonly value="01/01/1990"></label><div id="calendar" hidden>Calendar</div>');
  await page.locator('#dob').evaluate((node,event)=>{
    node.addEventListener(event,()=>document.querySelector('#calendar').hidden=false);
    for(const type of ['focus','click','input','change']) node.addEventListener(type,()=>document.body.dataset[type]=String(Number(document.body.dataset[type]||0)+1));
  },event);
  const frame=await control.dispatch({type:'live_frame'});
  const result=await control.dispatch({...request,frame_sequence:frame.frame_sequence,type:'click',selector:'#dob'});
  assert.equal(result.error,null);
  assert.equal(result.extracted.control_receipt.target.value,'dob');
  assert.equal(await page.locator('#calendar').isVisible(),true);
  assert.equal(await page.locator('#dob').inputValue(),'01/01/1990');
  assert.deepEqual(await page.locator('body').evaluate(node=>({...node.dataset})),{focus:'1',click:'1'});
});

for(const tag of ['input','textarea']) test(`readonly ${tag} fill remains forbidden`,async t=>{
  const {page,control,request}=await fixture(t);
  await page.setContent(tag==='input'?'<input readonly value="unchanged">':'<textarea readonly>unchanged</textarea>');
  const frame=await control.dispatch({type:'live_frame'});
  await assert.rejects(control.dispatch({...request,frame_sequence:frame.frame_sequence,type:'fill',selector:tag,value:'forbidden'}),/not actionable/);
  assert.equal(await page.locator(tag).inputValue(),'unchanged');
});

for(const marker of ['type="password"','data-secret','data-sensitive']) test(`readonly click still rejects ${marker}`,async t=>{
  const {page,control,request}=await fixture(t);
  await page.setContent(`<input id="protected" readonly ${marker} onfocus="document.body.dataset.focused='yes'" onclick="document.body.dataset.clicked='yes'">`);
  const frame=await control.dispatch({type:'live_frame'});
  await assert.rejects(control.dispatch({...request,frame_sequence:frame.frame_sequence,type:'click',selector:'#protected'}),/sensitive/);
  assert.deepEqual(await page.locator('body').evaluate(node=>({...node.dataset})),{});
});

for(const mutation of ['replace','password','checkbox','secret','disabled','inert','hidden','obscured','expired']) test(`focus handler ${mutation} fails closed before the bound click`,async t=>{
  const {page,control,request}=await fixture(t);
  await page.setContent('<input id="target" value="unchanged" onclick="document.body.dataset.clicked=\'yes\'">');
  await page.locator('#target').evaluate((node,mutation)=>{
    node.addEventListener('focus',()=>{
      document.body.dataset.focused='yes';
      if(mutation==='replace') node.outerHTML=node.outerHTML;
      if(mutation==='password'||mutation==='checkbox') node.type=mutation;
      if(mutation==='secret') node.setAttribute('data-secret','');
      if(mutation==='disabled') node.disabled=true;
      if(mutation==='inert') node.inert=true;
      if(mutation==='hidden') node.style.visibility='hidden';
      if(mutation==='obscured') document.body.insertAdjacentHTML('beforeend','<div style="position:fixed;inset:0"></div>');
    });
  },mutation);
  const frame=await control.dispatch({type:'live_frame'});
  const expires=Date.now()+300;
  if(mutation==='expired') {
    // Event listeners leave the issued frame unchanged. A synchronous focus
    // handler then outlives the capability before the click can dispatch.
    await page.locator('#target').evaluate((node,expires)=>{
      node.addEventListener('focus',()=>{while(Date.now()<expires) {}}, {capture:true});
    },expires);
  }
  await assert.rejects(control.dispatch({...request,frame_sequence:frame.frame_sequence,...(mutation==='expired'?{actor_expires_at:new Date(expires).toISOString()}:{}),type:'click',selector:'#target'}),/bound target changed|sensitive|type changed|not actionable|obscured|expired/);
  assert.equal(await page.locator('body').getAttribute('data-focused'),'yes');
  assert.equal(await page.locator('body').getAttribute('data-clicked'),null);
  assert.equal(await page.locator('#target').inputValue(),'unchanged');
  await assert.rejects(control.dispatch({type:'control_sync',controller_kind:'agent',control_epoch:2}),/blocked/);
});

async function nestedPicker(t, tag='div') {
  const result=await fixture(t);
  await result.page.setContent(`<input id="age" readonly value="25">
    <div id="options" role="listbox" style="height:120px;width:220px;overflow:auto">
      ${Array.from({length:17},(_,index)=>index+19).map(age=>`<${tag} role="option" data-testid="age-${age}" style="display:block;height:32px;width:180px;flex-shrink:0" onclick="document.querySelector('#age').value='${age}';document.body.dataset.clicked=String(Number(document.body.dataset.clicked||0)+1)">${age}</${tag}>`).join('')}
    </div>`);
  return result;
}

test('click reveals the exact option in its nested scroller and acts only once',async t=>{
  const {page,control,request}=await nestedPicker(t);
  const target=page.getByTestId('age-30');
  assert.equal(await target.evaluate(node=>{const rect=node.getBoundingClientRect();return node.contains(document.elementFromPoint(rect.x+rect.width/2,rect.y+rect.height/2));}),false);
  const frame=await control.dispatch({type:'live_frame'});
  const action={...request,frame_sequence:frame.frame_sequence,type:'click',selector:'[data-testid="age-30"]'};
  const result=await control.dispatch(action);
  assert.equal(result.error,null);
  assert.equal(result.extracted.control_receipt.target.value,'age-30');
  assert.equal(await page.locator('#age').inputValue(),'30');
  assert.equal(await page.locator('body').getAttribute('data-clicked'),'1');
  assert.ok(await page.locator('#options').evaluate(node=>node.scrollTop)>0);
  assert.equal(await page.evaluate(()=>scrollY),0);
  await assert.rejects(control.dispatch(action),/stale frame/);
  assert.equal(await page.locator('body').getAttribute('data-clicked'),'1');
});

for(const mutation of ['replace','type','secret','disabled','inert','hidden','obscured','reclip','expired']) test(`nested scroll handler ${mutation} fails closed before clicking`,async t=>{
  const {page,control,request}=await nestedPicker(t,'button');
  await page.locator('#options').evaluate((popup,mutation)=>{
    const target=popup.querySelector('[data-testid="age-30"]');
    popup.addEventListener('scroll',()=>{
      document.body.dataset.scrolled='yes';
      if(mutation==='replace') target.outerHTML=target.outerHTML;
      if(mutation==='type') target.type='reset';
      if(mutation==='secret') target.setAttribute('data-secret','');
      if(mutation==='disabled') target.disabled=true;
      if(mutation==='inert') target.inert=true;
      if(mutation==='hidden') target.style.visibility='hidden';
      if(mutation==='obscured') document.body.insertAdjacentHTML('beforeend','<div style="position:fixed;inset:0"></div>');
      if(mutation==='reclip') popup.scrollTop=0;
    },{once:true});
  },mutation);
  const frame=await control.dispatch({type:'live_frame'});
  const expires=Date.now()+300;
  if(mutation==='expired') await page.locator('#options').evaluate((popup,expires)=>{
    popup.addEventListener('scroll',()=>{while(Date.now()<expires) {}},{once:true});
  },expires);
  await assert.rejects(control.dispatch({...request,frame_sequence:frame.frame_sequence,...(mutation==='expired'?{actor_expires_at:new Date(expires).toISOString()}:{}),type:'click',selector:'[data-testid="age-30"]'}),/bound target changed|type changed|sensitive|not actionable|obscured|expired/);
  assert.equal(await page.locator('body').getAttribute('data-scrolled'),'yes');
  assert.equal(await page.locator('body').getAttribute('data-clicked'),null);
  assert.equal(await page.locator('#age').inputValue(),'25');
  await assert.rejects(control.dispatch({type:'control_sync',controller_kind:'agent',control_epoch:2}),/blocked/);
});

test('scroll preparation cannot bypass an overlay covering the exact target',async t=>{
  const {page,control,request}=await nestedPicker(t);
  await page.locator('body').evaluate(node=>node.insertAdjacentHTML('beforeend','<div style="position:fixed;inset:0"></div>'));
  const frame=await control.dispatch({type:'live_frame'});
  await assert.rejects(control.dispatch({...request,frame_sequence:frame.frame_sequence,type:'click',selector:'[data-testid="age-30"]'}),/obscured/);
  assert.equal(await page.locator('#age').inputValue(),'25');
  assert.equal(await page.locator('body').getAttribute('data-clicked'),null);
});

async function redrawingSelect(t, mutation='safe', event='change') {
  const result=await fixture(t);
  await result.page.setContent('<select data-testid="year" aria-label="Select year"><option value="2008">2008</option><option value="1996">Year 1996</option></select>');
  await result.page.getByTestId('year').evaluate((node,{mutation,event})=>{
    for(const type of ['input','change']) node.addEventListener(type,()=>document.body.dataset[type]=String(Number(document.body.dataset[type]||0)+1));
    node.addEventListener(event,()=>{
      const replacement=node.cloneNode(true);
      replacement.value=node.value;
      node.replaceWith(replacement);
      if(mutation==='original-secret') node.setAttribute('data-secret','');
      if(mutation==='original-value') node.value='2008';
      if(mutation==='original-multiple') node.multiple=true;
      if(mutation==='replacement-secret') replacement.setAttribute('data-secret','');
      if(mutation==='replacement-password') replacement.outerHTML='<input type="password" data-testid="year" value="PRIVATE_REPLACEMENT_MARKER">';
      if(mutation==='mismatch') replacement.value='2008';
      if(mutation==='missing') replacement.remove();
      if(mutation==='ambiguous') replacement.after(replacement.cloneNode(true));
      if(mutation==='multiple') replacement.multiple=true;
    });
  },{mutation,event});
  const frame=await result.control.dispatch({type:'live_frame'});
  return {...result,request:{...result.request,frame_sequence:frame.frame_sequence}};
}

test('final native select change may redraw one safe matching control without losing its canonical receipt',async t=>{
  const {page,control,request}=await redrawingSelect(t);
  const result=await control.dispatch({...request,type:'select',selector:'[data-testid="year"]',value:'Year 1996'});
  assert.equal(result.error,null);
  assert.deepEqual(result.extracted.control_receipt.args,{value:'1996'});
  assert.equal(await page.getByTestId('year').inputValue(),'1996');
  assert.deepEqual(await page.locator('body').evaluate(node=>({...node.dataset})),{input:'1',change:'1'});
  await control.dispatch({type:'control_sync',controller_kind:'agent',control_epoch:2});
});

for(const mutation of ['original-secret','original-value','original-multiple','replacement-secret','replacement-password','mismatch','missing','ambiguous','multiple']) test(`select redraw with ${mutation} remains unknown and blocked`,async t=>{
  const {page,control,request}=await redrawingSelect(t,mutation);
  await assert.rejects(control.dispatch({...request,type:'select',selector:'[data-testid="year"]',value:'1996'}),/outcome unknown/);
  assert.deepEqual(await page.locator('body').evaluate(node=>({...node.dataset})),{input:'1',change:'1'});
  await assert.rejects(control.dispatch({type:'control_sync',controller_kind:'agent',control_epoch:2}),/blocked/);
});

test('select removal during input stays unknown and never dispatches change into a replacement',async t=>{
  const {page,control,request}=await redrawingSelect(t,'safe','input');
  await assert.rejects(control.dispatch({...request,type:'select',selector:'[data-testid="year"]',value:'1996'}),/outcome unknown/);
  assert.deepEqual(await page.locator('body').evaluate(node=>({...node.dataset})),{input:'1'});
});

for(const values of [['2008'],['1996','1996']]) test(`select rejects ${values.length===1?'missing':'ambiguous'} requested options without dispatching events`,async t=>{
  const {page,control,request}=await fixture(t);
  await page.setContent(`<select data-testid="year">${values.map(value=>`<option value="${value}">${value}</option>`).join('')}</select>`);
  await page.getByTestId('year').evaluate(node=>node.addEventListener('change',()=>document.body.dataset.changed='yes'));
  const frame=await control.dispatch({type:'live_frame'});
  await assert.rejects(control.dispatch({...request,frame_sequence:frame.frame_sequence,type:'select',selector:'[data-testid="year"]',value:'1996'}),/missing or ambiguous/);
  assert.equal(await page.locator('body').getAttribute('data-changed'),null);
});

test('select capability expiring before actuation never writes its canonical value',async t=>{
  const {page,control,request}=await fixture(t,undefined,async(_page,action)=>{
    if(action.type==='select') await new Promise(resolve=>setTimeout(resolve,150));
  });
  await page.setContent('<select><option value="2008">2008</option><option value="1996">1996</option></select>');
  const frame=await control.dispatch({type:'live_frame'});
  await assert.rejects(control.dispatch({...request,frame_sequence:frame.frame_sequence,actor_expires_at:new Date(Date.now()+100).toISOString(),type:'select',selector:'select',value:'1996'}),/expired/);
  assert.equal(await page.locator('select').inputValue(),'2008');
});

test('suspended scroll rendering times out without clicking or reopening control',async t=>{
  const {page,control,request}=await fixture(t,undefined,async(page,action)=>{
    // Owned fixture simulates a page whose rendering callbacks are suspended.
    if(action.type==='click') await page.evaluate(()=>window.requestAnimationFrame=()=>0);
  });
  await assert.rejects(control.dispatch({...request,type:'click',selector:'[data-testid="submit"]'}),/controlled scroll did not settle/);
  assert.equal(await page.locator('body').getAttribute('data-count'),null);
  await assert.rejects(control.dispatch({type:'control_sync',controller_kind:'agent',control_epoch:2}),/blocked/);
});

for(const change of ['unavailable','expired','revision']) test(`pre-action frame ${change} has a fixed reason and never reaches observation or click`,async t=>{
  let clock=1000,attempted=0;
  const {page,control,request}=await fixture(t,()=>clock,async(_page,action)=>{
    if(action.type==='click') attempted++;
  });
  if(change==='expired') clock=6001;
  if(change==='revision') {
    // A completed background API render changes an unrelated status, not the
    // button. The whole-document freshness guard must still reject this frame.
    await page.locator('body').evaluate(node=>node.insertAdjacentHTML('beforeend','<output>API results ready</output>'));
  }
  const expected={unavailable:'frame unavailable',expired:'frame expired',revision:'page revision changed'}[change];
  await assert.rejects(control.dispatch({...request,...(change==='unavailable'?{frame_sequence:999}:{}),type:'click',selector:'[data-testid="submit"]'}),error=>error.message===`stale frame; ${expected}`);
  assert.equal(attempted,0);
  assert.equal(await page.locator('body').getAttribute('data-count'),null);
});

for (const container of [false, true]) test(`scroll receipt identifies ${container?'container':'page'} for replay`,async t=>{
  const {page,control,request}=await fixture(t);
  if(container) await page.setContent('<div data-testid="scrollbox" style="height:100px;overflow:auto"><div style="height:1000px">content</div></div>');
  const frame=await control.dispatch({type:'live_frame'});
  const action={...request,frame_sequence:frame.frame_sequence,type:'scroll',delta_x:0,delta_y:80,...(container?{selector:'[data-testid="scrollbox"]'}:{})};
  const receipt=await control.dispatch(action);
  assert.deepEqual(receipt.extracted.control_receipt.args,{delta_x:0,delta_y:80,scroll_scope:container?'container':'page'});
  if(container){
    assert.equal(receipt.extracted.control_receipt.target.value,'scrollbox');
    assert.equal(await page.getByTestId('scrollbox').evaluate(n=>n.scrollTop),80);
  } else {
    assert.equal(receipt.extracted.control_receipt.target,null);
    assert.equal(await page.evaluate(()=>scrollY),80);
  }
});

test('agent select receipts acknowledge canonical native values rather than labels',async t=>{
  const {page,control}=await fixture(t);
  await page.setContent('<select data-testid="product"><option value="prod-b">Product B</option><option value="prod-a">Product A</option></select>');
  await control.dispatch({type:'control_sync',controller_kind:'agent',control_epoch:2});
  const receipt=await control.dispatch({actor_kind:'agent',control_epoch:2,type:'select',selector:'[data-testid="product"]',value:'Product A'});
  assert.equal(receipt.error,null);
  assert.equal(receipt.extracted.control_receipt.args.value,'prod-a');
  assert.equal(receipt.extracted.control_receipt.target.kind,'test_id');
  assert.equal(await page.getByTestId('product').inputValue(),'prod-a');
});

for(const readonly of [false,true]) test(`agent custom combobox select cannot produce a completed control receipt (readonly=${readonly})`,async t=>{
  const {page,control}=await fixture(t);
  await page.setContent(`<input ${readonly?'readonly':''} role="combobox" data-testid="product" value="Product B">`);
  await control.dispatch({type:'control_sync',controller_kind:'agent',control_epoch:2});
  await assert.rejects(control.dispatch({actor_kind:'agent',control_epoch:2,type:'select',selector:'[data-testid="product"]',value:'Product A'}),readonly?/target not actionable/:/controlled select target unsupported/);
  assert.equal(await page.getByTestId('product').inputValue(),'Product B');
});

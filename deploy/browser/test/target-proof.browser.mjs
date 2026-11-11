import assert from 'node:assert/strict';
import test from 'node:test';
import {chromium} from '@playwright/test';
import {createLiveControl} from '../live-control.mjs';

async function fixture(t) {
  const browser=await chromium.launch({headless:true});t.after(()=>browser.close());
  const page=await browser.newPage({viewport:{width:800,height:600}});
  await page.setContent(`<style>.option{height:32px}</style><span id="timer">00:10</span>
    <section><label id="age-label" for="">Age</label><div id="control">
    <input id="age" readonly role="combobox" aria-controls="ages" aria-expanded="false" value="25"
      onfocus="this.setAttribute('data-focus','');this.parentElement.setAttribute('data-focus','')"
      onclick="this.setAttribute('aria-expanded','true');document.querySelector('#ages').hidden=false;document.body.dataset.clicks=String(Number(document.body.dataset.clicks||0)+1)">
    <button aria-haspopup="listbox">Toggle suggestions</button></div>
    <div id="ages" role="listbox" aria-labelledby="age-label" hidden style="height:120px;width:180px;overflow:auto">
    ${Array.from({length:17},(_,i)=>i+19).map(n=>`<div id="age-${n}" role="option" class="option" data-value="${n}" onclick="document.querySelector('#age').value='${n}';document.body.dataset.clicks=String(Number(document.body.dataset.clicks||0)+1)">${n}</div>`).join('')}</div></section>`);
  let sequence=0;
  const control=createLiveControl({page,observe:async(_action,actuate)=>{if(actuate)await actuate();return {sequence:++sequence,error:null};}});
  await control.dispatch({type:'control_sync',controller_kind:'human',control_epoch:1});
  const frame=()=>control.dispatch({type:'live_frame'});
  const action=(f,selector)=>({type:'click',selector,frame_sequence:f.frame_sequence,viewport:f.viewport,actor_kind:'human',control_epoch:1,actor_expires_at:new Date(Date.now()+60000).toISOString()});
  const tick=()=>page.locator('#timer').evaluate(n=>n.textContent='00:09');
  return {page,control,frame,action,tick};
}

function interceptSession(page,hook) {
  const context=page.context(),connect=context.newCDPSession.bind(context);
  context.newCDPSession=async target=>{const session=await connect(target),send=session.send.bind(session);
    session.send=async(method,args)=>{await hook(method,args);return send(method,args);};return session;
  };
}

test('target proof allows unrelated countdown while opening and selecting the same readonly combobox',async t=>{
  const {page,control,frame,action,tick}=await fixture(t);
  const first=await frame();await tick();await control.dispatch(action(first,'#age'));
  assert.equal(await page.locator('#ages').isVisible(),true);
  const second=await frame();await page.locator('#timer').evaluate(n=>n.textContent='00:08');
  await control.dispatch(action(second,'#age-30'));
  assert.equal(await page.locator('#age').inputValue(),'30');
  assert.equal(await page.locator('body').getAttribute('data-clicks'),'2');
  await assert.rejects(control.dispatch(action(second,'#age-30')),/stale frame/);
});

for(const mutation of ['label','value','replace','options','ownership','description','overlay','reparent']) test(`target proof rejects ${mutation} without clicking`,async t=>{
  const {page,control,frame,action}=await fixture(t);const f=await frame();
  await page.evaluate(mutation=>{
    const n=document.querySelector('#age'),box=document.querySelector('#ages');
    if(mutation==='label') document.querySelector('#age-label').textContent='Coverage amount';
    if(mutation==='value') n.value='40';
    if(mutation==='replace') n.outerHTML=n.outerHTML;
    if(mutation==='options') box.prepend(box.lastElementChild);
    if(mutation==='ownership') n.setAttribute('aria-controls','missing');
    if(mutation==='description') n.setAttribute('aria-describedby','timer');
    if(mutation==='overlay') document.body.insertAdjacentHTML('beforeend','<div style="position:fixed;inset:0"></div>');
    if(mutation==='reparent') document.body.append(n);
  },mutation);
  await assert.rejects(control.dispatch(action(f,'#age')));
  assert.equal(await page.locator('body').getAttribute('data-clicks'),null);
});

for(const phase of ['capture','admission']) for(const kind of ['label','cssom']) test(`target proof rejects ${kind} mutate-revert during ${phase}`,async t=>{
  const {page,control,frame,action}=await fixture(t);
  const mutate=()=>page.evaluate(kind=>{
    if(kind==='label'){const n=document.querySelector('#age-label'),v=n.textContent;n.textContent='Changed';n.textContent=v;}
    else {const s=document.styleSheets[0];s.insertRule('input{transform:translateX(100px)}',s.cssRules.length);s.deleteRule(s.cssRules.length-1);}
  },kind);
  if(phase==='capture')interceptSession(page,async method=>{if(method==='Runtime.evaluate')await mutate();});
  await assert.rejects(async()=>{const f=await frame();if(phase==='admission')await mutate();await control.dispatch(action(f,'#age'));});
  assert.equal(await page.locator('body').getAttribute('data-clicks'),null);
});

for(const mode of ['toggle','coordinates','form','missing-label','protected','oversized']) test(`target proof keeps ${mode} on strict validation`,async t=>{
  const {page,control,frame,action,tick}=await fixture(t);
  if(mode==='form')await page.locator('section').evaluate(n=>{const form=document.createElement('form');n.replaceWith(form);form.append(n);});
  if(mode==='missing-label')await page.locator('#ages').evaluate(n=>n.removeAttribute('aria-labelledby'));
  if(mode==='protected')await page.locator('#age-label').evaluate(n=>n.setAttribute('data-secret',''));
  if(mode==='oversized')await page.locator('#age-label').evaluate(n=>n.textContent='x'.repeat(140000));
  const f=await frame();await tick();let a=action(f,mode==='toggle'?'button':'#age');
  if(mode==='coordinates'){const box=await page.locator('#age').boundingBox();delete a.selector;a={...a,x:box.x+5,y:box.y+5};}
  await assert.rejects(control.dispatch(a));assert.equal(await page.locator('body').getAttribute('data-clicks'),null);
});

for(const change of ['secret','replace','label-aba','overlay','expired']) test(`target proof fails closed after focus: ${change}`,async t=>{
  const {page,control,frame,action,tick}=await fixture(t);
  await page.evaluate(change=>{
    const overlay=document.createElement('div');overlay.id='overlay';overlay.style='position:fixed;inset:0;display:none';document.body.append(overlay);
    document.querySelector('#age').addEventListener('focus',()=>{
      if(change==='secret')document.querySelector('#age').setAttribute('data-secret','');
      if(change==='replace'){const n=document.querySelector('#age');n.outerHTML=n.outerHTML;}
      if(change==='label-aba'){const n=document.querySelector('#age-label'),s=n.textContent;n.textContent='Private';n.textContent=s;}
      if(change==='overlay')overlay.style.display='block';
      if(change==='expired'){const until=Date.now()+200;while(Date.now()<until){}}
    });
  },change);
  const f=await frame();await tick();const a=action(f,'#age');
  if(change==='expired')a.actor_expires_at=new Date(Date.now()+150).toISOString();
  await assert.rejects(control.dispatch(a));
  assert.equal(await page.locator('body').getAttribute('data-clicks'),null);
});

test('target proof does not expose private capture state in the public frame',async t=>{
  const {frame}=await fixture(t);const f=await frame();
  assert.deepEqual(Object.keys(f).sort(),['captured_at','control_epoch','frame_sequence','image_base64','media_type','viewport']);
});

for(const kind of ['viewport','document','frame-expiry']) test(`target proof retains ${kind} fence`,async t=>{
  const {page,control,frame,action,tick}=await fixture(t);const f=await frame();await tick();
  const a=action(f,'#age');
  if(kind==='viewport')await page.setViewportSize({width:801,height:600});
  if(kind==='document')await page.goto('about:blank#replacement');
  if(kind==='frame-expiry')await new Promise(r=>setTimeout(r,5050));
  await assert.rejects(control.dispatch(a));
});

test('target proof observers are disposed on eviction, consumption and handback',async t=>{
  const {page,control,frame,action}=await fixture(t);
  await page.evaluate(()=>{
    window.observers=0;
    for(const key of ['MutationObserver','ResizeObserver']){const Base=window[key];window[key]=class extends Base{
      constructor(...args){super(...args);this.live=/data-focus|positions.get/.test(String(args[0]));if(this.live)window.observers++;}
      disconnect(){if(this.live){window.observers--;this.live=false;}return super.disconnect();}
    };}
  });
  let last;for(let i=0;i<9;i++)last=await frame();
  assert.equal(await page.evaluate(()=>window.observers),16);
  await control.dispatch(action(last,'#age'));
  assert.equal(await page.evaluate(()=>window.observers),0);
  await frame();assert.equal(await page.evaluate(()=>window.observers),2);
  await control.dispatch({type:'control_sync',controller_kind:'agent',control_epoch:2});
  assert.equal(await page.evaluate(()=>window.observers),0);
});

for(const kind of ['ancestor-name','option-description','css-rules','sibling-layout']) test(`target proof rejects changed contextual dependency: ${kind}`,async t=>{
  const {page,control,frame,action}=await fixture(t);
  await page.evaluate(kind=>{
    const note=document.createElement('span');note.id='context';note.textContent='Initial context';document.body.append(note);
    if(kind==='ancestor-name')document.querySelector('section').setAttribute('aria-labelledby','context');
    if(kind==='option-description')document.querySelector('#age-30').innerHTML='<span aria-describedby="context">30</span>';
  },kind);
  const f=await frame();await page.evaluate(kind=>{
    if(kind==='ancestor-name'||kind==='option-description')document.querySelector('#context').textContent='Changed context';
    if(kind==='css-rules')document.styleSheets[0].insertRule('input{opacity:.5}',0);
    if(kind==='sibling-layout')document.querySelector('#timer').textContent='x '.repeat(2000);
  },kind);
  await assert.rejects(control.dispatch(action(f,'#age')));
  assert.equal(await page.locator('body').getAttribute('data-clicks'),null);
});

for(const kind of ['silent-value','external-reflow']) test(`target proof rejects changed state before the capture fence: ${kind}`,async t=>{
  const {page,frame}=await fixture(t);
  interceptSession(page,async method=>{if(method==='Runtime.evaluate')await page.evaluate(kind=>{
    if(kind==='silent-value')document.querySelector('#age').value='40';
    else document.querySelector('#timer').textContent='x '.repeat(2000);
  },kind);});
  await assert.rejects(frame());
  assert.equal(await page.locator('body').getAttribute('data-clicks'),null);
});

test('target proof freezes site timers through screenshot and resumes them afterward',async t=>{
  const {page,control,frame,action}=await fixture(t);
  const context=page.context(),connect=context.newCDPSession.bind(context);
  let nativeCapture=false;
  context.newCDPSession=async target=>{
    const session=await connect(target),send=session.send.bind(session);let callFrameId;
    session.on('Debugger.paused',event=>{callFrameId=event.callFrames[0].callFrameId;});
    session.send=async(method,args)=>{
      if(method==='Runtime.evaluate')await page.evaluate(()=>{window.ran=false;setTimeout(()=>{const n=document.querySelector('#age');n.value='40';n.value='25';window.ran=true;},30);});
      if(method==='Page.captureScreenshot'){
        nativeCapture=true;await new Promise(r=>setTimeout(r,80));
        const state=await send('Debugger.evaluateOnCallFrame',{callFrameId,expression:'window.ran',returnByValue:true});
        assert.equal(state.result.value,false,'site timer must not execute inside capture');
      }
      return send(method,args);
    };return session;
  };
  const f=await frame();assert.equal(nativeCapture,true);
  await page.waitForFunction(()=>window.ran===true);
  await control.dispatch(action(f,'#age'));
  assert.equal(await page.locator('body').getAttribute('data-clicks'),'1');
});

test('native capture failure resumes page execution without dispatching a click',async t=>{
  const {page,frame}=await fixture(t);
  interceptSession(page,async method=>{if(method==='Page.captureScreenshot')throw new Error('PRIVATE_TEST_FAILURE');});
  await assert.rejects(frame());
  assert.equal(await page.evaluate(()=>42),42);
  assert.equal(await page.locator('body').getAttribute('data-clicks'),null);
});

for(const kind of ['child-frame','animation','pending-font']) test(`native capture keeps ${kind} on strict masked validation`,async t=>{
  const {page,control,frame,action,tick}=await fixture(t);
  if(kind==='child-frame')await page.evaluate(()=>document.body.append(document.createElement('iframe')));
  if(kind==='animation')await page.evaluate(()=>document.querySelector('#timer').animate([{opacity:1},{opacity:.5}],{duration:100000,iterations:Infinity}));
  if(kind==='pending-font')await page.evaluate(()=>Object.defineProperty(document.fonts,'status',{get:()=> 'loading'}));
  const f=await frame();await tick();await assert.rejects(control.dispatch(action(f,'#age')));
});


test('capture rejects exact target replacement before its native pause',async t=>{
  const {page,control,frame,action}=await fixture(t);
  interceptSession(page,async method=>{if(method==='Runtime.evaluate')await page.locator('#age').evaluate(n=>n.outerHTML=n.outerHTML);});
  await assert.rejects(async()=>{const f=await frame();await control.dispatch(action(f,'#age'));});
  assert.equal(await page.locator('body').getAttribute('data-clicks'),null);
});

test('capture rejects an image readiness transition even when DOM text is unchanged',async t=>{
  const {page,frame}=await fixture(t);
  await page.evaluate(()=>{const image=document.createElement('img');image.width=10;image.height=10;Object.defineProperty(image,'complete',{value:false,configurable:true});document.body.append(image);});
  let checks=0;
  interceptSession(page,async(method,args)=>{if(method==='Debugger.evaluateOnCallFrame'&&++checks===2)args.expression=`(Object.defineProperty(document.querySelector('img'),'complete',{value:true}),${args.expression})`;});
  await assert.rejects(frame());assert.equal(await page.evaluate(()=>42),42);
});

test('authorized option scroll preserves hidden decorative descendants',async t=>{
  const {page,control,frame,action,tick}=await fixture(t);
  await page.locator('#age-30').evaluate(n=>n.innerHTML='<span>30</span><span hidden><svg><path d="M0 0L1 1"/></svg></span>');
  let f=await frame();await control.dispatch(action(f,'#age'));
  f=await frame();await tick();await control.dispatch(action(f,'#age-30'));
  assert.equal(await page.locator('#age').inputValue(),'30');
});

test('uncertain native restoration blocks further control transitions',async t=>{
  const {page,control,frame}=await fixture(t);
  const context=page.context(),connect=context.newCDPSession.bind(context);
  context.newCDPSession=async target=>{const session=await connect(target),send=session.send.bind(session);
    session.send=async(method,args)=>{const result=await send(method,args);if(method==='Debugger.resume')throw new Error('PRIVATE_RESTORATION_FAILURE');return result;};return session;
  };
  await assert.rejects(frame(),/capture fence restoration failed/);
  assert.equal(await page.evaluate(()=>42),42);
  await assert.rejects(control.dispatch({type:'control_sync',controller_kind:'agent',control_epoch:2}),/runtime blocked/);
});

test('actual paused-page restoration failure returns bounded and blocks later writes',async t=>{
  const {page,control,frame}=await fixture(t);
  const context=page.context(),connect=context.newCDPSession.bind(context);let fail=false,paused=false;
  context.newCDPSession=async target=>{const session=await connect(target),send=session.send.bind(session);
    session.on('Debugger.paused',()=>{paused=true;});session.on('Debugger.resumed',()=>{paused=false;});
    session.send=async(method,args)=>{if(fail&&['Debugger.resume','Debugger.disable'].includes(method)){assert.equal(paused,true);throw new Error('PRIVATE_RESTORATION_FAILURE');}return send(method,args);};return session;
  };
  await frame();fail=true;
  let timer;
  try{await assert.rejects(Promise.race([frame(),new Promise((_,reject)=>{timer=setTimeout(()=>reject(new Error('capture did not return')),6500);})]),/capture fence restoration failed/);}
  finally{clearTimeout(timer);}
  await assert.rejects(control.dispatch({type:'control_sync',controller_kind:'agent',control_epoch:2}),/runtime blocked/);
});

test('a stalled native stylesheet barrier times out and resumes the page',async t=>{
  const {page,control,frame}=await fixture(t);
  const context=page.context(),connect=context.newCDPSession.bind(context);
  context.newCDPSession=async target=>{const session=await connect(target),send=session.send.bind(session);let paused=false;
    session.on('Debugger.paused',()=>{paused=true;});session.on('Debugger.resumed',()=>{paused=false;});
    session.send=async(method,args)=>{if(paused&&method==='CSS.getMediaQueries')return new Promise(()=>{});return send(method,args);};return session;
  };
  await assert.rejects(frame(),/capture fence timed out/);
  assert.equal(await page.evaluate(()=>42),42);
  await control.dispatch({type:'control_sync',controller_kind:'agent',control_epoch:2});
});

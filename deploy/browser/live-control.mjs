// Private inspector protocol. The service, not page JSON, authenticates the actor.
import {createStyleFence,captureTargetProof,captureProofFrame} from './frame-target-proof.mjs';
// Presence of data-secret or data-sensitive explicitly designates protected data.
const sensitiveSelector='input[type="password"],[autocomplete*="password"],[autocomplete="one-time-code"],[autocomplete^="cc-"],[data-secret],[data-sensitive]';
export function serialDispatcher(operation) {
  let tail=Promise.resolve();
  return action=>{
    const result=tail.then(()=>operation(action));
    tail=result.catch(()=>{});
    return result;
  };
}
async function revision(page) {
  return page.evaluate(()=>{
    // Compare page state rather than MutationObserver ticks: Playwright briefly
    // injects screenshot styles, which must not invalidate every frame.
    const root=document.documentElement.cloneNode(true);for(const n of root.querySelectorAll('[style=""]'))n.removeAttribute('style');
    const data=root.outerHTML+JSON.stringify([...document.querySelectorAll('input,select,textarea')].filter(n=>n.type!=='password'&&!/password|one-time-code|cc-/.test(n.autocomplete||'')).map(n=>[n.value,n.checked]));
    let first=2166136261,second=5381;
    for(let i=0;i<data.length;i++){first=Math.imul(first^data.charCodeAt(i),16777619);second=Math.imul(second,33)^data.charCodeAt(i);}
    return JSON.stringify([location.href,scrollX,scrollY,innerWidth,innerHeight,data.length,first,second]);
  });
}
async function targetFor(page, action, retainElement=false) {
  let selector=action.selector;
  if (action.type==='scroll') selector='body';
  if (!selector && ['click','fill','select','press'].includes(action.type)) {
    if (![action.x,action.y].every(Number.isFinite)) throw new Error('invalid click coordinates');
    selector=await page.evaluate(({x,y})=>{
      let node=document.elementFromPoint(x,y);
      node=node?.closest('button,a,input,select,textarea,[role],[data-testid]') || node;
      if (!node || node.tagName==='IFRAME') throw new Error('target unavailable');
      const parts=[];
      for(let el=node;el;el=el.parentElement){
        parts.unshift(`${el.tagName.toLowerCase()}:nth-of-type(${[...(el.parentElement?.children||[el])].filter(s=>s.tagName===el.tagName).indexOf(el)+1})`);
      }
      return parts.join(' > ');
    },{x:action.x,y:action.y});
  }
  if(typeof selector!=='string'||selector.length>2000) throw new Error('invalid target');
  const locator=page.locator(selector);
  if(await locator.count()!==1) throw new Error('target missing or ambiguous');
  const element=await locator.elementHandle();
  try {
    if(await element.ownerFrame()!==page.mainFrame()) throw new Error('nested frame target unsupported in live control');
  } catch(error) { await element.dispose(); throw error; }
  try {
  const attach=result=>({...result,...(retainElement?{element}: {})});
  const facts=await element.evaluate((node,sensitiveSelector)=>{
    if(node.matches(sensitiveSelector)) throw new Error('sensitive target unavailable');
    const unique=css=>document.querySelectorAll(css).length===1;
    const id=node.getAttribute('data-testid');
    const testId=id&&unique(`[data-testid=${JSON.stringify(id)}]`)?id:null;
    let css=node.id&&unique(`#${CSS.escape(node.id)}`)?`#${CSS.escape(node.id)}`:null;
    if(!css){const parts=[];for(let el=node;el;el=el.parentElement){parts.unshift(`${el.tagName.toLowerCase()}:nth-of-type(${[...(el.parentElement?.children||[el])].filter(s=>s.tagName===el.tagName).indexOf(el)+1})`);}css=parts.join(' > ');}
    return {testId,css};
  },sensitiveSelector);
  if(facts.testId) return attach({selector:`[data-testid=${JSON.stringify(facts.testId)}]`,target:{kind:'test_id',value:facts.testId,fallback:false}});
  const snapshot=await locator.ariaSnapshot();
  const match=snapshot.match(/^- ([a-z]+) "((?:[^"\\]|\\.)*)"/);
  if(match) {
    const role=match[1], name=JSON.parse(`"${match[2]}"`);
    if(await page.getByRole(role,{name,exact:true}).count()===1) {
      return attach({selector:facts.css,target:{kind:'role_name',value:role,name,fallback:false}});
    }
  }
  return attach({selector:facts.css,target:{kind:'css',value:facts.css,fallback:true}});
  } catch(error) { await element.dispose(); throw error; }
  finally { if(!retainElement) await element.dispose(); }
}
// This fixed callback is the only controlled write path. The exact bound DOM
// element, sensitivity, actionability and deadline are checked in the same JS
// task as mutation: no selector re-resolution or asynchronous input dispatch.
// Click preparation may scroll the retained node and yield for scroll handlers;
// the final guards and actual click still run together after that boundary.
async function actuateBound(binding, action) {
  const expires=action.actor_kind==='human'?Math.min(Date.parse(action.actor_expires_at),binding.frameDeadline??Infinity):null;
  if(action.actor_kind==='human'&&(!Number.isFinite(expires)||Date.now()>=expires)) throw new Error('human capability expired');
  if(action.type==='press') throw new Error('controlled press unsupported');
  if(binding.proof) {
    await binding.element.evaluate(async(node,{proof,expires})=>{
      const valid=()=>{if(Date.now()>=expires)throw new Error('human capability expired');proof.check(node,true);};
      proof.prepare(node);
      const r=node.getBoundingClientRect();
      if(!r.width||!r.height||getComputedStyle(node).visibility!=='visible'||node.matches(':disabled')||node.getAttribute('aria-disabled')==='true'||node.closest('[inert]'))throw new Error('target not actionable');
      valid();Element.prototype.scrollIntoView.call(node,{block:'nearest',inline:'nearest',behavior:'instant'});
      await new Promise((resolve,reject)=>{const timeout=setTimeout(()=>reject(new Error('controlled scroll did not settle')),1000);requestAnimationFrame(()=>{clearTimeout(timeout);resolve();});});
      valid();
      if(node instanceof HTMLInputElement){HTMLElement.prototype.focus.call(node,{preventScroll:true});valid();}
    },{proof:binding.proof.handle,expires});
    await binding.proof.checkStyles();
  }
  return binding.element.evaluate(async (node,{action,selector,expires,sensitiveSelector,proof})=>{
    const sensitive=()=>node.matches(sensitiveSelector);
    const identity=()=>node.isConnected&&node.ownerDocument===document&&document.querySelector(selector)===node;
    const current=()=>{
      if(expires!==null&&Date.now()>=expires) throw new Error('human capability expired');
      if(!identity()) throw new Error('bound target changed');
      if(sensitive()) throw new Error('sensitive target unavailable');
      if(proof)proof.check(node,true);
    };
    const actionable=(checkHit=true)=>{
      const rect=node.getBoundingClientRect(),style=getComputedStyle(node);
      const editing=action.type==='fill'||action.type==='select';
      if(!rect.width||!rect.height||style.visibility!=='visible'||node.matches(':disabled')||(editing&&node.readOnly)||node.closest('[inert]')) throw new Error('target not actionable');
      if(checkHit&&action.type!=='scroll') {
        const x=Math.max(0,Math.min(innerWidth-1,rect.x+rect.width/2));
        const y=Math.max(0,Math.min(innerHeight-1,rect.y+rect.height/2));
        const hit=document.elementFromPoint(x,y);
        if(!hit||!node.contains(hit)) throw new Error('target obscured or outside viewport');
      }
    };
    current();actionable(action.type!=='click');
    if(action.type==='click'&&!proof) {
      const type=node.type;
      current();Element.prototype.scrollIntoView.call(node,{block:'nearest',inline:'nearest',behavior:'instant'});
      // Scroll events are queued by the browser. Let their handlers run before
      // final checks, without reacquiring a selector or dispatching any input.
      await new Promise((resolve,reject)=>{
        const timeout=setTimeout(()=>reject(new Error('controlled scroll did not settle')),1000);
        requestAnimationFrame(()=>{clearTimeout(timeout);resolve();});
      });
      current();
      if(node.type!==type) throw new Error('bound target type changed');
      actionable();
    }
    const after=()=>{if(!identity()||sensitive()) throw new Error('controlled action outcome unknown');};
    if(action.type==='fill') {
      if(!(node instanceof HTMLInputElement||node instanceof HTMLTextAreaElement)||
          (node instanceof HTMLInputElement&&!['text','email','number','tel','url','search'].includes(node.type))) throw new Error('controlled fill target unsupported');
      if(typeof action.value!=='string'||action.value.length>4000) throw new Error('invalid fill');
      const proto=node instanceof HTMLInputElement?HTMLInputElement.prototype:HTMLTextAreaElement.prototype;
      current();
      Object.getOwnPropertyDescriptor(proto,'value').set.call(node,action.value);
      node.dispatchEvent(new Event('input',{bubbles:true}));after();
      node.dispatchEvent(new Event('change',{bubbles:true}));after();
    } else if(action.type==='select') {
      if(!(node instanceof HTMLSelectElement)||node.multiple) throw new Error('controlled select target unsupported');
      const options=[...node.options].filter(o=>o.value===action.value||o.label===action.value);
      if(options.length!==1||options[0].matches(':disabled')) throw new Error('requested option missing or ambiguous');
      const selectedValue=options[0].value;
      current();
      Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype,'value').set.call(node,selectedValue);
      node.dispatchEvent(new Event('input',{bubbles:true}));after();
      node.dispatchEvent(new Event('change',{bubbles:true}));
      // A final change handler may redraw a native picker. Only acknowledge the
      // already-dispatched canonical value, never a new post-handler value.
      // Input-handler removal remains forbidden by after() above.
      if(node.ownerDocument!==document||node.multiple||sensitive()||node.value!==selectedValue) throw new Error('controlled action outcome unknown');
      if(!identity()) {
        const replacements=document.querySelectorAll(selector),replacement=replacements[0];
        if(node.isConnected||replacements.length!==1||!(replacement instanceof HTMLSelectElement)||replacement.multiple||replacement.matches(sensitiveSelector)||replacement.value!==selectedValue) throw new Error('controlled action outcome unknown');
      }
      return {value:selectedValue};
    } else if(action.type==='click') {
      if(!proof&&(node instanceof HTMLInputElement||node instanceof HTMLTextAreaElement||node instanceof HTMLButtonElement||node instanceof HTMLSelectElement)) {
        // HTMLElement.click() omits the focus a user click gives form controls.
        // Focus can open readonly pickers, but its handlers can also replace or
        // protect the target. Recheck the same node before dispatching the click.
        const type=node.type;
        current();HTMLElement.prototype.focus.call(node,{preventScroll:true});
        current();
        if(node.type!==type) throw new Error('bound target type changed');
        actionable();
      }
      current();actionable();node.click(); // Navigation/removal after click is an expected result.
    } else if(action.type==='scroll') {
      current();window.scrollBy({left:action.delta_x,top:action.delta_y,behavior:'instant'});
    } else throw new Error('unsupported controlled write');
    return null;
  },{action:{type:action.type,value:action.value,delta_x:action.delta_x,delta_y:action.delta_y},selector:binding.selector,expires,sensitiveSelector,proof:binding.proof?.handle??null});
}
async function actuateNavigation(page, action) {
  const url=new URL(action.url);
  if(!['http:','https:'].includes(url.protocol)||url.username||url.password) throw new Error('unsupported navigation URL');
  const expires=action.actor_kind==='human'?Date.parse(action.actor_expires_at):null;
  if(action.actor_kind==='human'&&(!Number.isFinite(expires)||Date.now()>=expires)) throw new Error('human capability expired');
  // Arm the observer first, but initiate exactly once. Context loss or timeout
  // remains an unknown outcome; never use a second goto to recover.
  const navigation=page.waitForNavigation({waitUntil:'domcontentloaded',timeout:30000});
  void navigation.catch(()=>{});
  await page.evaluate(({url,expires})=>{
    if(expires!==null&&Date.now()>=expires) throw new Error('human capability expired');
    location.assign(url);
  },{url:action.url,expires});
  await navigation;
  return {url:action.url};
}
export function createLiveControl({page,observe,onControl=async()=>{},now=Date.now}) {
  let epoch=0,kind='agent',frameSequence=0,observationSequence=0;
  const frames=new Map();
  let styleFencePromise;
  const release=async(frame,keep)=>{clearTimeout(frame.timer);if(frame.proof&&frame.proof!==keep)await frame.proof.dispose();};
  const clearFrames=async keep=>{const old=[...frames.values()];frames.clear();await Promise.all(old.map(f=>release(f,keep)));};
  page.on('close',()=>{void clearFrames();void styleFencePromise?.then(f=>f?.session.detach().catch(()=>{}));});
  const assertControl=action=>{
    if(action.actor_kind==='human'&&action.actor_expires_at!==undefined&&(!Number.isFinite(Date.parse(action.actor_expires_at))||Date.now()>=Date.parse(action.actor_expires_at))) throw new Error('human capability expired');
    if(action.control_epoch!==epoch||action.actor_kind!==kind) throw new Error('stale or missing control generation');
  };
  const assertFrame=async (action,binding,envelopeOnly=false)=>{
    const frame=frames.get(action.frame_sequence);
    if(!frame) throw new Error('stale frame; frame unavailable');
    if(frame.epoch!==epoch) throw new Error('stale frame; control generation changed');
    if(now()-frame.created>5000) throw new Error('stale frame; frame expired');
    if(action.viewport?.width!==frame.viewport.width||action.viewport?.height!==frame.viewport.height) throw new Error('viewport mismatch');
    if(envelopeOnly)return;
    if(action.type==='click'&&action.selector&&binding&&frame.proof&&await frame.proof.handle.evaluate((p,n)=>p.check(n),binding.element)) {
      await frame.proof.checkStyles();binding.proof=frame.proof;binding.frameDeadline=Date.now()+Math.max(0,5000-(now()-frame.created));return;
    }
    if(!frame.strict||frame.revision!==await revision(page)) throw new Error('stale frame; page revision changed');
  };
  const preflight=async (action,retainElement=false)=>{
    assertControl(action);
    if(kind!=='human'||!['click','fill','select','scroll'].includes(action.type)) throw new Error('unsupported human control');
    await assertFrame(action,null,true);
    const target=await targetFor(page,action,true);
    try {
      await assertFrame(action,target);
      if(action.type==='scroll' && (![action.delta_x,action.delta_y].every(Number.isFinite)||Math.abs(action.delta_x)>2000||Math.abs(action.delta_y)>2000)) throw new Error('invalid scroll delta');
      return retainElement?{...target,pre_observation_sequence:observationSequence}:{selector:target.selector,target:target.target,pre_observation_sequence:observationSequence};
    } catch(error) {await target.element.dispose();throw error;}
    finally {if(!retainElement)await target.element.dispose();}
  };
  return {dispatch:serialDispatcher(async action=>{
    if(action.type==='control_sync') {
      if(kind==='blocked') throw new Error('runtime blocked after uncertain control outcome');
      if(!['agent','human'].includes(action.controller_kind)||!Number.isSafeInteger(action.control_epoch)||action.control_epoch!==epoch+1) throw new Error('invalid control transition');
      // Fail closed if stopping trace or collecting the new observation fails.
      kind='blocked';await clearFrames();
      await onControl(action.controller_kind);
      const result=await observe({type:'snapshot'});
      if(result.error) throw new Error('fresh observation unavailable');
      observationSequence=result.sequence;
      epoch=action.control_epoch;kind=action.controller_kind;
      return result;
    }
    if(action.type==='live_frame') {
      const viewport=page.viewportSize();
      if(!viewport||viewport.width>1920||viewport.height>1080) throw new Error('viewport exceeds frame limit');
      const before=await revision(page);
      let proof=null;
      try {
      if(page.frames().length===1&&await page.locator('input[readonly][role="combobox"]').count()&&!(await Promise.all(page.frames().map(f=>f.locator(sensitiveSelector).count()))).some(Boolean)) {
        styleFencePromise??=createStyleFence(page).catch(()=>null);
        const fence=await styleFencePromise;if(fence&&!fence.closed)proof=await captureTargetProof(page,fence,sensitiveSelector);
      }
      const bytes=proof?await captureProofFrame(proof):await page.screenshot({type:'jpeg',quality:60,fullPage:false,timeout:5000,caret:'initial',mask:page.frames().map(frame=>frame.locator(sensitiveSelector))});
      const strict=before===await revision(page);
      const stable=proof&&await proof.checkStyles().then(()=>proof.handle.evaluate(p=>p.stable())).catch(()=>false);
      if(bytes.length>500000||(proof?!stable:!strict)) throw new Error('frame changed or exceeds limit');
      const frame={epoch,frame_sequence:++frameSequence,created:now(),revision:before,viewport,strict,proof};
      frames.set(frameSequence,frame);
      frame.timer=setTimeout(()=>{if(frames.get(frame.frame_sequence)===frame){frames.delete(frame.frame_sequence);void release(frame);}},5001);frame.timer.unref?.();
      while(frames.size>8){const first=frames.keys().next().value,old=frames.get(first);frames.delete(first);await release(old);}
      return {control_epoch:epoch,frame_sequence:frameSequence,captured_at:new Date(frame.created).toISOString(),viewport,media_type:'image/jpeg',image_base64:bytes.toString('base64')};
      }catch(error){
        if(error?.message==='capture fence restoration failed'){
          kind='blocked';
          const abandoned=new Set([proof,...[...frames.values()].map(f=>f.proof)]);
          for(const frame of frames.values())clearTimeout(frame.timer);frames.clear();
          await Promise.all([...abandoned].filter(Boolean).map(p=>p.abandon()));
        }else await proof?.dispose();
        throw error;
      }
    }
    if(action.type==='human_preflight') return preflight(action.action);
    if(action.type==='recording') return observe(action);
    assertControl(action);
    if(action.type==='preflight') {
      if(!['fill','select','press'].includes(action.operation)||['value','text','key','arguments'].some(key=>Object.hasOwn(action,key))) throw new Error('preflight must be value-free');
      if(kind==='human') await assertFrame(action);
      const bound=await targetFor(page,{...action,type:action.operation});
      const result=await observe({type:'snapshot'});
      if(result.error) throw new Error('preflight observation unavailable');
      observationSequence=result.sequence;
      return {...result,extracted:{preflight:{safe:true,operation:action.operation,selector:action.selector,x:action.x,y:action.y,frame_sequence:action.frame_sequence,viewport:action.viewport,control_epoch:epoch,target:bound.target,pre_observation_sequence:result.sequence}}};
    }
    let binding=null;
    if(kind==='agent'&&['click','fill','select','press'].includes(action.type)) {
      binding=await targetFor(page,action,true);
      action={...action,selector:binding.selector};
    }
    const preSequence=observationSequence;
    if(action.type==='navigate') {
      if(kind==='human') await assertFrame(action);
      binding={target:null};
    }
    if(kind==='human'&&action.type!=='navigate') {
      if(action.type==='fill'&&(typeof action.value!=='string'||action.value.length>4000)) throw new Error('invalid fill');
      const bound=await preflight(action,true);
      binding=bound;
      action={...action,selector:bound.selector};
    }
    await clearFrames(binding?.proof);
    let result,acknowledgedArgs=null;
    const actuate=binding?async()=>{
      acknowledgedArgs=action.type==='navigate'?await actuateNavigation(page,action):await actuateBound(binding,action);
    }:undefined;
    try {
      result=await observe(action,actuate);
      if(binding&&result.error) kind='blocked';
    } catch(error) {
      if(binding) kind='blocked';
      throw error;
    } finally { await binding?.element?.dispose();await binding?.proof?.dispose(); }
    observationSequence=result.sequence;
    if(binding&&!result.error) {
      const args={};
      for(const key of ['value','key','delta_x','delta_y']) if(Object.hasOwn(action,key)) args[key]=action[key];
      if(acknowledgedArgs) Object.assign(args,acknowledgedArgs);
      return {...result,extracted:{...result.extracted,control_receipt:{target:binding.target,pre_observation_sequence:preSequence,args}}};
    }
    return result;
  })};
}

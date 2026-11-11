// Private, narrowly scoped freshness proofs. Never serialized into frame JSON.
export async function createStyleFence(page) {
  const session=await page.context().newCDPSession(page);
  const fence={version:0,closed:false,session,async barrier(){await session.send('CSS.getMediaQueries');}};
  for(const event of ['CSS.styleSheetAdded','CSS.styleSheetRemoved','CSS.styleSheetChanged']) session.on(event,()=>fence.version++);
  session.on('Disconnected',()=>{fence.closed=true;});
  try{await session.send('DOM.enable');await session.send('CSS.enable');await fence.barrier();return fence;}
  catch(error){await session.detach().catch(()=>{});throw error;}
}

export async function captureTargetProof(page, fence, sensitiveSelector) {
  const version=fence.version;
  const handle=await page.evaluateHandle(({sensitiveSelector})=>{
    const groups=[],targets=new Map(),knownIds=new Set();let total=0,disposed=false;
    const fail=()=>{throw new Error('frame target proof changed');};
    const attrs=['id','name','type','role','title','placeholder','aria-label','aria-labelledby','aria-describedby','aria-controls','aria-owns','aria-disabled','aria-readonly','aria-selected','aria-checked','data-value','autocomplete','disabled','readonly','inert','aria-hidden'];
    const unique=id=>{if(!id)return null;const nodes=document.querySelectorAll(`#${CSS.escape(id)}`);return nodes.length===1?nodes[0]:null;};
    const protectedNode=n=>n.matches(sensitiveSelector)||Boolean(n.querySelector(sensitiveSelector));
    const values=n=>[n.tagName,...attrs.map(a=>n.getAttribute(a)),n instanceof HTMLInputElement?n.value:null,n instanceof HTMLInputElement?n.checked:null];
    const chain=n=>{const out=[];for(let p=n.parentElement;p;p=p.parentElement)out.push(p);return out;};
    const rect=n=>{const r=n.getBoundingClientRect();return [r.x,r.y,r.width,r.height].map(v=>Math.round(v*64)/64);};
    const appearance=n=>{const s=getComputedStyle(n);return [s.display,s.visibility,s.opacity,s.pointerEvents,s.transform,s.clipPath,s.overflowX,s.overflowY,getComputedStyle(n,'::before').content,getComputedStyle(n,'::after').content];};
    const bytes=s=>new TextEncoder().encode(s).length;
    const inputs=[...document.querySelectorAll('input[readonly][role="combobox"]')];
    for(const input of inputs.length<=128?inputs:[]) {
      targets.set(input,null);
      const ids=(input.getAttribute('aria-controls')||'').trim().split(/\s+/).filter(Boolean),box=ids.length===1?unique(ids[0]):null;
      if(!(input instanceof HTMLInputElement)||input.type!=='text'||input.form||!box||box.getAttribute('role')!=='listbox'||box.closest('form')||input.shadowRoot||box.shadowRoot)continue;
      if(inputs.filter(n=>(n.getAttribute('aria-controls')||'').trim()===ids[0]).length!==1)continue;
      const optionNodes=box.querySelectorAll('[role="option"]');if(optionNodes.length>128)continue;
      const options=[...optionNodes];
      if(!options.length||options.some(n=>n.closest('[role="listbox"]')!==box||n.shadowRoot)||targets.size+options.length>128)continue;
      const roots=[...new Set([input,box,...options,...input.labels])],seen=new Set(),visiting=new Set();let complete=true;
      if(!(box.getAttribute('aria-labelledby')||'').trim())continue;
      const inspect=n=>{
        if(visiting.has(n)){complete=false;return;}if(seen.has(n))return;
        if(seen.size>=1024||n.shadowRoot||n.hasAttribute('aria-owns')){complete=false;return;}
        seen.add(n);visiting.add(n);
        for(const attr of ['aria-labelledby','aria-describedby']) {
          const names=(n.getAttribute(attr)||'').trim().split(/\s+/).filter(Boolean);
          if(names.length>8){complete=false;break;}
          for(const id of names){const ref=unique(id);if(!ref){complete=false;break;}if(!roots.includes(ref))roots.push(ref);inspect(ref);}
        }
        visiting.delete(n);
      };
      for(let i=0;i<roots.length&&complete;i++) {
        const n=roots[i],descendants=n.querySelectorAll('*');
        if(descendants.length>1024||roots.length>256||protectedNode(n)){complete=false;break;}
        if(n!==input&&(n.matches('input,select,textarea,button,[contenteditable]')||n.querySelector('input,select,textarea,button,[contenteditable]'))){complete=false;break;}
        for(const child of [n,...descendants])inspect(child);
        for(const ancestor of chain(n)){
          inspect(ancestor);
          if(ancestor.tagName==='FIELDSET')for(const legend of ancestor.querySelectorAll(':scope > legend'))if(!roots.includes(legend))roots.push(legend);
        }
      }
      if(!complete)continue;
      const parents=new Map(roots.map(n=>[n,chain(n)])),nodes=[...seen];
      const animations=()=>document.getAnimations().some(a=>a.playState==='running'&&nodes.some(n=>n===a.effect?.target||roots.includes(n)&&n.contains(a.effect?.target)));
      if(animations())continue;
      const semantic=()=>JSON.stringify([roots.map(n=>[values(n),n===input?null:n.textContent]),nodes.filter(n=>!roots.includes(n)).map(values),[...box.querySelectorAll('[role="option"]')].map(n=>options.indexOf(n)),input.form===null]);
      let dirty=false,prepared=false;
      const records=items=>{for(const m of items)if(!(prepared&&m.type==='attributes'&&m.attributeName==='data-focus'&&nodes.includes(m.target)))dirty=true;};
      const observer=new MutationObserver(records);
      for(const n of roots)observer.observe(n,{attributes:true,childList:true,characterData:true,subtree:true});
      for(const n of nodes)if(!roots.includes(n))observer.observe(n,{attributes:true,childList:true});
      const baseline=semantic();if(bytes(baseline)>131072||total+bytes(baseline)>524288){observer.disconnect();continue;}total+=bytes(baseline);
      const rendered=new Map(nodes.map(n=>[n,n.getClientRects().length!==0]));
      const positions=new Map(nodes.map(n=>[n,rect(n)])),looks=new Map(nodes.map(n=>[n,appearance(n)])),scrolls=new Map(nodes.map(n=>[n,[n.scrollLeft,n.scrollTop]]));
      const baseWindow=[scrollX,scrollY],url=location.href,doc=document,viewport=[innerWidth,innerHeight];
      const resize=new ResizeObserver(entries=>{for(const e of entries){const r=rect(e.target),b=positions.get(e.target);if(r[2]!==b[2]||r[3]!==b[3])dirty=true;}});
      for(const n of nodes)resize.observe(n);
      const group={
        input,box,options,captureNodes:nodes,
        check(target,afterPreparation=false){
          records(observer.takeRecords());
          if(disposed||dirty||document!==doc||location.href!==url||innerWidth!==viewport[0]||innerHeight!==viewport[1]||(!afterPreparation&&(scrollX!==baseWindow[0]||scrollY!==baseWindow[1])))fail();
          if(!afterPreparation&&animations())fail();
          if(roots.some(n=>!n.isConnected||n.ownerDocument!==doc||protectedNode(n))||semantic()!==baseline)fail();
          if(unique(ids[0])!==box||roots.some(n=>{const a=chain(n),b=parents.get(n);return a.length!==b.length||a.some((v,i)=>v!==b[i]);}))fail();
          const allowed=afterPreparation?new Set(chain(target)):new Set();
          for(const n of nodes){
            const r=rect(n),b=positions.get(n),a=appearance(n),old=looks.get(n);
            if(a.some((v,i)=>v!==old[i]))fail();
            const scroll=scrolls.get(n);
            if((n.scrollLeft!==scroll[0]||n.scrollTop!==scroll[1])&&!allowed.has(n))fail();
            let dx=afterPreparation?scrollX-baseWindow[0]:0,dy=afterPreparation?scrollY-baseWindow[1]:0;
            if(afterPreparation)for(const parent of chain(n)){if(parent===document.scrollingElement)continue;const s=scrolls.get(parent);if(s){dx+=parent.scrollLeft-s[0];dy+=parent.scrollTop-s[1];}}
            if((n.getClientRects().length!==0)!==rendered.get(n))fail();
            if(!rendered.get(n)){dx=0;dy=0;}
            if(Math.abs(r[0]+dx-b[0])>0.02||Math.abs(r[1]+dy-b[1])>0.02||r[2]!==b[2]||r[3]!==b[3])fail();
          }
          return true;
        },
        prepare(target){this.check(target);prepared=true;},
        dispose(){observer.disconnect();resize.disconnect();},
      };
      groups.push(group);targets.set(input,group);knownIds.add(input.id);knownIds.add(box.id);
      for(const n of options)targets.set(n,group);
    }
    // A fixed, read-only capture expression can run on a paused call frame.
    // Its baseline is taken in this same JS task as all private node bindings.
    const captureState=(selectors,sensitiveSelector)=>{
      if(document.fonts.status!=='loaded'||document.querySelector(sensitiveSelector)||document.querySelector('iframe,frame,video,canvas,object,embed,svg animate,svg animateTransform,svg animateMotion,svg set')||document.getAnimations().some(a=>a.playState==='running'))throw new Error('unsupported capture state');
      const properties=['display','visibility','opacity','pointer-events','transform','clip-path','overflow-x','overflow-y','font','line-height','color','background-color','border','padding','margin','filter','content'];
      const root=document.documentElement.cloneNode(true);for(const n of root.querySelectorAll('[style=""]'))n.removeAttribute('style');
      const data=JSON.stringify([location.href,scrollX,scrollY,innerWidth,innerHeight,root.outerHTML,[...document.querySelectorAll('input,select,textarea')].map(n=>[n.value,n.checked]),[...document.images].map(n=>[n.complete,n.currentSrc,n.naturalWidth,n.naturalHeight]),selectors.map(selector=>{
        const matches=document.querySelectorAll(selector);if(matches.length!==1)throw new Error('capture dependency changed');
        const n=matches[0],r=n.getBoundingClientRect();
        if(n.matches('img,image,video,canvas,object,embed'))throw new Error('unsupported capture state');
        return [n.tagName,r.x,r.y,r.width,r.height,n.scrollLeft,n.scrollTop,...['', '::before','::after'].map(p=>{const s=getComputedStyle(n,p||null);return properties.map(k=>s.getPropertyValue(k));})];
      })]);
      if(data.length>4000000)throw new Error('unsupported capture state');
      let first=2166136261,second=5381;for(let i=0;i<data.length;i++){first=Math.imul(first^data.charCodeAt(i),16777619);second=Math.imul(second,33)^data.charCodeAt(i);}
      return JSON.stringify([data.length,first,second]);
    };
    const path=n=>{const parts=[];for(let p=n;p;p=p.parentElement)parts.unshift(`${p.tagName.toLowerCase()}:nth-child(${[...p.parentElement?.children||[p]].indexOf(p)+1})`);return parts.join('>');};
    let capture=null;
    if(groups.length)try{
      const selectors=[...new Set(groups.flatMap(g=>g.captureNodes).map(path))];
      const expression=`(${captureState.toString()})(${JSON.stringify(selectors)},${JSON.stringify(sensitiveSelector)})`;
      if(expression.length<=524288)capture={expression,signature:captureState(selectors,sensitiveSelector)};
    }catch{/* Unsupported resources keep strict, masked frame validation. */}
    const lookup=node=>{
      if(disposed)fail();if(targets.has(node))return targets.get(node);
      if(node.matches('input[readonly][role="combobox"]')&&knownIds.has(node.id)||node.matches('[role="option"]')&&knownIds.has(node.closest('[role="listbox"]')?.id))fail();
      return null;
    };
    return Object.freeze({
      count:groups.length,capture,
      stable(){return groups.every(g=>{try{return g.check(g.input);}catch{return false;}});},
      check(node,after=false){const g=lookup(node);return g?g.check(node,after):false;},
      prepare(node){const g=lookup(node);if(!g)fail();g.prepare(node);},
      invalidate(){for(const g of groups)g.dispose();disposed=true;},
      dispose(){for(const g of groups)g.dispose();disposed=true;},
    });
  },{sensitiveSelector});
  const capture=await handle.evaluate(p=>p.capture);
  if(!capture){await handle.evaluate(p=>p.dispose());await handle.dispose();return null;}
  return {
    handle,version,fence,capture,
    async checkStyles(){await fence.barrier();if(fence.closed||fence.version!==version)throw new Error('frame target proof changed');},
    async dispose(){await handle.evaluate(p=>p.dispose()).catch(()=>{});await handle.dispose().catch(()=>{});},
    async abandon(){
      // The renderer may still be paused. Never evaluate cleanup JavaScript.
      fence.closed=true;let timer;
      try{await Promise.race([Promise.allSettled([handle.dispose(),fence.session.detach()]),new Promise(resolve=>{timer=setTimeout(resolve,1000);})]);}
      finally{clearTimeout(timer);}
    },
  };
}

// Pause only for capture, never for actuation. All cleanup is bounded and a
// restoration failure is explicit so the dispatcher can block further writes.
export async function captureProofFrame(proof) {
  const {session}=proof.fence;
  const bounded=async operation=>{let timer;try{return await Promise.race([operation,new Promise((_,reject)=>{timer=setTimeout(()=>reject(new Error('capture fence timed out')),3000);})]);}finally{clearTimeout(timer);}};
  let pauseResolve;const paused=new Promise(resolve=>{pauseResolve=resolve;});
  const onPause=event=>pauseResolve(event);session.once('Debugger.paused',onPause);
  let trigger;
  try {
    await bounded(session.send('Debugger.enable'));
    // Pause in our own empty script, not midway through a site's layout callback.
    trigger=session.send('Runtime.evaluate',{expression:'debugger;void 0',silent:true});void trigger.catch(()=>{});
    const event=await bounded(paused),callFrameId=event.callFrames[0]?.callFrameId;
    if(!callFrameId)throw new Error('capture fence unavailable');
    const check=async()=>{
      const state=await bounded(session.send('Debugger.evaluateOnCallFrame',{callFrameId,expression:proof.capture.expression,returnByValue:true,silent:true}));
      if(state.exceptionDetails||state.result.value!==proof.capture.signature)throw new Error('frame target proof changed');
      await bounded(proof.checkStyles());
    };
    await check();
    const image=await bounded(session.send('Page.captureScreenshot',{format:'jpeg',quality:60,captureBeyondViewport:false,fromSurface:true}));
    await check();return Buffer.from(image.data,'base64');
  } finally {
    session.off('Debugger.paused',onPause);
    let restored=true;
    try{await bounded(session.send('Debugger.resume'));}catch{restored=false;}
    try{await bounded(session.send('Debugger.disable'));}catch{restored=false;}
    if(trigger)await bounded(trigger).catch(()=>{restored=false;});
    if(!restored)throw new Error('capture fence restoration failed');
  }
}

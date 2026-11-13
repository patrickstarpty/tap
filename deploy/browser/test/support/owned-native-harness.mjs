// TEST ONLY: fixed synthetic fixtures, no production activation/export path.
// observe intentionally substitutes native input for the old DOM actuator.
// This proves controller protocol compatibility, NOT atomic target protection.
import {chromium} from '@playwright/test';
import {createLiveControl} from '../../live-control.mjs';

const fixtureIds=new Set(['basic','replaced','sensitive','post-effect-error','timeout','network','focus-sensitive']);
const allowed=new Map([
  ['click:#open',null],['click:#choice',null],['fill:#editor','owned editor'],
  ['fill:#text','owned result'],['press:#text','Enter'],['select:#select','premium'],
]);
const protectedTarget='input[type="password"],[autocomplete*="password"],[autocomplete="one-time-code"],[autocomplete^="cc-"],[data-secret],[data-sensitive]';
const html=`<button id="open">Choose product</button>
  <div id="choices" hidden><button id="choice">Premium</button></div>
  <output id="chosen"></output><input id="text"><output id="result"></output>
  <div id="editor" contenteditable="true"></div>
  <select id="select"><option value="basic">Basic</option><option value="premium">Premium</option></select>
  <script>
    window.pointerCount=0;window.pointerTrusted=false;
    document.querySelector('#open').addEventListener('pointerdown',event=>{
      window.pointerCount++;window.pointerTrusted=event.isTrusted;
      document.querySelector('#choices').hidden=false;
    });
    document.querySelector('#choice').addEventListener('click',()=>document.querySelector('#chosen').textContent='Premium');
    document.querySelector('#text').addEventListener('keydown',event=>{
      if(event.key==='Enter')document.querySelector('#result').textContent=event.target.value;
    });
  </script>`;

export async function createOwnedNativeHarness({fixtureId}) {
  if(!fixtureIds.has(fixtureId))throw Error('unknown owned fixture');
  const browser=await chromium.launch({headless:true});
  try {
    const context=await browser.newContext({acceptDownloads:false,serviceWorkers:'block'});
    let blockedRequests=0,nativeCalls=0,active=0,maxConcurrent=0,sequence=0;
    await context.route('**/*',route=>{blockedRequests++;return route.abort('blockedbyclient');});
    const page=await context.newPage();
    context.on('page',unexpected=>{if(unexpected!==page)void unexpected.close();});
    page.on('download',download=>void download.cancel());
    await page.setContent(html);
    const handles=new Map();
    for(const selector of new Set([...allowed.keys()].map(key=>key.slice(key.indexOf(':')+1)))) {
      handles.set(selector,await page.locator(selector).elementHandle());
    }
    if(fixtureId==='replaced')await page.locator('#open').evaluate(node=>node.replaceWith(node.cloneNode(true)));
    if(fixtureId==='sensitive')await page.locator('#open').evaluate(node=>node.setAttribute('data-sensitive',''));
    if(fixtureId==='timeout')await page.evaluate(()=>{
      const cover=document.createElement('div');
      cover.style='position:fixed;inset:0;background:white;z-index:9999';document.body.append(cover);
    });
    if(fixtureId==='focus-sensitive')await page.locator('#text').evaluate(node=>node.addEventListener('focus',()=>node.setAttribute('data-sensitive','')));
    if(fixtureId==='network')await page.evaluate(()=>fetch('https://example.com/owned-fixture-blocked').catch(()=>null));
    const validateTarget=async selector=>{
      const element=handles.get(selector);
      const valid=element&&await element.evaluate((node,{selector,protectedTarget})=>
        node.isConnected&&document.querySelectorAll(selector).length===1&&document.querySelector(selector)===node
        &&!node.matches(protectedTarget),{selector,protectedTarget});
      if(!valid)throw Error('fixture target rejected');
      return element;
    };
    const control=createLiveControl({page,observe:async(action,actuate)=>{
      if(action.type==='snapshot')return {sequence:++sequence,error:null};
      if(!actuate||action.actor_kind!=='agent')throw Error('fixture native actuator unavailable');
      const element=await validateTarget(action.selector);
      nativeCalls++;active++;maxConcurrent=Math.max(maxConcurrent,active);
      try {
        const options={timeout:500};
        if(action.type==='click')await element.click(options);
        else if(action.type==='fill')await element.fill(action.value,options);
        else if(action.type==='press')await element.press(action.key,options);
        else if(action.type==='select')await element.selectOption(action.value,options);
        else throw Error('unsupported fixture action');
        if(fixtureId==='post-effect-error')throw Error('owned observation failure after input');
        return {sequence:++sequence,error:null};
      }catch{return {sequence:++sequence,error:'native fixture outcome unknown'};}
      finally {active--;}
    }});
    return {
      dispatch:async action=>{
        if(action.actor_kind==='human')throw Error('human native unsupported');
        if(!['control_sync','finish'].includes(action.type)) {
          const key=`${action.type}:${action.selector}`;
          const expected=allowed.get(key);
          if(!allowed.has(key)||(expected!==null&&(action.type==='press'?action.key:action.value)!==expected))throw Error('unregistered fixture action');
        }
        const result=await control.dispatch(action);
        // Never publish the production shell's old atomic control_receipt as
        // native target proof. This harness has no AA/LCA evidence authority.
        return {sequence:result.sequence,error:result.error};
      },
      inspect:async()=>({profile:'owned_native_shell_compatibility',nativeCalls,maxConcurrent,blockedRequests,
        pages:context.pages().length,cookies:(await context.cookies()).length,
        ...await page.evaluate(()=>({pointerTrusted:window.pointerTrusted,pointerCount:window.pointerCount,
          choice:document.querySelector('#chosen').textContent,editor:document.querySelector('#editor').textContent,
          result:document.querySelector('#result').textContent,text:document.querySelector('#text').value,
          textSensitive:document.querySelector('#text').hasAttribute('data-sensitive'),selected:document.querySelector('#select').value})),
      }),
      dispose:()=>browser.close(),
    };
  }catch(error){await browser.close();throw error;}
}

// Owned geometry regression; no public sites or forced pointer actions.
import assert from 'node:assert/strict';
import test from 'node:test';
import {chromium} from '@playwright/test';
import {createLiveControl,preActuationFailure} from '../live-control.mjs';

async function fixture(t,{overlay='footer',mutation}={}) {
  const browser=await chromium.launch({headless:true});
  t.after(()=>browser.close());
  const page=await browser.newPage({viewport:{width:1280,height:800}});
  await page.setContent(`<style>body{margin:0} #go{height:40px;width:160px} #cover{position:fixed;bottom:0;left:0;width:100%;height:${overlay==='full'?'100%':'90px'};background:#aaa;z-index:100}</style><div style="height:1200px"></div><button id="go" onclick="window.clicks++">Continue</button><div style="height:1000px"></div>${overlay==='none'?'':'<div id="cover">Owned overlay</div>'}`);
  await page.evaluate(mutation=>{
    window.clicks=0;window.centerScrolls=0;
    addEventListener('scroll',()=>{
      if(scrollY<600)return;
      window.centerScrolls++;
      const node=document.querySelector('#go');
      if(mutation==='replace')node.replaceWith(node.cloneNode(true));
      if(mutation==='type')node.type='reset';
      if(mutation==='sensitive')node.setAttribute('data-sensitive','');
      if(mutation==='disabled')node.disabled=true;
      if(mutation==='aria-disabled')node.setAttribute('aria-disabled','true');
    });
  },mutation);
  const control=createLiveControl({page,observe:async(action,actuate)=>{
    if(actuate)await actuate();return {sequence:1,error:null};
  }});
  const click=()=>control.dispatch({type:'click',selector:'#go',actor_kind:'agent',control_epoch:0});
  return {page,click};
}

test('agent click uses one bounded center fallback below fixed footer, matching no-force Playwright',async t=>{
  const {page,click}=await fixture(t);
  await click();
  assert.equal(await page.evaluate(()=>window.clicks),1);
  assert.equal(await page.evaluate(()=>window.centerScrolls),1);
  const rect=await page.locator('#go').boundingBox();
  assert.ok(rect.y>0 && rect.y+rect.height<710);
  // Independent no-force comparison, same owned HTML/context viewport.
  await page.evaluate(()=>{window.clicks=0;scrollTo(0,0);});
  await page.locator('#go').click({timeout:5000});
  assert.equal(await page.evaluate(()=>window.clicks),1);
});

test('full overlay remains blocked after at most one center fallback',async t=>{
  const {page,click}=await fixture(t,{overlay:'full'});
  await assert.rejects(click(),error=>{
    assert.equal(preActuationFailure(error),null);
    return /target obscured or outside viewport/.test(error.message);
  });
  assert.equal(await page.evaluate(()=>window.clicks),0);
  assert.equal(await page.evaluate(()=>window.centerScrolls),1);
});

for(const mutation of ['replace','type','sensitive','disabled','aria-disabled']) test(`fallback ${mutation} change refuses without mutation`,async t=>{
  const {page,click}=await fixture(t,{mutation});
  await assert.rejects(click(),error=>{
    assert.equal(preActuationFailure(error),null);
    return /bound target|sensitive target|target not actionable/.test(error.message);
  });
  assert.equal(await page.evaluate(()=>window.clicks),0);
});

test('ordinary click without obstruction needs no center fallback and acts once',async t=>{
  const {page,click}=await fixture(t,{overlay:'none'});
  await click();
  assert.equal(await page.evaluate(()=>window.clicks),1);
  assert.equal(await page.evaluate(()=>window.centerScrolls),0);
});

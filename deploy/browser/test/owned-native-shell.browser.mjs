// Controlled protocol compatibility only. No production actuator is replaced.
import assert from 'node:assert/strict';
import test from 'node:test';
import {createOwnedNativeHarness} from './support/owned-native-harness.mjs';

const agent={actor_kind:'agent',control_epoch:0};
async function fixture(t,fixtureId='basic') {
  const harness=await createOwnedNativeHarness({fixtureId});
  t.after(()=>harness.dispose());
  return harness;
}

test('native shell preserves pointer, editor, keyboard and selection semantics',async t=>{
  const h=await fixture(t);
  for(const action of [
    {type:'click',selector:'#open'},
    {type:'click',selector:'#choice'},
    {type:'fill',selector:'#editor',value:'owned editor'},
    {type:'fill',selector:'#text',value:'owned result'},
    {type:'press',selector:'#text',key:'Enter'},
    {type:'select',selector:'#select',value:'premium'},
  ]) assert.equal((await h.dispatch({...agent,...action})).error,null);
  const state=await h.inspect();
  assert.equal(state.pointerTrusted,true);
  assert.equal(state.choice,'Premium');
  assert.equal(state.editor,'owned editor');
  assert.equal(state.result,'owned result');
  assert.equal(state.selected,'premium');
  assert.equal(state.nativeCalls,6);
  assert.equal(state.maxConcurrent,1);
  assert.equal(state.profile,'owned_native_shell_compatibility');
});

test('epoch transition waits behind native dispatch and fences queued old writes',async t=>{
  const h=await fixture(t);
  const results=await Promise.allSettled([
    h.dispatch({...agent,type:'click',selector:'#open'}),
    h.dispatch({type:'control_sync',controller_kind:'human',control_epoch:1}),
    h.dispatch({...agent,type:'fill',selector:'#text',value:'owned result'}),
  ]);
  assert.deepEqual(results.map(r=>r.status),['fulfilled','fulfilled','rejected']);
  assert.equal((await h.inspect()).nativeCalls,1);
  await assert.rejects(h.dispatch({actor_kind:'human',control_epoch:1,type:'click',selector:'#open'}),/human native unsupported/);
  assert.equal((await h.inspect()).nativeCalls,1);
});

for(const fixtureId of ['replaced','sensitive']) test(`${fixtureId} target rejects before native input`,async t=>{
  const h=await fixture(t,fixtureId);
  await assert.rejects(h.dispatch({...agent,type:'click',selector:'#open'}),/target/);
  assert.equal((await h.inspect()).nativeCalls,0);
});

test('non-fixture values, actions and destinations never enter native dispatch',async t=>{
  const h=await fixture(t);
  for(const action of [
    {type:'fill',selector:'#text',value:'not registered'},
    {type:'press',selector:'#text',key:'Escape'},
    {type:'navigate',url:'https://example.com'},
    {type:'click',selector:'#unknown'},
    {type:'evaluate',expression:'arbitrary'},
  ]) await assert.rejects(h.dispatch({...agent,...action}),/fixture action/);
  assert.equal((await h.inspect()).nativeCalls,0);
});

for(const fixtureId of ['post-effect-error','timeout']) test(`${fixtureId} permanently blocks without retry`,async t=>{
  const h=await fixture(t,fixtureId);
  const result=await h.dispatch({...agent,type:'click',selector:'#open'});
  assert.equal(result.error,'native fixture outcome unknown');
  assert.equal(Object.hasOwn(result,'control_receipt'),false);
  for(const action of [
    {...agent,type:'click',selector:'#open'},
    {type:'control_sync',controller_kind:'human',control_epoch:1},
    {...agent,type:'finish'},
  ]) await assert.rejects(h.dispatch(action),/blocked|control generation/);
  const state=await h.inspect();
  assert.equal(state.nativeCalls,1);
  assert.equal(state.pointerCount,fixtureId==='post-effect-error'?1:0);
});

test('fresh context blocks external requests and opens no other pages',async t=>{
  const h=await fixture(t,'network');
  const state=await h.inspect();
  assert.equal(state.blockedRequests,1);
  assert.equal(state.pages,1);
  assert.equal(state.cookies,0);
});


test('diagnostic limitation: native focus can protect a previously ordinary target before fill',async t=>{
  const h=await fixture(t,'focus-sensitive');
  const result=await h.dispatch({...agent,type:'fill',selector:'#text',value:'owned result'});
  const state=await h.inspect();
  assert.equal(result.error,null);
  assert.equal(state.textSensitive,true);
  assert.equal(state.text,'owned result');
  // This counterexample prevents treating pre-dispatch checks or the shell
  // receipt as preservation of the old atomic sensitivity guarantee.
});

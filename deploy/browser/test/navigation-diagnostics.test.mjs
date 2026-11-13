import assert from 'node:assert/strict';
import test from 'node:test';
import {createLiveControl} from '../live-control.mjs';

for (const [name, expected] of [
  ['TimeoutError', 'controlled navigation wait timeout'],
  ['Error', 'controlled navigation wait failed'],
]) test(`navigation ${name} keeps only a fixed diagnostic and blocks repeat writes`, async () => {
  let initiations=0, waits=0, sequence=0;
  const original=new Error('PRIVATE_URL_AND_QUERY_VALUE'); original.name=name;
  const page={on(){},waitForNavigation(options){waits++;assert.deepEqual(options,{waitUntil:'domcontentloaded',timeout:30000});return Promise.reject(original);},async evaluate(){initiations++;}};
  const control=createLiveControl({page,observe:async(_action,actuate)=>{if(actuate)await actuate();return {sequence:++sequence,error:null};}});
  await control.dispatch({type:'control_sync',controller_kind:'agent',control_epoch:1});
  const request={actor_kind:'agent',control_epoch:1,type:'navigate',url:'https://fixture.test/entry'};
  await assert.rejects(control.dispatch(request),error=>{assert.equal(error.message,expected);assert.equal(error.cause,undefined);assert.equal(error.stack.includes('PRIVATE_URL_AND_QUERY_VALUE'),false);return true;});
  await assert.rejects(control.dispatch(request),/stale or missing control generation/);
  await assert.rejects(control.dispatch({type:'control_sync',controller_kind:'agent',control_epoch:2}),/blocked/);
  assert.equal(initiations,1);assert.equal(waits,1);
});

test('navigation dispatch failure does not falsely identify navigation-wait failure',async()=>{
  let sequence=0;
  const original=new Error('dispatch failed before wait');
  const page={on(){},waitForNavigation(){return Promise.resolve();},async evaluate(){throw original;}};
  const control=createLiveControl({page,observe:async(_action,actuate)=>{if(actuate)await actuate();return {sequence:++sequence,error:null};}});
  await control.dispatch({type:'control_sync',controller_kind:'agent',control_epoch:1});
  await assert.rejects(control.dispatch({actor_kind:'agent',control_epoch:1,type:'navigate',url:'https://fixture.test/entry'}),error=>error===original);
});

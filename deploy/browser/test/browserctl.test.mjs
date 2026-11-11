import assert from 'node:assert/strict';
import test from 'node:test';
import { Readable } from 'node:stream';
import { validateAction, boundedObservation, readAction } from '../browserctl.mjs';

test('browserctl accepts only fixed inspector actions', () => {
  assert.deepEqual(validateAction({ type: 'click', selector: '#submit' }), { type: 'click', selector: '#submit' });
  assert.throws(() => validateAction({ type: 'evaluate', code: 'fetch("secret")' }), /unsupported/);
  assert.throws(() => validateAction({ type: 'click', selector: '#submit', code: 'x' }), /fields/);
  assert.throws(() => validateAction({ type: 'navigate', url: '../escape' }), /URL/);
});

test('bounded observation retains actual identity and explicitly marks truncation', () => {
  const value = boundedObservation({ session_id: 'session-a', sequence: 1, bounded_snapshot: 'a'.repeat(16000), fields: [], url: 'https://example.com', title: 'Example', error: null });
  assert.equal(value.session_id, 'session-a');
  assert.equal(value.sequence, 1);
  assert.equal(value.truncated, true);
  assert.ok(value.bounded_snapshot.length <= 5000);
});


test('large page observations keep usable controls instead of discarding all fields', () => {
  const fields = Array.from({length: 60}, (_, i) => ({tag:'button', label:`Choice ${i}`, selector:`#choice-${i}`, options: Array.from({length:30}, (_, j) => ({label:`Option ${j}`, value:String(j)}))}));
  const result = boundedObservation({session_id:'s',sequence:1,url:'https://example.com',title:'Quote',bounded_snapshot:'x'.repeat(16000),fields,error:'strict mode violation'});
  assert.ok(result.fields.length > 0);
  assert.equal(result.fields[0].selector, '#choice-0');
  assert.ok(Buffer.byteLength(JSON.stringify(result)) <= 12000);
  assert.equal(result.error, 'strict mode violation');
});

test('browser actions can carry a bounded business step without allowing arbitrary code', () => {
  const action = {type:'click', selector:'#cover', step:'choose-cover'};
  assert.deepEqual(validateAction(action),action);
  assert.throws(() => validateAction({...action,step:{code:'fetch(secret)'}}), /step/);
  assert.throws(() => validateAction({type:'plan', steps:[{id:'s',keyword:'Passed',title:'Claim success'}]}), /step/);
});


test('structured stdin preserves apostrophes and selectors without shell quoting', async () => {
  const action = { type: 'plan', steps: [{ id: 'age', keyword: 'When', title: "Read customer's age", expected_result: "Age field shows '30'" }] };
  const body = JSON.stringify(action);
  assert.deepEqual(await readAction(['--stdin'], Readable.from([body.slice(0, 17), body.slice(17)])), action);
  const fill = { type: 'fill', step: 'age', selector: '[id="combobox::age"]', value: '30' };
  assert.deepEqual(await readAction(['--stdin'], Readable.from([JSON.stringify(fill)])), fill);
  await assert.rejects(readAction(['--stdin'], Readable.from(['x'.repeat(16385)])), /size cap/);
  await assert.rejects(readAction(['--stdin'], Readable.from(['{"type":"evaluate","code":"x"}'])), /unsupported/);
});

test('business plans distinguish exact input proof from final result checks', () => {
  const input = {id:'age', keyword:'Then', title:'Confirm requested age', verification_kind:'input'};
  const plan = {type:'plan', steps:[input, {id:'quote',keyword:'Then',title:'Verify quote',verification_kind:'result'}]};
  assert.deepEqual(validateAction(plan), plan);
  assert.throws(() => validateAction({type:'plan',steps:[{...input,keyword:'When'}]}), /step/);
  assert.throws(() => validateAction({type:'plan',steps:[{...input,verification_kind:'skip'}]}), /step/);
});

test('plan error identifies And result verification and tells the agent how to fix it', () => {
  const plan = {type:'plan', steps:[
    {id:'step_1',keyword:'Given',title:'Open product page'},
    {id:'step_2',keyword:'When',title:'Select product'},
    {id:'step_3',keyword:'And',title:'Fill form'},
    {id:'step_4',keyword:'Then',title:'Verify inputs',verification_kind:'input'},
    {id:'step_5',keyword:'And',title:'Verify premium',verification_kind:'result'},
  ]};
  assert.throws(() => validateAction(plan), /step 5 \(id step_5\).*verification_kind.*Then.*change keyword to Then or remove verification_kind/i);
  assert.throws(() => validateAction({type:'plan',steps:[{id:'verify',keyword:'Then',title:'Verify',verification_kind:'skip'}]}), /step 1 \(id verify\).*verification_kind.*input.*result/);
});

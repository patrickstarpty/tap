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

import assert from 'node:assert/strict';
import test from 'node:test';
import { validateAction, boundedObservation } from '../browserctl.mjs';

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

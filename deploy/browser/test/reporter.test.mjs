import assert from 'node:assert/strict';
import test from 'node:test';
import { summarize } from '../reporter.mjs';

test('reporter does not accept zero or skipped tests as a pass', () => {
  assert.equal(summarize({ passed: 0, failed: 0, skipped: 1 }).status, 'failed');
  assert.equal(summarize({ passed: 0, failed: 0, skipped: 0 }).status, 'failed');
  assert.equal(summarize({ passed: 1, failed: 0, skipped: 0 }).status, 'passed');
});

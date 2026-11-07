import assert from 'node:assert/strict';
import test from 'node:test';
import TrustedReporter, { collectBusinessOutputs, boundedFailure } from '../reporter.mjs';

test('JSON tapper-result attachment survives as bounded structured output', () => {
  const body = Buffer.from(JSON.stringify({ quote: '北京', amount: '¥42' }));
  assert.deepEqual(collectBusinessOutputs([{ name: 'tapper-result', contentType: 'application/json', body }]), [{ quote: '北京', amount: '¥42' }]);
});

test('invalid and oversized tapper-result fail explicitly', () => {
  for (const body of [Buffer.from('{bad'), Buffer.alloc(32769, 0x20)]) {
    assert.throws(() => collectBusinessOutputs([{ name: 'tapper-result', contentType: 'application/json', body }]), /tapper-result/);
  }
  assert.throws(() => collectBusinessOutputs([{ name: 'tapper-result', contentType: 'text/plain', body: Buffer.from('{}') }]), /tapper-result/);
});

test('actual failure reason is bounded and removes host paths', () => {
  const message = boundedFailure({ message: 'Expected 42, received 17 at /private/tmp/secret/flow.spec.ts:12' });
  assert.match(message, /Expected 42, received 17/);
  assert.doesNotMatch(message, /\/private\/tmp/);
  assert.ok(Buffer.byteLength(message) <= 2000);
});

test('collection errors enter the failed receipt even when no tests ran', () => {
  const reporter = new TrustedReporter();
  reporter.onError({ message: 'Test has unknown parameter "testInfo" at /opt/browser/frozen/flow.spec.ts:11' });
  const receipt = reporter.buildReceipt([]);
  assert.equal(receipt.status, 'failed');
  assert.deepEqual(receipt.tests, []);
  assert.match(receipt.errors[0], /unknown parameter "testInfo"/);
  assert.doesNotMatch(receipt.errors[0], /\/opt\/browser\/frozen/);
});

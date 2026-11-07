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

test('aggregate business outputs over 32768 UTF-8 bytes cannot produce a passed receipt', () => {
  const reporter = new TrustedReporter();
  for (const index of [1, 2]) {
    reporter.onTestEnd({ title: `part ${index}` }, {
      status: 'passed', steps: [], error: null,
      attachments: [{ name: 'tapper-result', contentType: 'application/json', body: Buffer.from(JSON.stringify({ value: 'x'.repeat(20000) })) }],
    });
  }
  const receipt = reporter.buildReceipt([]);
  assert.equal(receipt.status, 'failed');
  assert.ok(Buffer.byteLength(JSON.stringify(receipt.outputs)) <= 32768);
  assert.match(receipt.tests[1].output_error, /32768/);
});

test('non-ASCII outputs use the same compact UTF-8 bound as Python dispatch', () => {
  const value = { value: '中'.repeat(6000) };
  assert.ok(Buffer.byteLength(JSON.stringify([value])) < 32768);
  assert.deepEqual(collectBusinessOutputs([{ name: 'tapper-result', contentType: 'application/json', body: Buffer.from(JSON.stringify(value)) }]), [value]);
});

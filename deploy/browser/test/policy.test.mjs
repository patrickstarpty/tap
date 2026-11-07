import assert from 'node:assert/strict';
import test from 'node:test';
import { isPublicIP, validateTarget, validateInnerRequest, resolvePublicIPv4 } from '../site-proxy.mjs';

test('proxy rejects private, metadata, and unauthorized destinations', () => {
  const allowed = ['https://example.com'];
  assert.throws(() => validateTarget('http://169.254.169.254/', allowed));
  assert.throws(() => validateTarget('http://127.0.0.1/', allowed));
  assert.throws(() => validateTarget('https://other.example/', allowed));
  assert.equal(validateTarget('https://example.com/path', allowed).hostname, 'example.com');
});

test('DNS result rejects private and IPv6 mapped addresses', () => {
  assert.equal(isPublicIP('169.254.169.254'), false);
  assert.equal(isPublicIP('192.168.1.1'), false);
  assert.equal(isPublicIP('::ffff:127.0.0.1'), false);
  assert.equal(isPublicIP('93.184.215.14'), true);
});

test('dual-stack name resolves only public A records and pins one address', async () => {
  let requested;
  const selected = await resolvePublicIPv4('www.selenium.dev', async (_host, options) => {
    requested = options;
    return [
      { address: '185.199.108.153', family: 4 },
      { address: '185.199.109.153', family: 4 },
    ];
  });
  assert.deepEqual(requested, { family: 4, all: true, verbatim: true });
  assert.equal(selected, '185.199.108.153');
});

test('DNS rejects mixed private A, no A, mapped and malformed answers', async () => {
  const resolve = rows => resolvePublicIPv4('example.com', async () => rows);
  await assert.rejects(resolve([{ address: '93.184.215.14', family: 4 }, { address: '169.254.169.254', family: 4 }]));
  await assert.rejects(resolve([]));
  await assert.rejects(resolve([{ address: '::ffff:127.0.0.1', family: 4 }]));
  await assert.rejects(resolve([{ address: '2606:4700:10::6814:179a', family: 6 }]));
  await assert.rejects(resolve([{ address: 'not-an-ip', family: 4 }]));
});

test('decrypted HTTPS authority and SNI must match the frozen CONNECT origin', () => {
  const outer = 'https://example.test';
  const allowed = [outer];
  assert.equal(validateInnerRequest({ method: 'GET', url: '/account', headers: { host: 'example.test' }, servername: 'example.test' }, outer, allowed), true);
  assert.throws(() => validateInnerRequest({ method: 'GET', url: '/account', headers: { host: 'other.test' }, servername: 'example.test' }, outer, allowed));
  assert.throws(() => validateInnerRequest({ method: 'GET', url: '/account', headers: { host: 'example.test' }, servername: 'other.test' }, outer, allowed));
  assert.throws(() => validateInnerRequest({ method: 'GET', url: 'https://other.test/account', headers: { host: 'example.test' }, servername: 'example.test' }, outer, allowed));
});

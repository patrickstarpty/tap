import assert from 'node:assert/strict';
import test from 'node:test';
import { isPublicIP, validateTarget, validateInnerRequest } from '../site-proxy.mjs';

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

test('decrypted HTTPS authority and SNI must match the frozen CONNECT origin', () => {
  const outer = 'https://example.test';
  const allowed = [outer];
  assert.equal(validateInnerRequest({ method: 'GET', url: '/account', headers: { host: 'example.test' }, servername: 'example.test' }, outer, allowed), true);
  assert.throws(() => validateInnerRequest({ method: 'GET', url: '/account', headers: { host: 'other.test' }, servername: 'example.test' }, outer, allowed));
  assert.throws(() => validateInnerRequest({ method: 'GET', url: '/account', headers: { host: 'example.test' }, servername: 'other.test' }, outer, allowed));
  assert.throws(() => validateInnerRequest({ method: 'GET', url: 'https://other.test/account', headers: { host: 'example.test' }, servername: 'example.test' }, outer, allowed));
});

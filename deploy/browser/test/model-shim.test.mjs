import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { mailboxRequest, createModelShim } from '../model-shim.mjs';

function fixture() {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'tapper-mailbox-'));
  fs.mkdirSync(path.join(root, 'requests'), { mode: 0o700 });
  fs.mkdirSync(path.join(root, 'responses'), { mode: 0o700 });
  fs.mkdirSync(path.join(root, 'acks'), { mode: 0o700 });
  return root;
}

test('mailbox stages request and ack inside writable children when parent is read-only', async () => {
  const root = fixture();
  fs.chmodSync(root, 0o500);
  const responder = setInterval(() => {
    for (const name of fs.readdirSync(path.join(root, 'requests'))) {
      fs.writeFileSync(path.join(root, 'responses', name), '{"ok":true}', { mode: 0o600 });
    }
  }, 5);
  try {
    assert.deepEqual(await mailboxRequest(root, 'browser', { marker: 'bounded' }, 1000), { ok: true });
    assert.equal(fs.readdirSync(path.join(root, 'acks')).length, 1);
  } finally {
    clearInterval(responder);
    fs.chmodSync(root, 0o700);
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test('mailbox request isolates concurrent IDs and acknowledges private responses', async () => {
  const root = fixture();
  try {
    const responder = setInterval(() => {
      for (const name of fs.readdirSync(path.join(root, 'requests'))) {
        const request = JSON.parse(fs.readFileSync(path.join(root, 'requests', name)));
        fs.writeFileSync(path.join(root, 'responses', name), JSON.stringify({ id: request.id, value: request.payload.marker }), { mode: 0o600 });
        fs.unlinkSync(path.join(root, 'requests', name));
      }
    }, 5);
    try {
      const [a, b] = await Promise.all([
        mailboxRequest(root, 'browser', { marker: 'a' }, 1000),
        mailboxRequest(root, 'browser', { marker: 'b' }, 1000),
      ]);
      assert.equal(a.value, 'a');
      assert.equal(b.value, 'b');
      assert.notEqual(a.id, b.id);
      assert.deepEqual(new Set(fs.readdirSync(path.join(root, 'acks'))), new Set([`${a.id}.json`, `${b.id}.json`]));
      assert.equal(fs.readdirSync(path.join(root, 'responses')).length, 2);
    } finally { clearInterval(responder); }
  } finally { fs.rmSync(root, { recursive: true, force: true }); }
});

test('model shim preserves status, content type and body through mailbox', async () => {
  const root = fixture();
  const server = createModelShim({ root, token: 'local-token', port: 0 });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const responder = setInterval(() => {
    for (const name of fs.readdirSync(path.join(root, 'requests'))) {
      const request = JSON.parse(fs.readFileSync(path.join(root, 'requests', name)));
      fs.writeFileSync(path.join(root, 'responses', name), JSON.stringify({ status: 429, contentType: 'application/json', bodyBase64: Buffer.from('{"error":"budget"}').toString('base64') }), { mode: 0o600 });
      fs.unlinkSync(path.join(root, 'requests', name));
      assert.equal(request.kind, 'model');
    }
  }, 5);
  try {
    const response = await fetch(`http://127.0.0.1:${server.address().port}/v1/chat/completions`, { method: 'POST', headers: { authorization: 'Bearer local-token' }, body: '{}' });
    assert.equal(response.status, 429);
    assert.equal(response.headers.get('content-type'), 'application/json');
    assert.equal(await response.text(), '{"error":"budget"}');
  } finally {
    clearInterval(responder);
    await new Promise(resolve => server.close(resolve));
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test('model shim enforces raw 8 MiB request and 1 MiB response limits', async () => {
  const root = fixture();
  const server = createModelShim({ root, token: 'local-token', port: 0 });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  let responseBytes = 1048576;
  const responder = setInterval(() => {
    for (const name of fs.readdirSync(path.join(root, 'requests'))) {
      const value = Buffer.alloc(responseBytes, 120);
      fs.writeFileSync(path.join(root, 'responses', name), JSON.stringify({ status: 200, contentType: 'application/json', bodyBase64: value.toString('base64') }), { mode: 0o600 });
      fs.unlinkSync(path.join(root, 'requests', name));
    }
  }, 5);
  try {
    const url = `http://127.0.0.1:${server.address().port}/v1/chat/completions`;
    const headers = { authorization: 'Bearer local-token' };
    const full = await fetch(url, { method: 'POST', headers, body: Buffer.alloc(8 * 1024 * 1024, 120) });
    assert.equal(full.status, 200);
    assert.equal((await full.arrayBuffer()).byteLength, 1048576);
    const oversizedRequest = await fetch(url, { method: 'POST', headers, body: Buffer.alloc(8 * 1024 * 1024 + 1, 120) });
    assert.equal(oversizedRequest.status, 413);
    responseBytes = 1048577;
    const oversizedResponse = await fetch(url, { method: 'POST', headers, body: '{}' });
    assert.equal(oversizedResponse.status, 502);
  } finally {
    clearInterval(responder);
    await new Promise(resolve => server.close(resolve));
    fs.rmSync(root, { recursive: true, force: true });
  }
});

for (const [elapsed, expectedStatus] of [[31000, 200], [61000, 502]]) {
  test(`model response at ${elapsed}ms obeys task deadline rather than a fixed 30s`, async t => {
    const root = fixture();
    let now = Date.now();
    const deadlineAt = now + 60000;
    t.mock.method(Date, 'now', () => now);
    const server = createModelShim({ root, token: 'local-token', port: 0, deadlineAt });
    await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
    const responder = setInterval(() => {
      for (const name of fs.readdirSync(path.join(root, 'requests')).filter(name => name.endsWith('.json'))) {
        now += elapsed;
        fs.writeFileSync(path.join(root, 'responses', name), JSON.stringify({
          status: 200, contentType: 'application/json', bodyBase64: Buffer.from('{"complete":true}').toString('base64'),
        }), { mode: 0o600 });
        fs.unlinkSync(path.join(root, 'requests', name));
      }
    }, 5);
    try {
      const response = await fetch(`http://127.0.0.1:${server.address().port}/v1/chat/completions`, {
        method: 'POST', headers: { authorization: 'Bearer local-token' }, body: '{}',
      });
      assert.equal(response.status, expectedStatus);
      if (expectedStatus === 200) {
        assert.equal(await response.text(), '{"complete":true}');
        assert.equal(fs.readdirSync(path.join(root, 'acks')).length, 1);
      } else {
        assert.equal(fs.readdirSync(path.join(root, 'acks')).length, 0);
      }
    } finally {
      clearInterval(responder);
      await new Promise(resolve => server.close(resolve));
      fs.rmSync(root, { recursive: true, force: true });
    }
  });
}

test('only local terminal budget errors stop SDK retries; provider 429 is preserved', async () => {
  const root = fixture();
  const server = createModelShim({root,token:'local-token',port:0});
  await new Promise(resolve => server.listen(0,'127.0.0.1',resolve));
  let local = true;
  const responder = setInterval(() => {
    for (const name of fs.readdirSync(path.join(root,'requests')).filter(x=>x.endsWith('.json'))) {
      const error = local ? {type:'tap_budget_exhausted',code:'budget_cost',retryable:false,message:'Task stopped: budget_cost'} : {type:'rate_limit_error',message:'try later'};
      fs.writeFileSync(path.join(root,'responses',name), JSON.stringify({status:429,contentType:'application/json',bodyBase64:Buffer.from(JSON.stringify({error})).toString('base64')}),{mode:0o600});
      fs.unlinkSync(path.join(root,'requests',name));
    }
  },5);
  try {
    const send = () => fetch(`http://127.0.0.1:${server.address().port}/v1/chat/completions`,{method:'POST',headers:{authorization:'Bearer local-token'},body:'{}'});
    const denied = await send();
    assert.equal(denied.status,402);
    assert.equal((await denied.json()).error.code,'budget_cost');
    local = false;
    assert.equal((await send()).status,429);
  } finally {
    clearInterval(responder);
    await new Promise(resolve=>server.close(resolve));
    fs.rmSync(root,{recursive:true,force:true});
  }
});

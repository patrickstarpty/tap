import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import net from 'node:net';
import http from 'node:http';
import { spawn } from 'node:child_process';
import { once } from 'node:events';
import test from 'node:test';

const root = async () => fs.mkdtemp('/tmp/tap-local-test-');
async function stop(child) {
  if (child.exitCode !== null) return;
  child.kill('SIGKILL');
  await once(child, 'close');
}

test('control client addresses the configured private socket', async () => {
  const dir = await root();
  const socket = `${dir}/inspector.sock`;
  const server = net.createServer(client => client.once('data', () => client.end('{"sequence":7}')));
  server.listen(socket);
  await once(server, 'listening');
  const child = spawn(process.execPath, [new URL('../ctl.mjs', import.meta.url).pathname], {
    env: {...process.env, TAP_BROWSER_SOCKET:socket},
  });
  let output = '';
  child.stdout.on('data', data => output += data);
  child.stdin.end('{"type":"snapshot"}');
  try {
    const [code] = await once(child, 'close');
    assert.equal(code, 0);
    assert.deepEqual(JSON.parse(output), {sequence:7});
  } finally {
    await stop(child);
    await new Promise(resolve => server.close(resolve));
    await fs.rm(dir, {recursive:true, force:true});
  }
});

test('local proxy uses owned loopback port and retains destination policy', async () => {
  const dir = await root();
  const fixture = http.createServer((_req, res) => res.end('fixture quote'));
  fixture.listen(0, '127.0.0.1');
  await once(fixture, 'listening');
  const child = spawn(process.execPath, [new URL('../site-proxy.mjs', import.meta.url).pathname], {
    env: {...process.env, TAP_BROWSER_LOCAL_DIR:dir,
      TAP_BROWSER_PROXY_POLICY:JSON.stringify({allowed_origins:['http://example.test'],
        test_target:`http://127.0.0.1:${fixture.address().port}`})},
  });
  child.stdout.resume(); child.stderr.resume();
  try {
    let port;
    for (let i = 0; i < 50; i++) {
      port = await fs.readFile(`${dir}/proxy-port`, 'utf8').catch(() => null);
      if (port) break;
      await new Promise(resolve => setTimeout(resolve, 20));
    }
    assert.ok(port, 'proxy must publish its private dynamic port');
    const request = raw => new Promise((resolve, reject) => {
      const req = http.get({host:'127.0.0.1', port:Number(port), path:raw,
        headers:{host:new URL(raw).host}}, res => {
        let body = ''; res.on('data', data => body += data);
        res.on('end', () => resolve({status:res.statusCode, body}));
      });
      req.on('error', reject);
    });
    assert.deepEqual(await request('http://example.test/quote'), {status:200, body:'fixture quote'});
    assert.equal((await request('http://127.0.0.1/private')).status, 403);
    assert.equal((await request('http://other.test/')).status, 403);
  } finally {
    await stop(child);
    await new Promise(resolve => fixture.close(resolve));
    await fs.rm(dir, {recursive:true, force:true});
  }
});

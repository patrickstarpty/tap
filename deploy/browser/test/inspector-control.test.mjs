import assert from 'node:assert/strict';
import net from 'node:net';
import { spawn } from 'node:child_process';
import { once } from 'node:events';
import test from 'node:test';

// Exercise the real control client against a slow socket response, without a model/browser.
test('an inspector response after 21 seconds survives the control client deadline', async () => {
  const server = net.createServer(socket => {
    socket.once('data', () => {
      const timer = setTimeout(() => socket.end('{"status":"completed"}'), 21_000);
      socket.on('close', () => clearTimeout(timer));
    });
  });
  // listen fails if another owner already has this socket; never remove their file.
  server.listen('/tmp/inspector.sock');
  await once(server, 'listening');
  try {
    const child = spawn(process.execPath, [new URL('../ctl.mjs', import.meta.url).pathname]);
    let output = '';
    let error = '';
    child.stdout.on('data', data => output += data);
    child.stderr.on('data', data => error += data);
    child.stdin.end('{"type":"snapshot"}');
    const [code] = await once(child, 'close');
    assert.equal(code, 0, error);
    assert.deepEqual(JSON.parse(output), {status:'completed'});
  } finally {
    await new Promise(resolve => server.close(resolve));
  }
});

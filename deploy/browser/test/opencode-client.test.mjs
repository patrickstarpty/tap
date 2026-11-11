import assert from 'node:assert/strict';
import crypto from 'node:crypto';
import { spawn } from 'node:child_process';
import test from 'node:test';
import { terminalMessages, terminalFailure, linkedChildren, validateFrozenInput, waitForDaemon, waitForServeReadiness } from '../opencode-client.mjs';

const requiredServePaths = ['/session', '/session/{sessionID}/message', '/session/{sessionID}/prompt_async',
  '/session/{sessionID}/children', '/session/{sessionID}/abort', '/event'];

test('serve may become ready after fifteen seconds while staying within its bounded window', async () => {
  let now = 0;
  let probes = 0;
  await waitForServeReadiness({ exitCode: null, signalCode: null }, 60_000, {
    now: () => now,
    pause: async () => { now += 1000; },
    probe: async timeoutMs => {
      probes += 1;
      assert.ok(0 < timeoutMs && timeoutMs <= 5000);
      if (now < 20_000) throw new Error('not ready');
      return { status: 200, value: { openapi: '3.1.0', paths: Object.fromEntries(requiredServePaths.map(path => [path, {}])) } };
    },
  });
  assert.equal(now, 20_000);
  assert.equal(probes, 21);
});

test('serve readiness cannot outlive the absolute task deadline', async () => {
  let now = 0;
  await assert.rejects(waitForServeReadiness({ exitCode: null, signalCode: null }, 9000, {
    now: () => now,
    pause: async () => { now += 1000; },
    probe: async () => { throw new Error('not ready'); },
  }), /coding daemon deadline exceeded/);
  assert.equal(now, 9000);
});

test('a document arriving after the readiness edge is rejected and its probe is clipped', async () => {
  let now = 0;
  const timeouts = [];
  await assert.rejects(waitForServeReadiness({ exitCode: null, signalCode: null }, 60_000, {
    now: () => now,
    pause: async () => { now = Math.max(now, 29_900); },
    probe: async timeoutMs => {
      timeouts.push(timeoutMs);
      if (timeouts.length === 1) throw new Error('not ready');
      now += 200;
      return { status: 200, value: { openapi: '3.1.0', paths: Object.fromEntries(requiredServePaths.map(path => [path, {}])) } };
    },
  }), /OpenCode serve readiness deadline/);
  assert.deepEqual(timeouts, [5000, 100]);
});

test('daemon deadline terminates its independent process group', async () => {
  const child = spawn('/bin/sh', ['-c', 'sleep 30 & wait'], { detached: true, stdio: 'ignore' });
  try {
    const started = Date.now();
    const expired = await waitForDaemon(child, started + 150);
    assert.equal(expired, true);
    assert.ok(Date.now() - started < 3000);
    assert.throws(() => process.kill(-child.pid, 0), { code: 'ESRCH' });
  } finally {
    try { process.kill(-child.pid, 'SIGKILL'); } catch {}
  }
});

test('coding input requires exact frozen bytes and digest', () => {
  const data = Buffer.from('{"notes":"customer=Ada; region=west"}');
  const digest = crypto.createHash('sha256').update(data).digest('hex');
  assert.equal(validateFrozenInput(data, digest), data);
  assert.throws(() => validateFrozenInput(Buffer.from('{"notes":"other"}'), digest), /frozen input/);
  assert.throws(() => validateFrozenInput(Buffer.from('[]'), crypto.createHash('sha256').update('[]').digest('hex')), /frozen input/);
  assert.throws(() => validateFrozenInput(Buffer.alloc(32769), digest), /frozen input/);
});

test('current user and assistant stop plus completed tools are required', () => {
  const user = { info: { id: 'msg_current', role: 'user' }, parts: [] };
  const assistant = { info: { id: 'msg_assistant', role: 'assistant', parentID: 'msg_current', finish: 'stop' },
    parts: [{ type: 'tool', state: { status: 'completed' } }] };
  assert.equal(terminalMessages([user, assistant], 'msg_current'), 'complete');
  assert.equal(terminalMessages([user, { ...assistant, parts: [{ type: 'tool', state: { status: 'running' } }] }], 'msg_current'), 'incomplete');
  assert.equal(terminalMessages([user, { ...assistant, info: { ...assistant.info, error: { name: 'ProviderError' } } }], 'msg_current'), 'failed');
  assert.equal(terminalMessages([user, { ...assistant, info: { ...assistant.info, parentID: 'old' } }], 'msg_current'), 'incomplete');
});

test('historical tool error may be recovered only by a final assistant stop', () => {
  const user = { info: { id: 'msg_current', role: 'user' }, parts: [] };
  const toolError = { info: { id: 'msg_error', role: 'assistant', parentID: 'msg_current' },
    parts: [{ type: 'tool', tool: 'read', state: { status: 'error', error: 'File not found' } }] };
  const completed = { info: { id: 'msg_completed', role: 'assistant', parentID: 'msg_current' },
    parts: [{ type: 'tool', tool: 'read', state: { status: 'completed' } }] };
  const final = { info: { id: 'msg_final', role: 'assistant', parentID: 'msg_current', finish: 'stop' }, parts: [] };
  assert.equal(terminalMessages([user, toolError, completed, final], 'msg_current'), 'complete');
  assert.equal(terminalMessages([user, toolError, completed], 'msg_current'), 'incomplete');
  assert.equal(terminalMessages([user, toolError, { ...completed, parts: [{ type: 'tool', state: { status: 'running' } }] }, final], 'msg_current'), 'incomplete');
  assert.equal(terminalMessages([user, { ...toolError, info: { ...toolError.info, parentID: 'old' } }, final], 'msg_current'), 'incomplete');
  const assistantError = { ...final, info: { ...final.info, error: { name: 'ProviderError', message: 'private body' } } };
  assert.equal(terminalMessages([user, toolError, assistantError], 'msg_current'), 'failed');
  assert.deepEqual(terminalFailure([user, toolError, assistantError], 'msg_current'), {
    category: 'assistant_error', tool: null, summary: 'OpenCode assistant reported an error.',
  });
});

test('new children require linkage to actual task tool result', () => {
  const assistant = { info: { role: 'assistant', parentID: 'msg_current' }, parts: [{ type: 'tool', tool: 'task',
    state: { status: 'completed', metadata: { sessionId: 'ses_child' } } }] };
  assert.deepEqual(linkedChildren([assistant], [{ id: 'ses_child', parentID: 'ses_root' }], 'ses_root', new Set()), ['ses_child']);
  assert.equal(linkedChildren([], [{ id: 'ses_child', parentID: 'ses_root' }], 'ses_root', new Set()), null);
});

test('failed terminal classifies tool error without persisting raw output or secrets', () => {
  const user = { info: { id: 'msg_current', role: 'user' }, parts: [] };
  const failed = { info: { id: 'msg_assistant', role: 'assistant', parentID: 'msg_current' },
    parts: [{ type: 'tool', tool: 'bash', state: { status: 'error', error: 'cat: /opt/browser/frozen/inputs.json: No such file or directory; sk-private' } }] };
  assert.deepEqual(terminalFailure([user, failed], 'msg_current'), {
    category: 'missing_frozen_input', tool: 'bash', summary: 'Frozen browser inputs are unavailable in the coding container.',
  });
  assert.deepEqual(terminalFailure([user, { ...failed, parts: [{ type: 'tool', tool: 'bash', state: { status: 'error', error: 'File not found: /opt/browser/frozen/inputs.json' } }] }], 'msg_current'), {
    category: 'missing_frozen_input', tool: 'bash', summary: 'Frozen browser inputs are unavailable in the coding container.',
  });
  assert.deepEqual(terminalFailure([user, { ...failed, parts: [{ type: 'tool', tool: 'bad secret=abc', state: { status: 'error', error: 'sk-private' } }] }], 'msg_current'), {
    category: 'tool_error', tool: 'unknown', summary: 'OpenCode tool reported an error.',
  });
});

test('failed terminal classifies assistant error without persisting provider body', () => {
  const user = { info: { id: 'msg_current', role: 'user' }, parts: [] };
  const failed = { info: { id: 'msg_assistant', role: 'assistant', parentID: 'msg_current', error: { name: 'ProviderError', message: 'sk-private' } }, parts: [] };
  assert.deepEqual(terminalFailure([user, failed], 'msg_current'), {
    category: 'assistant_error', tool: null, summary: 'OpenCode assistant reported an error.',
  });
  assert.equal(terminalFailure([failed, user], 'msg_current'), null);
});

import { parseInputRequest } from '../opencode-client.mjs';

test('blocked outcome is bounded and cannot be interpreted as script success', () => {
  assert.deepEqual(parseInputRequest('{"reason":"missing_input","question":"Which account?"}'), { reason: 'missing_input', question: 'Which account?' });
  assert.throws(() => parseInputRequest('{"reason":"passed","question":"done"}'), /input request/);
  assert.throws(() => parseInputRequest(JSON.stringify({ reason: 'external_block', question: 'x'.repeat(2001) })), /input request/);
});


test('native compaction continues the same task and summary alone is not completion', () => {
  const original = {info:{id:'u1',role:'user'},parts:[{type:'text',text:'original goal'}]};
  const action = {info:{id:'a1',role:'assistant',parentID:'u1',finish:'tool-calls'},parts:[]};
  const compact = {info:{id:'u2',role:'user'},parts:[{type:'compaction',auto:true}]};
  const summary = {info:{id:'a2',role:'assistant',parentID:'u2',summary:true,finish:'stop'},parts:[]};
  const resume = {info:{id:'u3',role:'user'},parts:[{type:'text',synthetic:true,metadata:{compaction_continue:true},text:'Continue'}]};
  const final = {info:{id:'a3',role:'assistant',parentID:'u3',finish:'stop'},parts:[]};
  const history = [original,action,compact,summary,resume,final];
  assert.equal(terminalMessages(history.slice(0,4),'u1'), 'incomplete');
  assert.equal(terminalMessages(history,'u1'), 'complete');
  const failed = {...final,info:{...final.info,error:{name:'ProviderError'}}};
  assert.equal(terminalMessages([...history.slice(0,5),failed],'u1'),'failed');
  assert.equal(terminalFailure([...history.slice(0,5),failed],'u1').category,'assistant_error');
  const foreign = {...resume,parts:[{type:'text',text:'another task'}]};
  assert.equal(terminalMessages([...history.slice(0,4),foreign,final],'u1'),'incomplete');
});

test('local session creation can exceed five seconds without being retried', async () => {
  const http = await import('node:http');
  const { api } = await import('../opencode-client.mjs');
  let requests = 0;
  const server = http.createServer((_request, response) => {
    requests += 1;
    setTimeout(() => response.writeHead(200, { 'content-type': 'application/json' }).end('{"id":"ses_slow"}'), 5200);
  });
  const previous = process.env.OPENCODE_SERVER_PASSWORD;
  process.env.OPENCODE_SERVER_PASSWORD = 'test-local-password';
  await new Promise(resolve => server.listen(39002, '127.0.0.1', resolve));
  try {
    assert.deepEqual(await api('POST', '/session', {}), { status: 200, value: { id: 'ses_slow' } });
    assert.equal(requests, 1);
    await assert.rejects(api('POST', '/session', {}, 25), /POST \/session timed out/);
    assert.equal(requests, 2);
  } finally {
    if (previous === undefined) delete process.env.OPENCODE_SERVER_PASSWORD;
    else process.env.OPENCODE_SERVER_PASSWORD = previous;
    server.closeAllConnections();
    await new Promise(resolve => server.close(resolve));
  }
});

test('terminal budget reason is reported without raw provider data', () => {
  const user = {info:{id:'u',role:'user'},parts:[]};
  const failure = {info:{role:'assistant',parentID:'u',error:{name:'APIError',data:{statusCode:402,responseBody:JSON.stringify({error:{type:'tap_budget_exhausted',code:'budget_cost',retryable:false,message:'private'}})}}},parts:[]};
  assert.deepEqual(terminalFailure([user,failure],'u'),{category:'budget_cost',tool:null,summary:'Task stopped: budget_cost'});
});

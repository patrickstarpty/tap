import assert from 'node:assert/strict';
import test from 'node:test';
import { terminalMessages, linkedChildren } from '../opencode-client.mjs';

test('current user and assistant stop plus completed tools are required', () => {
  const user = { info: { id: 'msg_current', role: 'user' }, parts: [] };
  const assistant = { info: { id: 'msg_assistant', role: 'assistant', parentID: 'msg_current', finish: 'stop' },
    parts: [{ type: 'tool', state: { status: 'completed' } }] };
  assert.equal(terminalMessages([user, assistant], 'msg_current'), 'complete');
  assert.equal(terminalMessages([user, { ...assistant, parts: [{ type: 'tool', state: { status: 'running' } }] }], 'msg_current'), 'incomplete');
  assert.equal(terminalMessages([user, { ...assistant, info: { ...assistant.info, error: { name: 'ProviderError' } } }], 'msg_current'), 'failed');
  assert.equal(terminalMessages([user, { ...assistant, info: { ...assistant.info, parentID: 'old' } }], 'msg_current'), 'incomplete');
});

test('new children require linkage to actual task tool result', () => {
  const assistant = { info: { role: 'assistant', parentID: 'msg_current' }, parts: [{ type: 'tool', tool: 'task',
    state: { status: 'completed', metadata: { sessionId: 'ses_child' } } }] };
  assert.deepEqual(linkedChildren([assistant], [{ id: 'ses_child', parentID: 'ses_root' }], 'ses_root', new Set()), ['ses_child']);
  assert.equal(linkedChildren([], [{ id: 'ses_child', parentID: 'ses_root' }], 'ses_root', new Set()), null);
});

import { parseInputRequest } from '../opencode-client.mjs';

test('blocked outcome is bounded and cannot be interpreted as script success', () => {
  assert.deepEqual(parseInputRequest('{"reason":"missing_input","question":"Which account?"}'), { reason: 'missing_input', question: 'Which account?' });
  assert.throws(() => parseInputRequest('{"reason":"passed","question":"done"}'), /input request/);
  assert.throws(() => parseInputRequest(JSON.stringify({ reason: 'external_block', question: 'x'.repeat(2001) })), /input request/);
});

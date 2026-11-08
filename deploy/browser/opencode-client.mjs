import crypto from 'node:crypto';
import fs from 'node:fs';
import { spawn, spawnSync } from 'node:child_process';
import { pathToFileURL } from 'node:url';
import { createModelShim } from './model-shim.mjs';

const PORT = 39002;
const ORIGIN = `http://127.0.0.1:${PORT}`;
const MAX_RESPONSE = 1048576;
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

export function parseInputRequest(raw) {
  let value;
  try { value = JSON.parse(raw); } catch { throw new Error('invalid input request'); }
  if (!value || !['missing_input', 'external_block'].includes(value.reason) ||
      typeof value.question !== 'string' || !value.question.trim() ||
      Buffer.byteLength(value.question) > 2000 || Object.keys(value).some(key => !['reason', 'question'].includes(key))) {
    throw new Error('invalid input request');
  }
  return value;
}

export function validateFrozenInput(data, digest) {
  if (!Buffer.isBuffer(data) || !data.length || data.length > 32768 ||
      typeof digest !== 'string' || !/^[a-f0-9]{64}$/.test(digest) ||
      crypto.createHash('sha256').update(data).digest('hex') !== digest) {
    throw new Error('invalid frozen input');
  }
  let value;
  try { value = JSON.parse(data.toString('utf8')); } catch { throw new Error('invalid frozen input'); }
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('invalid frozen input');
  return data;
}

export function terminalMessages(messages, expectedUserId) {
  if (!Array.isArray(messages)) return 'incomplete';
  const start = messages.findIndex(item => item?.info?.role === 'user' && item.info.id === expectedUserId);
  if (start < 0) return 'incomplete';
  let assistant = false;
  for (const item of messages.slice(start + 1)) {
    if (item?.info?.role !== 'assistant' || item.info.parentID !== expectedUserId || !Array.isArray(item.parts)) return 'incomplete';
    if (item.info.error) return 'failed';
    if (item.parts.some(part => part?.type === 'tool' && !['completed', 'error'].includes(part.state?.status))) return 'incomplete';
    assistant = item.info.finish === 'stop';
  }
  return assistant ? 'complete' : 'incomplete';
}

export function terminalFailure(messages, expectedUserId) {
  if (!Array.isArray(messages)) return null;
  const start = messages.findIndex(item => item?.info?.role === 'user' && item.info.id === expectedUserId);
  if (start < 0) return null;
  for (const item of messages.slice(start + 1)) {
    if (item?.info?.role === 'assistant' && item.info.parentID === expectedUserId && item.info.error) {
      return { category: 'assistant_error', tool: null, summary: 'OpenCode assistant reported an error.' };
    }
  }
  for (const item of messages.slice(start + 1)) {
    if (item?.info?.role !== 'assistant' || item.info.parentID !== expectedUserId) continue;
    for (const part of item?.parts || []) {
      if (part?.type !== 'tool' || part.state?.status !== 'error') continue;
      const tool = typeof part.tool === 'string' && /^[a-z][a-z0-9_-]{0,39}$/.test(part.tool) ? part.tool : 'unknown';
      const error = typeof part.state.error === 'string' ? part.state.error : '';
      if (error.includes('/opt/browser/frozen/inputs.json') && /ENOENT|No such file or directory|File not found/i.test(error)) {
        return { category: 'missing_frozen_input', tool, summary: 'Frozen browser inputs are unavailable in the coding container.' };
      }
      if (/EACCES|permission denied/i.test(error)) return { category: 'permission_denied', tool, summary: 'OpenCode tool was denied access.' };
      if (/timed? out|timeout/i.test(error)) return { category: 'tool_timeout', tool, summary: 'OpenCode tool timed out.' };
      return { category: 'tool_error', tool, summary: 'OpenCode tool reported an error.' };
    }
  }
  return null;
}

export function linkedChildren(messages, children, rootId, baseline) {
  if (!Array.isArray(children) || children.some(child => child?.parentID !== rootId || typeof child.id !== 'string')) return null;
  const linked = new Set();
  for (const item of messages) {
    if (item?.info?.role !== 'assistant') continue;
    for (const part of item.parts || []) {
      if (part?.type === 'tool' && part.tool === 'task' && typeof part.state?.metadata?.sessionId === 'string') {
        linked.add(part.state.metadata.sessionId);
      }
    }
  }
  const fresh = children.filter(child => !baseline.has(child.id));
  if (fresh.some(child => !linked.has(child.id))) return null;
  return fresh.map(child => child.id);
}

function credentials() {
  const password = process.env.OPENCODE_SERVER_PASSWORD;
  if (!password) throw new Error('serve password missing');
  return 'Basic ' + Buffer.from(`opencode:${password}`).toString('base64');
}

async function api(method, route, body, timeoutMs = 5000) {
  const response = await fetch(ORIGIN + route, { method, headers: { authorization: credentials(), 'content-type': 'application/json' },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }), signal: AbortSignal.timeout(timeoutMs) });
  const raw = Buffer.from(await response.arrayBuffer());
  if (raw.length > MAX_RESPONSE) throw new Error('serve response exceeds cap');
  return { status: response.status, value: raw.length ? JSON.parse(raw.toString('utf8')) : null };
}

function verifiedIdentity() {
  const info = JSON.parse(fs.readFileSync('/workspace/serve.json', 'utf8'));
  if (!Number.isInteger(info.pid) || info.pid < 1 || !/^[a-f0-9]{32}$/.test(info.nonce)) throw new Error('invalid serve identity');
  const command = fs.readFileSync(`/proc/${info.pid}/cmdline`, 'utf8');
  if (!command.includes('/opt/opencode/opencode') || !command.includes('serve') || !command.includes('--pure')) throw new Error('serve identity no longer owns process');
  return info;
}

export async function waitForDaemon(child, deadlineMs) {
  let expired = false;
  let grace;
  const signalGroup = signal => {
    try { process.kill(-child.pid, signal); } catch (error) {
      if (error.code !== 'ESRCH') throw error;
    }
  };
  const timer = setTimeout(() => {
    expired = true;
    signalGroup('SIGTERM');
    grace = setTimeout(() => signalGroup('SIGKILL'), 2000);
  }, Math.max(0, deadlineMs - Date.now()));
  try {
    if (child.exitCode === null) {
      await new Promise((resolve, reject) => {
        child.once('exit', resolve);
        child.once('error', reject);
      });
    }
    return expired;
  } finally {
    clearTimeout(timer);
    if (grace) clearTimeout(grace);
    signalGroup('SIGKILL');
  }
}

export async function waitForServeReadiness(child, absoluteDeadline, {
  now = Date.now,
  probe = timeoutMs => api('GET', '/doc', undefined, timeoutMs),
  pause = sleep,
} = {}) {
  const readinessDeadline = Math.min(now() + 30000, absoluteDeadline);
  const required = ['/session', '/session/{sessionID}/message', '/session/{sessionID}/prompt_async', '/session/{sessionID}/children', '/session/{sessionID}/abort', '/event'];
  while (now() < readinessDeadline) {
    if (child.exitCode !== null || child.signalCode !== null) throw new Error('OpenCode serve exited before readiness');
    try {
      const remaining = readinessDeadline - now();
      if (remaining <= 0) break;
      const document = await probe(Math.max(1, Math.min(5000, remaining)));
      if (document.status === 200 && document.value?.openapi === '3.1.0' &&
          required.every(item => Object.hasOwn(document.value.paths || {}, item)) && now() < readinessDeadline) return;
    } catch {}
    await pause(Math.min(50, Math.max(0, readinessDeadline - now())));
  }
  if (now() >= absoluteDeadline) throw new Error('coding daemon deadline exceeded');
  throw new Error('OpenCode serve readiness deadline');
}

async function daemon() {
  const seconds = Number(process.env.TAP_CODING_DEADLINE_SECONDS);
  if (!Number.isInteger(seconds) || seconds < 1 || seconds > 86400) throw new Error('coding daemon deadline invalid');
  const absoluteDeadline = Date.now() + seconds * 1000;
  const version = spawnSync('/opt/opencode/opencode', ['--version'], { encoding: 'utf8', timeout: 10000 });
  if (version.status !== 0 || version.stdout.trim() !== '1.19.5') throw new Error('OpenCode version mismatch');
  const shim = createModelShim({ root: '/mailbox', token: process.env.TAP_MODEL_SHIM_TOKEN, deadlineAt: absoluteDeadline });
  await new Promise(resolve => shim.listen(39001, '127.0.0.1', resolve));
  const child = spawn('/opt/opencode/opencode', ['serve', '--pure', '--hostname', '127.0.0.1', '--port', String(PORT)],
    { cwd: '/workspace', env: process.env, detached: true, stdio: ['ignore', 'pipe', 'pipe'] });
  child.stdout.resume();
  child.stderr.resume();
  const lifetime = waitForDaemon(child, absoluteDeadline);
  const shutdown = () => { try { process.kill(-child.pid, 'SIGTERM'); } catch {} };
  process.on('SIGTERM', shutdown);
  process.on('SIGINT', shutdown);
  try {
  await waitForServeReadiness(child, absoluteDeadline);
  fs.writeFileSync('/workspace/serve.json', JSON.stringify({ pid: child.pid, nonce: crypto.randomBytes(16).toString('hex'), version: '1.19.5' }), { flag: 'wx', mode: 0o600 });
  if (await lifetime) throw new Error('coding daemon deadline exceeded');
  } finally {
    process.off('SIGTERM', shutdown);
    process.off('SIGINT', shutdown);
    shutdown();
    await lifetime;
    shim.close();
  }
}

async function run() {
  verifiedIdentity();
  const goal = fs.readFileSync('/workspace/goal.txt', 'utf8');
  if (!goal || Buffer.byteLength(goal) > 20000) throw new Error('goal size invalid');
  let sessionId;
  if (fs.existsSync('/workspace/session.json')) {
    sessionId = JSON.parse(fs.readFileSync('/workspace/session.json', 'utf8')).session_id;
    if (!/^ses_[A-Za-z0-9_-]{1,100}$/.test(sessionId || '')) throw new Error('saved session identity invalid');
  } else {
    const created = await api('POST', '/session', {});
    if (created.status !== 200 || !/^ses_[A-Za-z0-9_-]{1,100}$/.test(created.value?.id || '')) throw new Error('session create failed');
    sessionId = created.value.id;
    fs.writeFileSync('/workspace/session.json', JSON.stringify({ session_id: sessionId }), { flag: 'wx', mode: 0o600 });
  }
  const initial = await api('GET', `/session/${sessionId}/children`);
  if (initial.status !== 200 || !Array.isArray(initial.value)) throw new Error('child baseline unavailable');
  const baseline = new Set(initial.value.map(item => item.id));
  const messageId = 'msg_' + crypto.randomBytes(12).toString('hex');
  const idle = new Set();
  let assistantSeen = false;
  const controller = new AbortController();
  const stream = await fetch(ORIGIN + '/event', { headers: { authorization: credentials() }, signal: controller.signal });
  if (stream.status !== 200 || !stream.body) throw new Error('event stream unavailable');
  const watch = (async () => {
    const reader = stream.body.getReader();
    const decoder = new TextDecoder();
    let pending = '';
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      pending += decoder.decode(value, { stream: true });
      for (let end = pending.indexOf('\n\n'); end >= 0; end = pending.indexOf('\n\n')) {
        const frame = pending.slice(0, end);
        pending = pending.slice(end + 2);
        const line = frame.split('\n').find(item => item.startsWith('data:'));
        if (!line) continue;
        let event;
        try { event = JSON.parse(line.slice(5).trim()); } catch { continue; }
        const props = event.properties || {};
        if (event.type === 'message.updated' && props.info?.sessionID === sessionId && props.info?.parentID === messageId && props.info?.role === 'assistant') assistantSeen = true;
        if (assistantSeen && (event.type === 'session.idle' || (event.type === 'session.status' && props.status?.type === 'idle')) && typeof props.sessionID === 'string') idle.add(props.sessionID);
      }
    }
  })().catch(() => {});
  const deadline = Date.now() + Number(process.env.TAP_CODING_DEADLINE_SECONDS || 120) * 1000;
  try {
    const admitted = await api('POST', `/session/${sessionId}/prompt_async`, { parts: [{ type: 'text', text: goal }], messageID: messageId });
    if (admitted.status !== 204) throw new Error('prompt admission failed');
    while (Date.now() < deadline) {
      const [messages, rawChildren] = await Promise.all([api('GET', `/session/${sessionId}/message`), api('GET', `/session/${sessionId}/children`)]);
      if (messages.status !== 200 || rawChildren.status !== 200) throw new Error('session observation unavailable');
      const children = linkedChildren(messages.value, rawChildren.value, sessionId, baseline);
      if (children !== null) {
        const root = terminalMessages(messages.value, messageId);
        if (root === 'failed') return { session_id: sessionId, message_id: messageId, terminal: 'failed',
          failure: terminalFailure(messages.value, messageId) || { category: 'unknown', tool: null, summary: 'OpenCode failed without classified error.' } };
        if (root === 'complete' && idle.has(sessionId)) {
          let finished = true;
          for (const childId of children) {
            const childMessages = await api('GET', `/session/${childId}/message`);
            const user = childMessages.value?.filter(item => item?.info?.role === 'user').at(-1)?.info?.id;
            if (childMessages.status !== 200 || !idle.has(childId) || !user || terminalMessages(childMessages.value, user) !== 'complete') finished = false;
          }
          if (finished) return { session_id: sessionId, message_id: messageId, terminal: 'complete' };
        }
      }
      await sleep(200);
    }
    await api('POST', `/session/${sessionId}/abort`, {});
    return { session_id: sessionId, message_id: messageId, terminal: 'incomplete' };
  } finally {
    controller.abort();
    await watch;
  }
}

function collect() {
  verifiedIdentity();
  const result = {};
  const scriptPath = '/workspace/flow.spec.ts';
  if (fs.existsSync(scriptPath)) {
    const stat = fs.lstatSync(scriptPath);
    if (!stat.isFile() || stat.isSymbolicLink() || stat.size < 1 || stat.size > 256000) throw new Error('invalid generated script');
    const bytes = fs.readFileSync(scriptPath);
    result.script_base64 = bytes.toString('base64');
    result.script_sha256 = crypto.createHash('sha256').update(bytes).digest('hex');
  }
  const inputPath = '/workspace/tapper-input-request.json';
  if (fs.existsSync(inputPath)) {
    const stat = fs.lstatSync(inputPath);
    if (!stat.isFile() || stat.isSymbolicLink() || stat.size > 4096) throw new Error('invalid input request');
    result.input_request = parseInputRequest(fs.readFileSync(inputPath, 'utf8'));
  }
  return result;
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  try {
    if (process.argv[2] === 'daemon') await daemon();
    else if (process.argv[2] === 'run') process.stdout.write(JSON.stringify(await run()) + '\n');
    else if (process.argv[2] === 'collect') process.stdout.write(JSON.stringify(collect()) + '\n');
    else if (process.argv[2] === 'inspect') {
      const identity = verifiedIdentity();
      process.stdout.write(JSON.stringify({ ready: true, version: identity.version, pid: identity.pid }) + '\n');
    }
    else if (process.argv[2] === 'set-goal') {
      verifiedIdentity();
      const chunks = []; let size = 0;
      for await (const chunk of process.stdin) { size += chunk.length; if (size > 20000) throw new Error('goal size invalid'); chunks.push(chunk); }
      if (size === 0) throw new Error('empty goal');
      fs.writeFileSync('/workspace/goal.txt', Buffer.concat(chunks), { mode: 0o600 });
      process.stdout.write('{}\n');
    }
    else if (process.argv[2] === 'set-input') {
      verifiedIdentity();
      const chunks = []; let size = 0;
      for await (const chunk of process.stdin) { size += chunk.length; if (size > 32768) throw new Error('invalid frozen input'); chunks.push(chunk); }
      const data = validateFrozenInput(Buffer.concat(chunks), process.argv[3]);
      fs.writeFileSync('/workspace/frozen-inputs.json', data, { flag: 'wx', mode: 0o600 });
      process.stdout.write('{}\n');
    }
    else throw new Error('unsupported fixed client mode');
  } catch (error) {
    process.stderr.write(String(error.message).slice(0, 300) + '\n');
    process.exitCode = 1;
  }
}

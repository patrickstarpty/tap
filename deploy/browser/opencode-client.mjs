import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import { spawn, spawnSync } from 'node:child_process';
import { pathToFileURL } from 'node:url';
import { createModelShim } from './model-shim.mjs';

const LOCAL = process.env.TAP_CODING_LOCAL === '1';
const WORKSPACE = LOCAL ? process.env.TAP_CODING_WORKSPACE : '/workspace';
const BINARY = LOCAL ? process.env.TAP_OPENCODE_BINARY : '/opt/opencode/opencode';
const MAILBOX = LOCAL ? process.env.TAP_CODING_MAILBOX : '/mailbox';
const PORT = LOCAL ? Number(process.env.TAP_CODING_PORT) : 39002;
const SHIM_PORT = LOCAL ? Number(process.env.TAP_MODEL_SHIM_PORT) : 39001;
if (![WORKSPACE, BINARY, MAILBOX].every(value => typeof value === 'string' && path.isAbsolute(value)) ||
    ![PORT, SHIM_PORT].every(value => Number.isInteger(value) && value > 0 && value < 65536) || PORT === SHIM_PORT) {
  throw new Error('invalid coding client paths or ports');
}
const workspaceFile = name => path.join(WORKSPACE, name);

const ORIGIN = `http://127.0.0.1:${PORT}`;
const MAX_RESPONSE = 16 * 1024 * 1024;
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

function taskAssistants(messages, expectedUserId) {
  if (!Array.isArray(messages)) return null;
  const start = messages.findIndex(item => item?.info?.role === 'user' && item.info.id === expectedUserId);
  if (start < 0) return null;
  let parent = expectedUserId;
  let compaction = false;
  const assistants = [];
  for (const item of messages.slice(start + 1)) {
    if (!Array.isArray(item?.parts)) return null;
    if (item.info?.role === 'user') {
      const compact = item.parts.some(part => part.type === 'compaction' && part.auto === true);
      // Pinned OpenCode 1.19.5 marks its automatic continuation explicitly.
      const resume = compaction && item.parts.some(part => part.type === 'text'
        && part.synthetic === true && part.metadata?.compaction_continue === true);
      if (!compact && !resume) return null;
      parent = item.info.id;
      compaction = compact;
      assistants.push(null); // A summary or pending continuation is not completion.
      continue;
    }
    if (item.info?.role !== 'assistant' || item.info.parentID !== parent) return null;
    assistants.push(item);
  }
  return assistants;
}

const DIAGNOSTIC_TOOLS = new Set(['tap_browser', 'read', 'write', 'edit', 'bash', 'glob', 'grep', 'task', 'todowrite', 'webfetch']);
const DIAGNOSTIC_CATEGORIES = new Set(['none', 'validation_error', 'permission_denied', 'tool_timeout', 'tool_error', 'server_rejected', 'unknown_outcome']);

export function sanitizeToolDiagnostics(value) {
  if (!Array.isArray(value)) return [];
  return value.slice(0, 32).map((item, index) => ({
    sequence: index + 1,
    tool: DIAGNOSTIC_TOOLS.has(item?.tool) ? item.tool : 'other',
    status: ['completed', 'error'].includes(item?.status) ? item.status : 'pending',
    category: DIAGNOSTIC_CATEGORIES.has(item?.category) ? item.category : 'none',
  }));
}

export function toolDiagnostics(messages, expectedUserId) {
  const assistants = taskAssistants(messages, expectedUserId);
  const result = [];
  for (const item of assistants || []) {
    for (const part of item?.parts || []) {
      if (part?.type !== 'tool') continue;
      const status = ['completed', 'error'].includes(part.state?.status) ? part.state.status : 'pending';
      // Inspect only for fixed classifications; never retain model/tool text.
      const error = typeof part.state?.error === 'string' ? part.state.error.slice(0, 4096) : '';
      let category = 'none';
      if (status === 'error') {
        category = /invalid (?:arguments|input|browser (?:selector|step|value|key)|BDD plan|snapshot selector|target constraints|scope constraint|output name|read (?:condition|timeout))|unsupported browser action|browser action (?:requires absolute URL|exceeds size cap)|validation|schema|ZodError/i.test(error) ? 'validation_error'
          : /EACCES|permission denied/i.test(error) ? 'permission_denied'
          : /timed? out|timeout/i.test(error) ? 'tool_timeout' : 'tool_error';
      }
      // OpenCode completion means the tool returned, not that TAP accepted it.
      // Inspect only the bounded native tool envelope, never business output keys.
      if (status === 'completed' && part.tool === 'tap_browser' &&
          typeof part.state?.output === 'string' && part.state.output.length <= 65536) {
        let receipt;
        try { receipt = JSON.parse(part.state.output); } catch {}
        if (receipt && typeof receipt === 'object' && !Array.isArray(receipt)) {
          if (receipt.status === 'unknown') category = 'unknown_outcome';
          else if (['rejected','failed'].includes(receipt.status) || receipt.error || receipt.observation?.error)
            category = 'server_rejected';
        }
      }
      result.push({tool: part.tool, status, category});
      if (result.length === 32) return sanitizeToolDiagnostics(result);
    }
  }
  return sanitizeToolDiagnostics(result);
}

export function terminalMessages(messages, expectedUserId) {
  const assistants = taskAssistants(messages, expectedUserId);
  if (!assistants) return 'incomplete';
  for (const item of assistants) {
    if (!item) continue;
    if (item.info.error) return 'failed';
    if (item.parts.some(part => part?.type === 'tool' && !['completed', 'error'].includes(part.state?.status))) return 'incomplete';
  }
  const last = assistants.at(-1);
  return last && last.info.finish === 'stop' && last.info.summary !== true ? 'complete' : 'incomplete';
}

export function terminalFailure(messages, expectedUserId) {
  const assistants = taskAssistants(messages, expectedUserId);
  if (!assistants) return null;
  for (const item of assistants) {
    if (item?.info.error) {
      let error;
      try { error = JSON.parse(item.info.error.data?.responseBody || '').error; } catch {}
      if (error?.type === 'tap_budget_exhausted' && error.retryable === false &&
          ['budget_cost','budget_calls','budget_deadline','durable_reservation'].includes(error.code)) {
        return {category:error.code, tool:null, summary:'Task stopped: ' + error.code};
      }
      return { category: 'assistant_error', tool: null, summary: 'OpenCode assistant reported an error.' };
    }
  }
  for (const item of assistants) {
    for (const part of item?.parts || []) {
      if (part?.type !== 'tool' || part.state?.status !== 'error') continue;
      const tool = DIAGNOSTIC_TOOLS.has(part.tool) ? part.tool : 'other';
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

export async function api(method, route, body, timeoutMs = 30000) {
  try {
    const response = await fetch(ORIGIN + route, { method, headers: { authorization: credentials(), 'content-type': 'application/json' },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }), signal: AbortSignal.timeout(timeoutMs) });
    const raw = Buffer.from(await response.arrayBuffer());
    if (raw.length > MAX_RESPONSE) throw new Error('serve response exceeds cap');
    return { status: response.status, value: raw.length ? JSON.parse(raw.toString('utf8')) : null };
  } catch (error) {
    if (error.name === 'TimeoutError') throw new Error(`OpenCode ${method} ${route.split('?')[0]} timed out`);
    throw error;
  }
}

function verifiedIdentity() {
  const info = JSON.parse(fs.readFileSync(workspaceFile('serve.json'), 'utf8'));
  if (!Number.isInteger(info.pid) || info.pid < 1 || !/^[a-f0-9]{32}$/.test(info.nonce)) throw new Error('invalid serve identity');
  if (LOCAL) {
    if (info.nonce !== process.env.TAP_CODING_SERVE_NONCE) {
      throw new Error('serve identity no longer owns process');
    }
    process.kill(info.pid, 0);
    return info;
  }
  const command = fs.readFileSync(`/proc/${info.pid}/cmdline`, 'utf8');
  if (!command.includes('/opt/opencode/opencode') || !command.includes('serve') || !command.includes('127.0.0.1')) throw new Error('serve identity no longer owns process');
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
  const version = spawnSync(BINARY, ['--version'], { encoding: 'utf8', timeout: 10000 });
  if (version.status !== 0 || version.stdout.trim() !== '1.19.5') throw new Error('OpenCode version mismatch');
  const shim = createModelShim({ root: MAILBOX, token: process.env.TAP_MODEL_SHIM_TOKEN, deadlineAt: absoluteDeadline });
  await new Promise(resolve => shim.listen(SHIM_PORT, '127.0.0.1', resolve));
  const child = spawn(BINARY, ['serve', '--hostname', '127.0.0.1', '--port', String(PORT)],
    { cwd: WORKSPACE, env: process.env, detached: true, stdio: ['ignore', 'pipe', 'pipe'] });
  child.stdout.resume();
  child.stderr.resume();
  const lifetime = waitForDaemon(child, absoluteDeadline);
  const shutdown = () => { try { process.kill(-child.pid, 'SIGTERM'); } catch {} };
  process.on('SIGTERM', shutdown);
  process.on('SIGINT', shutdown);
  try {
  await waitForServeReadiness(child, absoluteDeadline);
  const config = JSON.parse(process.env.OPENCODE_CONFIG_CONTENT);
  const [provider, model] = config.model.split('/');
  const registered = await api('GET', `/experimental/tool?provider=${encodeURIComponent(provider)}&model=${encodeURIComponent(model)}`);
  if (registered.status !== 200 || !registered.value?.some(tool => tool.id === 'tap_browser')) {
    throw new Error('native browser tool unavailable');
  }
  fs.writeFileSync(workspaceFile('serve.json'), JSON.stringify({ pid: child.pid, nonce: LOCAL ? process.env.TAP_CODING_SERVE_NONCE : crypto.randomBytes(16).toString('hex'), version: '1.19.5' }), { flag: 'wx', mode: 0o600 });
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
  const goal = fs.readFileSync(workspaceFile('goal.txt'), 'utf8');
  if (!goal || Buffer.byteLength(goal) > 20000) throw new Error('goal size invalid');
  let sessionId;
  if (fs.existsSync(workspaceFile('session.json'))) {
    sessionId = JSON.parse(fs.readFileSync(workspaceFile('session.json'), 'utf8')).session_id;
    if (!/^ses_[A-Za-z0-9_-]{1,100}$/.test(sessionId || '')) throw new Error('saved session identity invalid');
  } else {
    const created = await api('POST', '/session', {});
    if (created.status !== 200 || !/^ses_[A-Za-z0-9_-]{1,100}$/.test(created.value?.id || '')) throw new Error('session create failed');
    sessionId = created.value.id;
    fs.writeFileSync(workspaceFile('session.json'), JSON.stringify({ session_id: sessionId }), { flag: 'wx', mode: 0o600 });
  }
  const initial = await api('GET', `/session/${sessionId}/children`);
  if (initial.status !== 200 || !Array.isArray(initial.value)) throw new Error('child baseline unavailable');
  const baseline = new Set(initial.value.map(item => item.id));
  const messageId = 'msg_' + crypto.randomBytes(12).toString('hex');
  const idle = new Set();
  let diagnostics = [];
  const persistDiagnostics = () => {
    const pending = workspaceFile('tool-diagnostics-' + crypto.randomBytes(12).toString('hex') + '.pending.json');
    fs.writeFileSync(pending, JSON.stringify(diagnostics), {mode: 0o600, flag: 'wx'});
    fs.renameSync(pending, workspaceFile('tool-diagnostics.json'));
  };
  persistDiagnostics();
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
      diagnostics = toolDiagnostics(messages.value, messageId);
      persistDiagnostics();
      const children = linkedChildren(messages.value, rawChildren.value, sessionId, baseline);
      if (children !== null) {
        const root = terminalMessages(messages.value, messageId);
        if (root === 'failed') return { session_id: sessionId, message_id: messageId, terminal: 'failed', diagnostics,
          failure: terminalFailure(messages.value, messageId) || { category: 'unknown', tool: null, summary: 'OpenCode failed without classified error.' } };
        if (root === 'complete' && idle.has(sessionId)) {
          let finished = true;
          for (const childId of children) {
            const childMessages = await api('GET', `/session/${childId}/message`);
            const user = childMessages.value?.filter(item => item?.info?.role === 'user').at(-1)?.info?.id;
            if (childMessages.status !== 200 || !idle.has(childId) || !user || terminalMessages(childMessages.value, user) !== 'complete') finished = false;
          }
          if (finished) return { session_id: sessionId, message_id: messageId, terminal: 'complete', diagnostics };
        }
      }
      await sleep(200);
    }
    await api('POST', `/session/${sessionId}/abort`, {});
    return { session_id: sessionId, message_id: messageId, terminal: 'incomplete', diagnostics };
  } finally {
    controller.abort();
    await watch;
  }
}

function collectDiagnostics() {
  let descriptor;
  try {
    // Keep task/process ownership checks, but never inspect generated artifacts.
    verifiedIdentity();
    descriptor = fs.openSync(workspaceFile('tool-diagnostics.json'), fs.constants.O_RDONLY | fs.constants.O_NOFOLLOW);
    const stat = fs.fstatSync(descriptor);
    if (!stat.isFile() || stat.size > 12000) throw new Error('invalid diagnostics');
    const bytes = Buffer.alloc(12001);
    const count = fs.readSync(descriptor, bytes, 0, bytes.length, 0);
    if (count > 12000) throw new Error('invalid diagnostics');
    const value = JSON.parse(bytes.subarray(0,count).toString('utf8'));
    if (!Array.isArray(value)) throw new Error('invalid diagnostics');
    return {diagnostics:sanitizeToolDiagnostics(value)};
  } catch {
    return {diagnostics:[],diagnostics_unavailable:true};
  } finally {
    if (descriptor !== undefined) fs.closeSync(descriptor);
  }
}

function collect() {
  verifiedIdentity();
  const result = {};
  const diagnosticPath = workspaceFile('tool-diagnostics.json');
  if (fs.existsSync(diagnosticPath)) {
    const stat = fs.lstatSync(diagnosticPath);
    if (stat.isFile() && !stat.isSymbolicLink() && stat.size <= 12000) {
      try { result.diagnostics = sanitizeToolDiagnostics(JSON.parse(fs.readFileSync(diagnosticPath, 'utf8'))); } catch {}
    }
  }
  const scriptPath = workspaceFile('flow.spec.ts');
  if (fs.existsSync(scriptPath)) {
    const stat = fs.lstatSync(scriptPath);
    if (!stat.isFile() || stat.isSymbolicLink() || stat.size < 1 || stat.size > 256000) throw new Error('invalid generated script');
    const bytes = fs.readFileSync(scriptPath);
    result.script_base64 = bytes.toString('base64');
    result.script_sha256 = crypto.createHash('sha256').update(bytes).digest('hex');
  }
  const inputPath = workspaceFile('tapper-input-request.json');
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
    else if (process.argv[2] === 'collect-diagnostics') process.stdout.write(JSON.stringify(collectDiagnostics()) + '\n');
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
      fs.writeFileSync(workspaceFile('goal.txt'), Buffer.concat(chunks), { mode: 0o600 });
      process.stdout.write('{}\n');
    }
    else if (process.argv[2] === 'set-input') {
      verifiedIdentity();
      const chunks = []; let size = 0;
      for await (const chunk of process.stdin) { size += chunk.length; if (size > 32768) throw new Error('invalid frozen input'); chunks.push(chunk); }
      const data = validateFrozenInput(Buffer.concat(chunks), process.argv[3]);
      fs.writeFileSync(workspaceFile('frozen-inputs.json'), data, { flag: 'wx', mode: 0o600 });
      process.stdout.write('{}\n');
    }
    else throw new Error('unsupported fixed client mode');
  } catch (error) {
    process.stderr.write(String(error.message).slice(0, 300) + '\n');
    process.exitCode = 1;
  }
}

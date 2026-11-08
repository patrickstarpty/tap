import crypto from 'node:crypto';
import fs from 'node:fs';
import http from 'node:http';
import path from 'node:path';
import { pathToFileURL } from 'node:url';

const MAX_REQUEST_BYTES = 131072;
const MAX_RESPONSE_BYTES = 1048576;
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

export async function mailboxRequest(root, kind, payload, timeoutMs = 30000) {
  if (!['model', 'browser'].includes(kind) || !Number.isInteger(timeoutMs) || timeoutMs < 1 || timeoutMs > 600000) {
    throw new Error('invalid mailbox operation');
  }
  const id = crypto.randomBytes(16).toString('hex');
  const requestPath = path.join(root, 'requests', `${id}.json`);
  const responsePath = path.join(root, 'responses', `${id}.json`);
  const ackPath = path.join(root, 'acks', `${id}.json`);
  const ackTemporary = path.join(root, 'acks', `${id}.tmp`);
  const temporary = path.join(root, 'requests', `${id}.tmp`);
  const deadlineAt = Date.now() + timeoutMs;
  const envelope = Buffer.from(JSON.stringify({ id, kind, deadline_at: deadlineAt / 1000, payload }));
  if (envelope.length > 196608) throw new Error('mailbox request exceeds size cap');
  fs.writeFileSync(temporary, envelope, { flag: 'wx', mode: 0o600 });
  fs.renameSync(temporary, requestPath);
  try {
    while (Date.now() < deadlineAt) {
      try {
        const stat = fs.lstatSync(responsePath);
        if (!stat.isFile() || stat.isSymbolicLink() || (stat.mode & 0o077)) throw new Error('unsafe mailbox response');
        const body = fs.readFileSync(responsePath);
        if (body.length > 1400000) throw new Error('mailbox response exceeds size cap');
        const result = JSON.parse(body.toString('utf8'));
        fs.writeFileSync(ackTemporary, id, { flag: 'wx', mode: 0o600 });
        fs.renameSync(ackTemporary, ackPath);
        return result;
      } catch (error) {
        if (error.code !== 'ENOENT') throw error;
      }
      await sleep(10);
    }
    throw new Error('mailbox response deadline');
  } finally {
    fs.rmSync(requestPath, { force: true });
    fs.rmSync(temporary, { force: true });
    fs.rmSync(ackTemporary, { force: true });
  }
}

export function createModelShim({ root, token, port = 39001, deadlineAt = null }) {
  if (!root || !token || !Number.isInteger(port) || port < 0 || port > 65535) throw new Error('invalid shim configuration');
  if (deadlineAt !== null && (!Number.isSafeInteger(deadlineAt) || deadlineAt <= 0)) throw new Error('invalid model deadline');
  return http.createServer(async (request, response) => {
    if (request.method !== 'POST' || request.url !== '/v1/chat/completions') {
      response.writeHead(404, { 'Content-Length': '0' }).end();
      return;
    }
    if (request.headers.authorization !== `Bearer ${token}`) {
      response.writeHead(401, { 'Content-Length': '0' }).end();
      return;
    }
    const chunks = [];
    let size = 0;
    const measure = process.env.TAP_CODING_MEASURE_REQUESTS === '1';
    try {
      for await (const chunk of request) {
        size += chunk.length;
        if (size > (measure ? 2097152 : MAX_REQUEST_BYTES)) {
          response.writeHead(413, { 'Content-Length': '0' }).end();
          return;
        }
        chunks.push(chunk);
      }
      if (measure) {
        let parsed = {};
        try { parsed = JSON.parse(Buffer.concat(chunks).toString('utf8')); } catch {}
        const toolBytes = Buffer.byteLength(JSON.stringify(parsed.tools || []));
        const messageBytes = Buffer.byteLength(JSON.stringify(parsed.messages || []));
        fs.appendFileSync('/workspace/model-request-sizes.jsonl', JSON.stringify({ bytes: size, toolBytes, messageBytes, tools: parsed.tools?.length || 0, messages: parsed.messages?.length || 0 }) + '\n', { mode: 0o600 });
      }
      if (size > MAX_REQUEST_BYTES) {
        response.writeHead(413, { 'Content-Length': '0' }).end();
        return;
      }
      if (size === 0) throw new Error('empty model request');
      const remaining = deadlineAt === null ? 30000 : Math.min(600000, deadlineAt - Date.now());
      if (remaining <= 0) throw new Error('coding task deadline exceeded');
      const result = await mailboxRequest(root, 'model', {
        bodyBase64: Buffer.concat(chunks).toString('base64'),
        claimedSessionId: request.headers['x-opencode-session-id'] || null,
      }, remaining);
      const body = Buffer.from(result.bodyBase64 || '', 'base64');
      if (body.length > MAX_RESPONSE_BYTES || !Number.isInteger(result.status) || result.status < 100 || result.status > 599 ||
          typeof result.contentType !== 'string' || !/^(?:application\/json|text\/event-stream)(?:;[^\r\n]{0,80})?$/.test(result.contentType)) {
        throw new Error('invalid model response envelope');
      }
      response.writeHead(result.status, { 'Content-Type': result.contentType, 'Content-Length': String(body.length) });
      response.end(body);
    } catch {
      if (!response.headersSent) response.writeHead(502, { 'Content-Length': '0' }).end();
    }
  });
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const server = createModelShim({ root: process.env.TAP_CODING_MAILBOX, token: process.env.TAP_MODEL_SHIM_TOKEN,
    port: Number(process.env.TAP_MODEL_SHIM_PORT || 39001) });
  server.listen(Number(process.env.TAP_MODEL_SHIM_PORT || 39001), '127.0.0.1');
}

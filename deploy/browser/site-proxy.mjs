import fs from 'node:fs';
import dns from 'node:dns/promises';
import http from 'node:http';
import https from 'node:https';
import net from 'node:net';
import { createTaskCertificates } from './proxy-cert.mjs';
import { isPublicIP, parseUpstreamProxy, resolveViaDoh, createPinnedAgent } from './upstream-transport.mjs';
export { isPublicIP, parseUpstreamProxy, parseDnsAnswer, openProxyTunnel, resolveViaDoh, createPinnedAgent } from './upstream-transport.mjs';

export function validateTarget(raw, allowed, publicWeb = false) {
  const url = new URL(raw);
  if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password ||
      (!publicWeb && !allowed.includes(url.origin)) || ['localhost', 'localhost.localdomain'].includes(url.hostname) ||
      (net.isIP(url.hostname) && !isPublicIP(url.hostname))) {
    throw new Error('destination denied');
  }
  return url;
}

export function validateInnerRequest(request, outerOrigin, allowed, publicWeb = false) {
  const outer = validateTarget(outerOrigin, allowed, publicWeb);
  if (outer.protocol !== 'https:' || request.servername !== outer.hostname ||
      request.headers.host !== outer.host || request.headers[':authority'] !== undefined ||
      !request.url?.startsWith('/') || request.url.startsWith('//') ||
      /[\r\n]/.test(request.url) || !/^[A-Z]+$/.test(request.method || '')) {
    throw new Error('HTTPS authority denied');
  }
  return true;
}

export async function resolvePublicIPv4(hostname, lookup = dns.lookup) {
  const addresses = await lookup(hostname, { family: 4, all: true, verbatim: true });
  if (!Array.isArray(addresses) || !addresses.length ||
      addresses.some(({ address, family }) => family !== 4 || !isPublicIP(address))) {
    throw new Error('resolved destination denied');
  }
  return addresses[0].address;
}

async function destination(url, policy, upstream) {
  if (policy.test_target) {
    const override = new URL(policy.test_target);
    if (override.protocol !== url.protocol || net.isIP(override.hostname) !== 4 ||
        isPublicIP(override.hostname) || !override.port) {
      throw new Error('invalid supervisor test target');
    }
    return { host: override.hostname, port: Number(override.port), synthetic: true };
  }
  const host = await resolvePublicIPv4(url.hostname, upstream ?
    hostname => resolveViaDoh(hostname, upstream) : dns.lookup);
  return { host, port: Number(url.port || (url.protocol === 'https:' ? 443 : 80)), synthetic: false };
}

export function createProxyTransport(policy) {
  const upstream = policy.upstream_proxy == null ? null : parseUpstreamProxy(policy.upstream_proxy);
  if (upstream && policy.test_target) throw new Error('upstream proxy cannot use synthetic targets');
  return {
    destination: async url => {
      validateTarget(url.href, policy.allowed_origins, policy.public_web === true);
      return destination(url, policy, upstream);
    },
    agent: (url, target) => upstream ? createPinnedAgent(upstream, target.host, target.port,
      url.hostname, url.protocol === 'https:') : undefined,
  };
}

if (process.env.TAP_BROWSER_PROXY_POLICY) {
  const policy = JSON.parse(process.env.TAP_BROWSER_PROXY_POLICY);
  const transport = createProxyTransport(policy);
  const { getCertificate } = createTaskCertificates(policy.allowed_origins, { publicWeb: policy.public_web === true });
  const server = http.createServer(async (req, res) => {
    try {
      const url = validateTarget(req.url, policy.allowed_origins, policy.public_web === true);
      if (url.protocol !== 'http:') throw new Error('HTTPS requires CONNECT');
      if (req.headers.host !== url.host) throw new Error('HTTP authority denied');
      const target = await transport.destination(url);
      const outbound = http.request({ host: target.host, port: target.port, method: req.method,
        agent: transport.agent(url, target), path: url.pathname + url.search, headers: { ...req.headers, host: url.host }, timeout: 15_000 },
      upstream => { res.writeHead(upstream.statusCode, upstream.headers); upstream.pipe(res); });
      outbound.on('timeout', () => outbound.destroy(new Error('destination timeout')));
      outbound.on('error', () => { if (!res.headersSent) res.writeHead(502); res.end(); });
      req.pipe(outbound);
    } catch { res.writeHead(403); res.end('destination denied'); }
  });
  server.on('connect', async (req, client, head) => {
    try {
      const url = validateTarget(`https://${req.url}`, policy.allowed_origins, policy.public_web === true);
      const target = await transport.destination(url);
      const identity = getCertificate(url.hostname);
      if (!identity) throw new Error('HTTPS identity unavailable');
      const inner = https.createServer({ ...identity, ALPNProtocols: ['http/1.1'],
        handshakeTimeout: 10_000 }, (innerReq, innerRes) => {
        try {
          validateInnerRequest({ method: innerReq.method, url: innerReq.url,
            headers: innerReq.headers, servername: innerReq.socket.servername }, url.origin,
          policy.allowed_origins, policy.public_web === true);
          if (innerReq.rawHeaders.filter((_, i) => i % 2 === 0).filter(x => x.toLowerCase() === 'host').length !== 1) {
            throw new Error('ambiguous Host');
          }
          const options = { host: target.host, port: target.port, servername: url.hostname,
            rejectUnauthorized: true, agent: transport.agent(url, target), method: innerReq.method, path: innerReq.url,
            headers: { ...innerReq.headers, host: url.host }, timeout: 15_000,
            ...(target.synthetic ? { ca: policy.test_ca_pem } : {}) };
          const outbound = https.request(options, upstream => {
            innerRes.writeHead(upstream.statusCode, upstream.headers); upstream.pipe(innerRes);
          });
          outbound.on('timeout', () => outbound.destroy(new Error('destination timeout')));
          outbound.on('error', () => { if (!innerRes.headersSent) innerRes.writeHead(502); innerRes.end(); });
          innerReq.pipe(outbound);
        } catch { innerRes.writeHead(403); innerRes.end('destination denied'); }
      });
      inner.on('secureConnection', socket => {
        if (socket.servername !== url.hostname) socket.destroy();
      });
      inner.on('tlsClientError', () => client.destroy());
      client.on('error', () => client.destroy());
      client.write('HTTP/1.1 200 Connection Established\r\n\r\n');
      if (head.length) client.unshift(head);
      inner.emit('connection', client);
    } catch { client.end('HTTP/1.1 403 Forbidden\r\n\r\n'); }
  });
  const localRoot = process.env.TAP_BROWSER_LOCAL_DIR;
  server.listen(localRoot ? 0 : 3128, localRoot ? '127.0.0.1' : '0.0.0.0', () => {
    if (localRoot) fs.writeFileSync(`${localRoot}/proxy-port`, String(server.address().port), {mode:0o600});
  });
}

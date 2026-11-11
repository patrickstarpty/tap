import net from 'node:net';
import http from 'node:http';
import https from 'node:https';
import tls from 'node:tls';

// This is supervisor configuration, never taken from a browser request or proxy env.
export function parseUpstreamProxy(raw) {
  const match = typeof raw === 'string' && /^http:\/\/(127\.\d+\.\d+\.\d+):([1-9]\d{0,4})\/?$/.exec(raw);
  if (!match || net.isIP(match[1]) !== 4 || Number(match[2]) > 65535) {
    throw new Error('invalid local upstream proxy');
  }
  return {host:match[1], port:Number(match[2])};
}

const privateV4 = (ip) => {
  const n = ip.split('.').map(Number);
  return n[0] === 0 || n[0] === 10 || n[0] === 127 || n[0] >= 224 ||
    (n[0] === 169 && n[1] === 254) || (n[0] === 172 && n[1] >= 16 && n[1] <= 31) ||
    (n[0] === 192 && n[1] === 168) || (n[0] === 100 && n[1] >= 64 && n[1] <= 127) ||
    (n[0] === 192 && n[1] === 0) || (n[0] === 198 && n[1] >= 18 && n[1] <= 19) ||
    (n[0] === 198 && n[1] === 51 && n[2] === 100) ||
    (n[0] === 203 && n[1] === 0 && n[2] === 113) ||
    (n[0] === 192 && n[1] === 88 && n[2] === 99);
};

export function isPublicIP(ip) {
  return net.isIP(ip) === 4 && !privateV4(ip);
}

function dnsName(value) {
  if (typeof value !== 'string') throw new Error('invalid DNS name');
  const name = value.toLowerCase().replace(/\.$/, '');
  if (name.length > 253 || !name.split('.').every(label =>
    /^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/.test(label)) ||
    ['localhost', 'localhost.localdomain'].includes(name) || net.isIP(name)) {
    throw new Error('invalid DNS name');
  }
  return name;
}

export function parseDnsAnswer(body, hostname) {
  const name = dnsName(hostname);
  if (!body || body.Status !== 0 || body.TC !== false ||
      !Array.isArray(body.Question) || body.Question.length !== 1 ||
      body.Question[0]?.type !== 1 || dnsName(body.Question[0]?.name) !== name ||
      !Array.isArray(body.Answer) || !body.Answer.length || body.Answer.length > 128) {
    throw new Error('invalid DNS response');
  }
  const aliases = new Map();
  const records = [];
  for (const record of body.Answer) {
    if (!record || !Number.isInteger(record.TTL) || record.TTL < 0) throw new Error('invalid DNS record');
    const owner = dnsName(record.name);
    if (record.type === 5 && !aliases.has(owner)) aliases.set(owner, dnsName(record.data));
    else if (record.type === 1 && isPublicIP(record.data)) records.push({owner, address:record.data, family:4});
    else throw new Error('DNS destination denied');
  }
  let terminal = name;
  const visited = new Set();
  while (aliases.has(terminal)) {
    if (visited.has(terminal) || visited.size >= 16) throw new Error('invalid DNS alias chain');
    visited.add(terminal);
    terminal = aliases.get(terminal);
  }
  if (visited.size !== aliases.size || !records.length || records.some(record => record.owner !== terminal)) {
    throw new Error('invalid DNS answer chain');
  }
  return records.map(({address, family}) => ({address, family}));
}

export async function openProxyTunnel(upstream, address, port, timeout = 15_000) {
  // No hostnames reach the upstream proxy: it cannot redo target DNS resolution.
  if (!isPublicIP(address) || !Number.isInteger(port) || port < 1 || port > 65535) {
    throw new Error('invalid pinned destination');
  }
  const proxy = parseUpstreamProxy(`http://${upstream.host}:${upstream.port}`);
  return new Promise((resolve, reject) => {
    const authority = `${address}:${port}`;
    const request = http.request({host:proxy.host, port:proxy.port, method:'CONNECT',
      path:authority, headers:{Host:authority}, agent:false, maxHeaderSize:8192});
    const timer = setTimeout(() => request.destroy(new Error('upstream proxy timeout')), timeout);
    request.once('error', error => { clearTimeout(timer); reject(error); });
    request.once('connect', (response, socket, head) => {
      clearTimeout(timer);
      if (response.statusCode !== 200 || head.length || response.headers['content-length'] !== undefined ||
          response.headers['transfer-encoding'] !== undefined) {
        socket.destroy(); reject(new Error('upstream CONNECT denied')); return;
      }
      resolve(socket);
    });
    request.once('response', response => {
      clearTimeout(timer); response.destroy(); request.destroy(); reject(new Error('upstream CONNECT denied'));
    });
    request.end();
  });
}

export function createPinnedAgent(upstream, address, port, hostname, secure) {
  const agent = secure ? new https.Agent({keepAlive:false}) : new http.Agent({keepAlive:false});
  agent.createConnection = (options, callback) => {
    openProxyTunnel(upstream, address, port).then(socket => {
      if (!secure) { callback(null, socket); return; }
      const encrypted = tls.connect({...options, socket, servername:hostname, rejectUnauthorized:true,
        minVersion:'TLSv1.2', checkServerIdentity:(_name, certificate) => tls.checkServerIdentity(hostname, certificate)});
      const timer = setTimeout(() => encrypted.destroy(new Error('upstream TLS timeout')), 15_000);
      encrypted.once('secureConnect', () => clearTimeout(timer));
      encrypted.once('close', () => clearTimeout(timer));
      callback(null, encrypted);
    }, callback);
  };
  return agent;
}

// Official endpoint/bootstrap: developers.cloudflare.com/1.1.1.1/{ip-addresses,
// encryption/dns-over-https/make-api-requests/}. TLS authenticates the resolver;
// the local DNS/TUN result is never used to bootstrap or resolve a destination.
export async function resolveViaDoh(hostname, upstream, request = https.request) {
  if (net.isIP(hostname)) {
    if (!isPublicIP(hostname)) throw new Error('DNS destination denied');
    return [{address:hostname, family:4}];
  }
  const name = dnsName(hostname);
  const agent = createPinnedAgent(upstream, '1.1.1.1', 443, 'cloudflare-dns.com', true);
  try {
    const body = await new Promise((resolve, reject) => {
      const outbound = request({hostname:'cloudflare-dns.com', port:443, method:'GET',
        path:`/dns-query?name=${encodeURIComponent(name)}&type=A`, agent, rejectUnauthorized:true,
        headers:{Host:'cloudflare-dns.com', Accept:'application/dns-json'}, maxHeaderSize:8192}, response => {
        if (response.statusCode !== 200 ||
            response.headers['content-type']?.split(';')[0].trim() !== 'application/dns-json') {
          response.destroy(); outbound.destroy(); reject(new Error('DNS HTTP response denied')); return;
        }
        const chunks = [];
        let bytes = 0;
        response.on('data', chunk => {
          bytes += chunk.length;
          if (bytes > 65_536) {
            outbound.destroy(); response.destroy(); reject(new Error('DNS response too large'));
          }
          else chunks.push(chunk);
        });
        response.once('error', reject);
        response.once('end', () => {
          try { resolve(JSON.parse(Buffer.concat(chunks).toString('utf8'))); }
          catch { reject(new Error('invalid DNS JSON')); }
        });
      });
      const timer = setTimeout(() => outbound.destroy(new Error('DNS request timeout')), 15_000);
      outbound.once('close', () => clearTimeout(timer));
      outbound.once('error', reject);
      outbound.end();
    });
    return parseDnsAnswer(body, name);
  } finally { agent.destroy(); }
}

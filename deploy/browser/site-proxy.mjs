import dns from 'node:dns/promises';
import http from 'node:http';
import net from 'node:net';

const privateV4 = (ip) => {
  const n = ip.split('.').map(Number);
  return n[0] === 0 || n[0] === 10 || n[0] === 127 || n[0] >= 224 ||
    (n[0] === 169 && n[1] === 254) || (n[0] === 172 && n[1] >= 16 && n[1] <= 31) ||
    (n[0] === 192 && n[1] === 168) || (n[0] === 100 && n[1] >= 64 && n[1] <= 127) ||
    (n[0] === 192 && n[1] === 0) || (n[0] === 198 && n[1] >= 18 && n[1] <= 19);
};

export function isPublicIP(ip) {
  return net.isIP(ip) === 4 && !privateV4(ip);
}

export function validateTarget(raw, allowed) {
  const url = new URL(raw);
  if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password ||
      !allowed.includes(url.origin) || url.hostname === 'localhost' ||
      (net.isIP(url.hostname) && !isPublicIP(url.hostname))) {
    throw new Error('destination denied');
  }
  return url;
}

async function destination(url, policy) {
  if (policy.test_target) {
    const override = new URL(policy.test_target);
    if (override.protocol !== 'http:' || net.isIP(override.hostname) !== 4 ||
        isPublicIP(override.hostname) || !override.port) {
      throw new Error('invalid supervisor test target');
    }
    return { host: override.hostname, port: Number(override.port), synthetic: true };
  }
  const addresses = await dns.lookup(url.hostname, { all: true, verbatim: true });
  if (!addresses.length || addresses.some(({ address }) => !isPublicIP(address))) {
    throw new Error('resolved destination denied');
  }
  return { host: addresses[0].address, port: Number(url.port || (url.protocol === 'https:' ? 443 : 80)), synthetic: false };
}

if (process.env.TAP_BROWSER_PROXY_POLICY) {
  const policy = JSON.parse(process.env.TAP_BROWSER_PROXY_POLICY);
  const server = http.createServer(async (req, res) => {
    try {
      const url = validateTarget(req.url, policy.allowed_origins);
      if (url.protocol !== 'http:') throw new Error('HTTPS requires CONNECT');
      const target = await destination(url, policy);
      const outbound = http.request({ host: target.host, port: target.port, method: req.method,
        path: url.pathname + url.search, headers: { ...req.headers, host: url.host }, timeout: 15_000 },
      upstream => { res.writeHead(upstream.statusCode, upstream.headers); upstream.pipe(res); });
      outbound.on('error', () => { if (!res.headersSent) res.writeHead(502); res.end(); });
      req.pipe(outbound);
    } catch { res.writeHead(403); res.end('destination denied'); }
  });
  server.on('connect', async (req, client, head) => {
    try {
      const url = validateTarget(`https://${req.url}`, policy.allowed_origins);
      const target = await destination(url, policy);
      if (target.synthetic) throw new Error('synthetic HTTPS unsupported');
      const upstream = net.connect(target.port, target.host);
      upstream.setTimeout(15_000, () => upstream.destroy());
      upstream.once('connect', () => { client.write('HTTP/1.1 200 Connection Established\r\n\r\n');
        if (head.length) upstream.write(head); client.pipe(upstream); upstream.pipe(client); });
      upstream.on('error', () => client.destroy());
    } catch { client.end('HTTP/1.1 403 Forbidden\r\n\r\n'); }
  });
  server.listen(3128, '0.0.0.0');
}

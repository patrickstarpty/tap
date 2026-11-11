import assert from 'node:assert/strict';
import test from 'node:test';
import * as proxy from '../site-proxy.mjs';

test('upstream transport requires an explicit credential-free IPv4 loopback HTTP proxy', () => {
  assert.equal(typeof proxy.parseUpstreamProxy, 'function');
  assert.deepEqual(proxy.parseUpstreamProxy('http://127.0.0.1:7897'), {host:'127.0.0.1', port:7897});
  for (const value of ['http://localhost:7897', 'http://10.0.0.1:7897', 'http://1.1.1.1:7897',
    'https://127.0.0.1:7897', 'socks5://127.0.0.1:7897', 'http://user:secret@127.0.0.1:7897',
    'http://127.0.0.1:7897/path', 'http://127.0.0.1:7897/?x=1', 'http://127.0.0.1:7897/#x',
    'http://127.0.0.1', 'http://127.1:7897', 'http://2130706433:7897']) {
    assert.throws(() => proxy.parseUpstreamProxy(value), /upstream proxy/);
  }
});

const answer = (changes = {}) => ({Status:0, TC:false,
  Question:[{name:'www.example.com.', type:1}],
  Answer:[{name:'www.example.com.', type:5, TTL:30, data:'edge.example.net.'},
    {name:'edge.example.net.', type:1, TTL:30, data:'93.184.215.14'}], ...changes});

test('authenticated DNS parser checks question, complete alias chain and every public IPv4', () => {
  assert.equal(typeof proxy.parseDnsAnswer, 'function');
  assert.deepEqual(proxy.parseDnsAnswer(answer(), 'www.example.com'), [{address:'93.184.215.14', family:4}]);
  for (const value of [null, {}, answer({Status:3}), answer({TC:true}), answer({Answer:[]}),
    answer({Question:[{name:'other.example.', type:1}]}), answer({Question:[]}),
    answer({Answer:[{name:'other.example.', type:1, TTL:30, data:'93.184.215.14'}]}),
    answer({Answer:[{name:'www.example.com.', type:5, TTL:30, data:'www.example.com.'}]}),
    answer({Answer:[{name:'www.example.com.', type:28, TTL:30, data:'::1'}]}),
    answer({Answer:[{name:'www.example.com.', type:1, TTL:-1, data:'93.184.215.14'}]}),
  ]) assert.throws(() => proxy.parseDnsAnswer(value, 'www.example.com'), /DNS/);
  for (const address of ['127.0.0.1', '10.0.0.1', '169.254.169.254', '198.18.0.39',
    '::ffff:127.0.0.1', 'garbage']) {
    const value = answer();
    value.Answer.push({name:'edge.example.net.', type:1, TTL:30, data:address});
    assert.throws(() => proxy.parseDnsAnswer(value, 'www.example.com'), /DNS/);
  }
});

async function localProxy(t, response = 'HTTP/1.1 200 Connection Established\r\n\r\n') {
  const net = await import('node:net');
  const {once} = await import('node:events');
  const requests = [];
  const sockets = new Set();
  const server = net.createServer(socket => {
    sockets.add(socket); socket.on('close', () => sockets.delete(socket));
    socket.once('data', data => { requests.push(data.toString()); if (response) socket.write(response); });
  });
  server.listen(0, '127.0.0.1'); await once(server, 'listening');
  t.after(() => { for (const socket of sockets) socket.destroy(); server.close(); });
  return {upstream:{host:'127.0.0.1', port:server.address().port}, requests};
}

test('CONNECT transports the validated IP only and never sends browser headers', async t => {
  assert.equal(typeof proxy.openProxyTunnel, 'function');
  const {upstream, requests} = await localProxy(t);
  const socket = await proxy.openProxyTunnel(upstream, '93.184.215.14', 443);
  socket.destroy();
  assert.equal(requests.length, 1);
  assert.match(requests[0], /^CONNECT 93\.184\.215\.14:443 HTTP\/1\.1\r\n/);
  assert.match(requests[0], /\r\nHost: 93\.184\.215\.14:443\r\n/i);
  assert.doesNotMatch(requests[0], /Authorization|Cookie|example/i);
  for (const host of ['198.18.0.39', '127.0.0.1', '169.254.169.254', 'example.com']) {
    await assert.rejects(proxy.openProxyTunnel(upstream, host, 443));
  }
  assert.equal(requests.length, 1);
});

test('CONNECT rejects redirects, authentication, malformed, extra-data and oversized responses', async t => {
  assert.equal(typeof proxy.openProxyTunnel, 'function');
  for (const response of ['HTTP/1.1 302 Found\r\nLocation: http://127.0.0.1/\r\n\r\n',
    'HTTP/1.1 407 Proxy Authentication Required\r\n\r\n', 'malformed\r\n\r\n',
    'HTTP/1.1 200 OK\r\n\r\ninjected', 'HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\n',
    'HTTP/1.1 200 OK\r\nX-Padding: ' + 'x'.repeat(9000) + '\r\n\r\n']) {
    const {upstream} = await localProxy(t, response);
    await assert.rejects(proxy.openProxyTunnel(upstream, '93.184.215.14', 443));
  }
  const {upstream} = await localProxy(t, null);
  await assert.rejects(proxy.openProxyTunnel(upstream, '93.184.215.14', 443, 30), /timeout/);
});

async function tlsFixture(t) {
  const fs = await import('node:fs');
  const https = await import('node:https');
  const http = await import('node:http');
  const net = await import('node:net');
  const {execFileSync} = await import('node:child_process');
  const {once} = await import('node:events');
  const dir = fs.mkdtempSync('/tmp/tap-upstream-tls-');
  execFileSync('openssl', ['req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1',
    '-keyout', `${dir}/key.pem`, '-out', `${dir}/cert.pem`, '-subj', '/CN=www.example.com',
    '-addext', 'subjectAltName=DNS:www.example.com,DNS:cloudflare-dns.com'], {stdio:'ignore'});
  const ca = fs.readFileSync(`${dir}/cert.pem`);
  const seen = [], connects = [], sockets = new Set();
  const fixture = {status:200, body:JSON.stringify(answer()), contentType:'application/dns-json'};
  const server = https.createServer({key:fs.readFileSync(`${dir}/key.pem`), cert:ca}, (req, res) => {
    seen.push({host:req.headers.host, servername:req.socket.servername, path:req.url});
    res.writeHead(fixture.status, {'content-type':fixture.contentType}); res.end(fixture.body);
  });
  const upstream = http.createServer();
  for (const listener of [server, upstream]) listener.on('connection', socket => {
    sockets.add(socket); socket.on('close', () => sockets.delete(socket)); socket.on('error', () => {});
  });
  upstream.on('connect', (req, client, head) => {
    connects.push({authority:req.url, host:req.headers.host});
    assert.ok(['1.1.1.1:443', '93.184.215.14:443'].includes(req.url));
    const remote = net.connect(server.address().port, '127.0.0.1', () => {
      client.write('HTTP/1.1 200 Connection Established\r\n\r\n');
      if (head.length) remote.write(head);
      client.pipe(remote); remote.pipe(client);
    });
    sockets.add(remote); remote.on('close', () => sockets.delete(remote));
    remote.on('error', () => client.destroy()); client.on('close', () => remote.destroy());
  });
  for (const listener of [server, upstream]) { listener.listen(0, '127.0.0.1'); await once(listener, 'listening'); }
  t.after(() => {
    for (const socket of sockets) socket.destroy(); server.close(); upstream.close();
    fs.rmSync(dir, {recursive:true, force:true});
  });
  return {...fixture, fixture, seen, connects, ca, upstream:{host:'127.0.0.1', port:upstream.address().port},
    request:(options, callback) => https.request({...options, ca}, callback)};
}

test('DoH pins its public bootstrap, verifies TLS and exact response identity without system DNS', async t => {
  assert.equal(typeof proxy.resolveViaDoh, 'function');
  const fixture = await tlsFixture(t);
  assert.deepEqual(await proxy.resolveViaDoh('www.example.com', fixture.upstream, fixture.request),
    [{address:'93.184.215.14', family:4}]);
  assert.deepEqual(fixture.connects, [{authority:'1.1.1.1:443', host:'1.1.1.1:443'}]);
  assert.deepEqual(fixture.seen, [{host:'cloudflare-dns.com', servername:'cloudflare-dns.com',
    path:'/dns-query?name=www.example.com&type=A'}]);
  await assert.rejects(proxy.resolveViaDoh('www.example.com', fixture.upstream), /certificate|self-signed/i);
  for (const change of [{status:302}, {status:200, contentType:'text/html'},
    {contentType:'application/dns-json', body:'invalid'}, {body:'x'.repeat(65_537)},
    {body:JSON.stringify(answer({Question:[{name:'other.example', type:1}]}))}]) {
    Object.assign(fixture.fixture, change);
    await assert.rejects(proxy.resolveViaDoh('www.example.com', fixture.upstream, fixture.request));
  }
});

test('target TLS retains original Host/SNI, rejects wrong certificates and pins every new connection', async t => {
  assert.equal(typeof proxy.createPinnedAgent, 'function');
  const {default:https} = await import('node:https');
  const fixture = await tlsFixture(t);
  fixture.fixture.body = 'verified target';
  const get = hostname => new Promise((resolve, reject) => {
    const agent = proxy.createPinnedAgent(fixture.upstream, '93.184.215.14', 443, hostname, true);
    const req = https.get({hostname, path:'/test', ca:fixture.ca, agent}, res => {
      res.resume(); res.on('end', () => resolve(res.statusCode));
    });
    req.on('error', reject); req.on('close', () => agent.destroy());
  });
  assert.equal(await get('www.example.com'), 200);
  assert.deepEqual(fixture.seen, [{host:'www.example.com', servername:'www.example.com', path:'/test'}]);
  await assert.rejects(get('other.example'), /hostname|altnames/i);
  assert.deepEqual(fixture.connects, [{authority:'93.184.215.14:443', host:'93.184.215.14:443'},
    {authority:'93.184.215.14:443', host:'93.184.215.14:443'}]);
});

test('policy transport is opt-in, cannot combine fixture bypasses, and keeps denied targets denied', async () => {
  assert.equal(typeof proxy.createProxyTransport, 'function');
  const policy = {allowed_origins:['https://www.example.com'], upstream_proxy:'http://127.0.0.1:7897'};
  assert.throws(() => proxy.createProxyTransport({...policy, test_target:'https://127.0.0.1:9999'}));
  assert.throws(() => proxy.createProxyTransport({...policy, upstream_proxy:'http://user:secret@127.0.0.1:7897'}));
  const transport = proxy.createProxyTransport(policy);
  for (const url of ['http://127.0.0.1', 'https://other.example', 'http://198.18.0.39']) {
    await assert.rejects(transport.destination(new URL(url)));
  }
  const publicTransport = proxy.createProxyTransport({...policy, public_web:true});
  assert.deepEqual(await publicTransport.destination(new URL('https://93.184.215.14')),
    {host:'93.184.215.14', port:443, synthetic:false});
  const direct = proxy.createProxyTransport({allowed_origins:['https://www.example.com']});
  assert.equal(direct.agent(new URL('https://www.example.com'), {host:'93.184.215.14', port:443}), undefined);
});

test('owned policy proxy completes verified HTTPS through upstream and rejects rebinding on the next CONNECT', async t => {
  const fs = await import('node:fs');
  const http = await import('node:http');
  const tls = await import('node:tls');
  const {spawn} = await import('node:child_process');
  const {once} = await import('node:events');
  const fixture = await tlsFixture(t);
  const dir = fs.mkdtempSync('/tmp/tap-upstream-policy-');
  fs.writeFileSync(`${dir}/fixture-ca.pem`, fixture.ca);
  const child = spawn(process.execPath, [new URL('../site-proxy.mjs', import.meta.url).pathname], {
    env:{PATH:process.env.PATH, HOME:process.env.HOME, TAP_BROWSER_LOCAL_DIR:dir,
      // Fixture-only process trust. Production LocalBrowserRuntime strips this variable.
      NODE_EXTRA_CA_CERTS:`${dir}/fixture-ca.pem`, TAP_BROWSER_PROXY_POLICY:JSON.stringify({
        allowed_origins:['https://www.example.com'], upstream_proxy:`http://127.0.0.1:${fixture.upstream.port}`})},
  });
  let stderr = ''; child.stdout.resume(); child.stderr.on('data', data => stderr += data);
  t.after(async () => {
    if (child.exitCode === null) { child.kill('SIGTERM'); await once(child, 'close'); }
    fs.rmSync(dir, {recursive:true, force:true});
  });
  let port;
  for (let i = 0; i < 100; i++) {
    if (fs.existsSync(`${dir}/proxy-port`)) { port = Number(fs.readFileSync(`${dir}/proxy-port`, 'utf8')); break; }
    await new Promise(resolve => setTimeout(resolve, 20));
  }
  assert.ok(port, stderr);
  const connect = (authority = 'www.example.com:443') => new Promise((resolve, reject) => {
    const req = http.request({host:'127.0.0.1', port, method:'CONNECT', path:authority,
      headers:{Host:authority}, agent:false});
    req.on('connect', (res, socket) => resolve({status:res.statusCode, socket})); req.on('error', reject); req.end();
  });
  const request = async host => {
    const connected = await connect();
    assert.equal(connected.status, 200);
    const secure = tls.connect({socket:connected.socket, servername:'www.example.com',
      ca:fs.readFileSync(`${dir}/ca/ca.crt`), rejectUnauthorized:true});
    secure.setTimeout(3000, () => secure.destroy(new Error('fixture request timeout')));
    await once(secure, 'secureConnect');
    let data = ''; secure.on('data', chunk => data += chunk.toString());
    secure.write(`GET /quote HTTP/1.1\r\nHost: ${host}\r\nConnection: close\r\n\r\n`);
    await once(secure, 'end'); secure.destroy(); return data;
  };
  assert.match(await request('www.example.com'), /^HTTP\/1\.1 200/);
  assert.equal(fixture.seen[1].servername, 'www.example.com');
  assert.equal(fixture.seen[1].host, 'www.example.com');
  assert.deepEqual(fixture.connects.map(row => row.authority), ['1.1.1.1:443', '93.184.215.14:443']);
  assert.match(await request('other.example'), /^HTTP\/1\.1 403/);
  fixture.fixture.body = JSON.stringify(answer({Answer:[
    {name:'www.example.com.', type:1, TTL:30, data:'198.18.0.39'}]}));
  const rebound = await connect(); assert.equal(rebound.status, 403); rebound.socket.destroy();
  const denied = await connect('other.example:443'); assert.equal(denied.status, 403); denied.socket.destroy();
  assert.equal(fixture.connects.filter(row => row.authority === '93.184.215.14:443').length, 1);
});

test('public IPv4 excludes documentation and deprecated relay ranges as well as private addresses', () => {
  for (const address of ['192.0.2.1', '198.51.100.1', '203.0.113.1', '192.88.99.1']) {
    assert.equal(proxy.isPublicIP(address), false, address);
    assert.throws(() => proxy.parseDnsAnswer(answer({Answer:[
      {name:'www.example.com.', type:1, TTL:30, data:address}]}), 'www.example.com'));
  }
});

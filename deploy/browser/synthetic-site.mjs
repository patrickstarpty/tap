import http from 'node:http';
import https from 'node:https';
import fs from 'node:fs';
import { execFileSync } from 'node:child_process';

const page = `<!doctype html><html><head><title>Synthetic Tapper Site</title></head><body>
<h1>Order lookup</h1><form id="form"><label>Customer <input name="customer" aria-label="Customer"></label>
<label>Region <select name="region" aria-label="Region"><option value="east">East</option><option value="west">West</option></select></label>
<button type="submit">Find</button></form><button id="custom-region" role="combobox" aria-label="Delivery region" aria-expanded="false">East</button><output id="result"></output>
<script>document.querySelector('#form').addEventListener('submit',event=>{event.preventDefault();
const f=new FormData(event.target);document.querySelector('#result').textContent=f.get('customer')+' / '+f.get('region')+' / FOUND';});</script>
</body></html>`;
const respond = (req, res) => {
  if (req.url === '/slow-script.js') {
    setTimeout(() => {
      res.writeHead(200, { 'content-type': 'text/javascript' });
      res.end('// delayed deferred script');
    }, 20_000);
    return;
  }
  if (req.url === '/slow-image.svg') {
    setTimeout(() => {
      res.writeHead(200, { 'content-type': 'image/svg+xml' });
      res.end('<svg xmlns="http://www.w3.org/2000/svg" width="1" height="1"/>');
    }, 20_000);
    return;
  }
  res.writeHead(200, { 'content-type': 'text/html; charset=utf-8' });
  if (req.url === '/delayed-dom') {
    res.end(page.replace('</head>', '<script defer src="/slow-script.js"></script></head>'));
    return;
  }
  res.end(req.url === '/delayed-load'
    ? page.replace('</body>', '<img src="/slow-image.svg" alt="">\n</body>')
    : page);
};
http.createServer(respond).listen(8080, '0.0.0.0');
execFileSync('openssl', ['req', '-x509', '-newkey', 'rsa:2048', '-nodes',
  '-keyout', '/tmp/site.key', '-out', '/tmp/site.crt', '-days', '1',
  '-subj', '/CN=example.test', '-addext', 'subjectAltName=DNS:example.test',
  '-addext', 'basicConstraints=critical,CA:TRUE'], { stdio: 'ignore', timeout: 10_000 });
https.createServer({ key: fs.readFileSync('/tmp/site.key'),
  cert: fs.readFileSync('/tmp/site.crt') }, respond).listen(8443, '0.0.0.0');

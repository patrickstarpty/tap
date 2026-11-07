import http from 'node:http';
import https from 'node:https';
import fs from 'node:fs';
import { execFileSync } from 'node:child_process';

const page = `<!doctype html><html><head><title>Synthetic Tapper Site</title></head><body>
<h1>Order lookup</h1><form id="form"><label>Customer <input name="customer" aria-label="Customer"></label>
<label>Region <select name="region" aria-label="Region"><option value="east">East</option><option value="west">West</option></select></label>
<button type="submit">Find</button></form><output id="result"></output>
<script>document.querySelector('#form').addEventListener('submit',event=>{event.preventDefault();
const f=new FormData(event.target);document.querySelector('#result').textContent=f.get('customer')+' / '+f.get('region')+' / FOUND';});</script>
</body></html>`;
const respond = (req, res) => {
  res.writeHead(200, { 'content-type': 'text/html; charset=utf-8' });
  res.end(page);
};
http.createServer(respond).listen(8080, '0.0.0.0');
execFileSync('openssl', ['req', '-x509', '-newkey', 'rsa:2048', '-nodes',
  '-keyout', '/tmp/site.key', '-out', '/tmp/site.crt', '-days', '1',
  '-subj', '/CN=example.test', '-addext', 'subjectAltName=DNS:example.test',
  '-addext', 'basicConstraints=critical,CA:TRUE'], { stdio: 'ignore', timeout: 10_000 });
https.createServer({ key: fs.readFileSync('/tmp/site.key'),
  cert: fs.readFileSync('/tmp/site.crt') }, respond).listen(8443, '0.0.0.0');

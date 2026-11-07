import http from 'node:http';

const page = `<!doctype html><html><head><title>Synthetic Tapper Site</title></head><body>
<h1>Order lookup</h1><form id="form"><label>Customer <input name="customer" aria-label="Customer"></label>
<label>Region <select name="region" aria-label="Region"><option value="east">East</option><option value="west">West</option></select></label>
<button type="submit">Find</button></form><output id="result"></output>
<script>document.querySelector('#form').addEventListener('submit',event=>{event.preventDefault();
const f=new FormData(event.target);document.querySelector('#result').textContent=f.get('customer')+' / '+f.get('region')+' / FOUND';});</script>
</body></html>`;
http.createServer((req, res) => {
  res.writeHead(200, { 'content-type': 'text/html; charset=utf-8' });
  res.end(page);
}).listen(8080, '0.0.0.0');

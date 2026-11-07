import http from 'node:http';
import { randomBytes } from 'node:crypto';

// Owned acceptance fixture. The order code changes on every site process start.
const code = `ORDER-${randomBytes(4).toString('hex').toUpperCase()}`;
const pages = {
  '/': `<!doctype html><html><head><title>Tapper Core Site</title></head><body>
    <h1>Order lookup</h1><form action="/lookup" method="get">
      <label>Customer <input name="customer" required></label>
      <label>Region <select name="region"><option value="east">East</option><option value="west">West</option></select></label>
      <button type="submit">Find order</button></form>
    <a href="/review">Review request</a><a href="/journey">Start journey</a>
    </body></html>`,
  '/review': `<!doctype html><html><head><title>Review request</title></head><body>
    <h1>Request review</h1><form action="/review-result" method="get">
      <label>Account <input name="account" required></label>
      <label>Amount <input name="amount" type="number" min="1" required></label>
      <button type="submit">Check request</button></form></body></html>`,
  '/journey': `<!doctype html><html><head><title>Start journey</title></head><body>
    <h1>Travel checklist</h1><a href="/journey/details">See details</a></body></html>`,
  '/journey/details': `<!doctype html><html><head><title>Journey details</title></head><body>
    <h1>Journey details</h1><a href="/journey/finish">Finish review</a></body></html>`,
  '/journey/finish': `<!doctype html><html><head><title>Journey complete</title></head><body>
    <h1>Review complete</h1><output id="journey-result">Three pages reviewed</output></body></html>`,
};

http.createServer((request, response) => {
  const url = new URL(request.url, 'http://example.test');
  let body = pages[url.pathname];
  if (url.pathname === '/lookup') {
    const customer = url.searchParams.get('customer') ?? '';
    const region = url.searchParams.get('region') ?? '';
    body = `<!doctype html><html><head><title>Order result</title></head><body>
      <h1>Order result</h1><output id="result">${customer} / ${region} / ${code}</output></body></html>`;
  } else if (url.pathname === '/review-result') {
    const account = url.searchParams.get('account') ?? '';
    const amount = url.searchParams.get('amount') ?? '';
    body = `<!doctype html><html><head><title>Review result</title></head><body>
      <h1>Review result</h1><output id="result">${account} / ${amount} / MATCH</output></body></html>`;
  }
  response.writeHead(body ? 200 : 404, { 'content-type': 'text/html; charset=utf-8' });
  response.end(body ?? '<h1>Not found</h1>');
}).listen(8080, '0.0.0.0');

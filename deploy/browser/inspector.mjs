import fs from 'node:fs';
import net from 'node:net';
import { chromium } from '@playwright/test';

const socketPath = '/tmp/inspector.sock';
const proxy = process.env.TAP_BROWSER_PROXY;
const context = await chromium.launchPersistentContext('/home/browser/profile', {
  headless: true, chromiumSandbox: true, proxy: { server: proxy },
  viewport: { width: 1280, height: 800 },
});
const page = context.pages()[0] || await context.newPage();
let sequence = 0;
const bounded = (value, max = 16_000) => String(value || '').slice(0, max);

async function observe(action) {
  let screenshot_ref = null;
  let screenshot_data = null;
  if (!['navigate', 'snapshot', 'click', 'fill', 'select', 'press', 'wait', 'screenshot'].includes(action.type)) {
    throw new Error('unsupported action');
  }
  if (action.type === 'navigate') await page.goto(action.url, { timeout: 15_000 });
  if (action.type === 'click') await page.locator(action.selector).click({ timeout: 10_000 });
  if (action.type === 'fill') await page.locator(action.selector).fill(bounded(action.value, 4000), { timeout: 10_000 });
  if (action.type === 'select') await page.locator(action.selector).selectOption(bounded(action.value, 1000), { timeout: 10_000 });
  if (action.type === 'press') await page.locator(action.selector).press(bounded(action.key, 100), { timeout: 10_000 });
  if (action.type === 'wait') await page.locator(action.selector).waitFor({ timeout: 10_000 });
  if (action.type === 'screenshot') {
    sequence++;
    const image = await page.screenshot({ fullPage: false, timeout: 10_000 });
    screenshot_data = image.toString('base64');
    screenshot_ref = null;
  } else sequence++;
  const fields = await page.locator('input,select,textarea').evaluateAll(nodes => nodes.slice(0, 50).map(node => ({
    tag: node.tagName.toLowerCase(), name: node.getAttribute('name'), id: node.id,
    type: node.getAttribute('type'), label: node.getAttribute('aria-label'),
    options: node.tagName === 'SELECT' ? [...node.options].slice(0, 30).map(o => ({ label: o.label, value: o.value })) : [],
  })));
  const snapshot = await page.locator('body').innerText({ timeout: 10_000 }).catch(() => '');
  return { session_id: process.env.TAP_BROWSER_SESSION, sequence, url: bounded(page.url(), 2048),
    title: bounded(await page.title(), 256), bounded_snapshot: bounded(snapshot), fields,
    screenshot_ref, screenshot_data, error: null };
}

try { fs.unlinkSync(socketPath); } catch {}
net.createServer(client => {
  let data = '';
  client.on('data', async chunk => {
    data += chunk;
    if (data.length > 16_384) { client.destroy(); return; }
    if (!data.endsWith('\n')) return;
    try { client.end(JSON.stringify(await observe(JSON.parse(data)))); }
    catch (error) { client.end(JSON.stringify({ session_id: process.env.TAP_BROWSER_SESSION,
      sequence: ++sequence, url: bounded(page.url(), 2048), title: '', bounded_snapshot: '',
      fields: [], screenshot_ref: null, error: bounded(error.message, 1000) })); }
  });
}).listen(socketPath);

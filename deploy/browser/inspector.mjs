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
  let action_error = null;
  try {
  if (!['navigate', 'snapshot', 'click', 'fill', 'select', 'press', 'wait', 'screenshot'].includes(action.type)) {
    throw new Error('unsupported action');
  }
  if (action.type === 'navigate') {
    await page.goto(action.url, { timeout: 15_000, waitUntil: 'domcontentloaded' });
  }
  if (action.type === 'click') await page.locator(action.selector).click({ timeout: 10_000 });
  if (action.type === 'fill') await page.locator(action.selector).fill(bounded(action.value, 4000), { timeout: 10_000 });
  if (action.type === 'select') await page.locator(action.selector).selectOption(bounded(action.value, 1000), { timeout: 10_000 });
  if (action.type === 'press') await page.locator(action.selector).press(bounded(action.key, 100), { timeout: 10_000 });
  if (action.type === 'wait') await page.locator(action.selector).waitFor({ timeout: 10_000 });
  if (action.type === 'screenshot') {
    const image = await page.screenshot({ fullPage: false, timeout: 10_000 });
    screenshot_data = image.toString('base64');
    screenshot_ref = null;
  }
  } catch (error) {
    action_error = bounded(error.message, 1000);
  }
  sequence++;
  const fields = await page.locator('input,select,textarea,button,a[href],[role="combobox"],[role="radio"],[role="checkbox"],[role="option"],[contenteditable="true"]').evaluateAll(nodes => {
    const unique = selector => document.querySelectorAll(selector).length === 1;
    const selectorFor = node => {
      if (node.id && unique(`#${CSS.escape(node.id)}`)) return `#${CSS.escape(node.id)}`;
      for (const attribute of ['data-testid', 'name', 'aria-label']) {
        const value = node.getAttribute(attribute);
        if (!value) continue;
        const candidate = `${node.tagName.toLowerCase()}[${attribute}=${JSON.stringify(value)}]`;
        if (unique(candidate)) return candidate;
      }
      const parts = [];
      for (let element = node; element && element !== document.body; element = element.parentElement) {
        const siblings = [...element.parentElement.children].filter(item => item.tagName === element.tagName);
        parts.unshift(`${element.tagName.toLowerCase()}:nth-of-type(${siblings.indexOf(element) + 1})`);
        const candidate = parts.join(' > ');
        if (unique(candidate)) return candidate;
      }
      return `body > ${parts.join(' > ')}`;
    };
    return nodes.filter(node => node.getClientRects().length && getComputedStyle(node).visibility !== 'hidden')
      .sort((a, b) => Number(a.tagName === 'A') - Number(b.tagName === 'A'))
      .slice(0, 100).map(node => ({
        tag: node.tagName.toLowerCase(), name: node.getAttribute('name'), id: node.id,
        type: node.getAttribute('type'), role: node.getAttribute('role'), selector: selectorFor(node),
        label: (node.getAttribute('aria-label') || (node.getAttribute('aria-labelledby') || '').split(/\s+/).map(id => document.getElementById(id)?.textContent || '').join(' ').trim() || [...(node.labels || [])].map(label => label.textContent).join(' ') || node.textContent || '').trim().slice(0, 200),
        value: node.type === 'password' ? '[hidden]' : String(node.value ?? '').slice(0, 200),
        checked: node.checked ?? node.getAttribute('aria-checked'), disabled: Boolean(node.disabled),
        options: node.tagName === 'SELECT' ? [...node.options].slice(0, 30).map(o => ({ label: o.label, value: o.value })) : [],
      }));
  }).catch(() => []);
  // Official Playwright CLI uses this same AI snapshot API and aria-ref engine.
  // Re-observe after every action so references describe the current page.
  const snapshotRoot = action.type === 'snapshot' && action.selector ? page.locator(action.selector) : page;
  const snapshot = await snapshotRoot.ariaSnapshot({ mode: 'ai', timeout: 10_000 }).catch(() => '');
  return { session_id: process.env.TAP_BROWSER_SESSION, sequence, url: bounded(page.url(), 2048),
    title: bounded(await page.title().catch(() => ''), 256), bounded_snapshot: bounded(snapshot), fields,
    screenshot_ref, screenshot_data, error: action_error };
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

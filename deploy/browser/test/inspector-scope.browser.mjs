import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';
import { chromium } from '@playwright/test';
import { observeFields, readValue, guardTarget, selectValue } from '../page-actions.mjs';

// Exercise the production observation handler with a real browser, excluding only
// process/socket bootstrap. This suite is explicitly run, not part of node:test's
// default *.test.mjs gate.
async function inspector(t, iframe) {
  const browser = await chromium.launch({headless:true});
  t.after(() => browser.close());
  const context = await browser.newContext();
  const page = await context.newPage();
  const content = '<section aria-label="Scope"><button>Inside</button></section><button>Outside</button>';
  if (iframe) {
    await page.setContent('<h1>Main page</h1><iframe></iframe>');
    await page.frames().find(frame=>frame!==page.mainFrame()).setContent(content);
  } else {
    await page.setContent(content);
  }
  const source = fs.readFileSync(new URL('../inspector.mjs', import.meta.url), 'utf8')
    .replace(/^import .*;\n/gm, '').split('try { fs.unlinkSync(socketPath); }')[0];
  const observe = await new (Object.getPrototypeOf(async function(){}).constructor)(
    'fs', 'net', 'chromium', 'observeFields', 'readValue', 'guardTarget', 'selectValue', 'process',
    source + '\nreturn observe;'
  )(fs, {}, {launchPersistentContext:async()=>context}, observeFields, readValue, guardTarget, selectValue,
    {env:{TAP_BROWSER_LOCAL_DIR:'/tmp/inspector-scope-no-files'}});
  return {page, observe};
}

function ref(snapshot, label) {
  const line = snapshot.split('\n').find(line=>line.includes(`"${label}"`));
  return line?.match(/\[ref=([^\]]+)\]/)?.[1];
}

for (const iframe of [false, true]) {
  const location = iframe ? 'iframe' : 'main frame';
  test(`${location}: scoped observation preserves current DOM refs outside its scope`, async t => {
    const {page, observe} = await inspector(t, iframe);
    const full = await observe({type:'snapshot'});
    const scope = ref(full.bounded_snapshot, 'Scope');
    const outside = ref(full.bounded_snapshot, 'Outside');
    assert.ok(scope && outside);
    const scoped = await observe({type:'snapshot', selector:`aria-ref=${scope}`});
    assert.equal(scoped.error, null);
    assert.match(scoped.bounded_snapshot, /Inside/);
    assert.doesNotMatch(scoped.bounded_snapshot, /Outside/);
    assert.equal(await page.locator(`aria-ref=${outside}`).count(), 1,
      'merely observing a scope must not invalidate another current element');
    const read = await observe({type:'read', selector:`aria-ref=${outside}`, name:'outside', attribute:'text'});
    assert.equal(read.error, null);
    assert.equal(read.extracted.value, 'Outside');
  });

  test(`${location}: failed scoped observation preserves its error and returns usable full refs`, async t => {
    const {page, observe} = await inspector(t, iframe);
    const full = await observe({type:'snapshot'});
    const outside = ref(full.bounded_snapshot, 'Outside');
    const missing = iframe ? outside.replace(/e\d+$/, 'e999999') : 'e999999';
    const result = await observe({type:'snapshot', selector:`aria-ref=${missing}`});
    assert.match(result.error, /does not match any element/);
    assert.match(result.bounded_snapshot, /Outside/);
    const recovered = ref(result.bounded_snapshot, 'Outside');
    assert.equal(await page.locator(`aria-ref=${recovered}`).innerText({timeout:1000}), 'Outside');
  });
}

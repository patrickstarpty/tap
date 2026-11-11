import assert from 'node:assert/strict';
import test from 'node:test';
import {createRequire} from 'node:module';
import {createDocumentProvenance} from '../document-provenance.mjs';
import {readValue} from '../page-actions.mjs';
const require = createRequire(new URL('../../../apps/tap-ai-frontend/package.json', import.meta.url));
const {chromium} = require('@playwright/test');

test('actual iframe Documents distinguish navigation, same-document history, reload and detach', async () => {
  const browser = await chromium.launch({headless:true});
  try {
    const page = await browser.newPage();
    await page.route('https://fixture.test/**', route => route.fulfill({contentType:'text/html', body:
      new URL(route.request().url()).pathname === '/' ? '<iframe id="outer" src="/parent"></iframe>' :
      new URL(route.request().url()).pathname === '/parent' ? '<iframe id="inner" src="/first"></iframe>' :
      '<output id="value">42</output>'}));
    await page.goto('https://fixture.test/');
    const provenance = createDocumentProvenance(page);
    const initial = await provenance.snapshot();
    assert.equal(initial.length, 3);
    const first = initial.find(item => item.url.endsWith('/first'));
    const selector = '#outer >> internal:control=enter-frame >> #inner >> internal:control=enter-frame >> #value';
    const read = await provenance.read({selector}, readValue);
    assert.equal(read.source_url, 'https://fixture.test/first');
    assert.equal(read.source_document_id, first.document_id);
    assert.equal(read.source_frame_id, first.frame_id);
    const frame = page.frames().find(frame => frame.url().endsWith('/first'));
    await frame.evaluate(() => { history.pushState({}, '', '/same'); location.hash = 'hash'; });
    const same = (await provenance.snapshot()).find(item => item.frame_id === first.frame_id);
    assert.equal(same.document_id, first.document_id);
    assert.equal(same.url, 'https://fixture.test/same#hash');
    await frame.goto('https://fixture.test/next');
    const next = (await provenance.snapshot()).find(item => item.frame_id === first.frame_id);
    assert.equal(page.url(), 'https://fixture.test/');
    assert.notEqual(next.document_id, first.document_id);
    await frame.goto(frame.url());
    const reload = (await provenance.snapshot()).find(item => item.frame_id === first.frame_id);
    assert.equal(reload.url, next.url);
    assert.notEqual(reload.document_id, next.document_id);
    await assert.rejects(provenance.read({selector}, async () => { await frame.goto('https://fixture.test/raced'); return '42'; }), /read document changed/);
    const parent = frame.parentFrame();
    await parent.locator('#inner').evaluate(node => node.remove());
    assert.ok(!(await provenance.snapshot()).some(item => item.frame_id === first.frame_id));
  } finally { await browser.close(); }
});

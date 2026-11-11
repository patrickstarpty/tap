import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import test from 'node:test';
import {createDocumentProvenance} from '../document-provenance.mjs';
import {createLiveControl, serialDispatcher} from '../live-control.mjs';

// Run the real inspector action handler with controlled browser lifecycle events.
async function inspector({ crashDuringSnapshot = false, snapshotError = false, localRoot, headless, controlled, now = () => '2026-11-09T00:00:00.000Z' } = {}) {
  let launch;
  let closed = false;
  let crash;
  let url = 'https://example.com/quote';
  let documentVersion = 0;
  const page = {
    on: (event, callback) => { if (event === 'crash') crash = callback; },
    url: () => url,
    goto: async next => { url = next; documentVersion++; },
    title: async () => 'Quote',
    screenshot: async () => Buffer.from('png'),
    video: () => ({ path: async () => '/tmp/video.webm' }),
    ariaSnapshot: async () => {
      if (closed) throw new Error('Target page, context or browser has been closed');
      if (crashDuringSnapshot) { crash(); throw new Error('Target crashed'); }
      if (snapshotError) throw new Error('snapshot unavailable');
      return '- heading "Quote"';
    },
  };
  page.frames = () => closed ? [] : [page];
  page.isDetached = () => closed;
  page.evaluateHandle = async () => {
    const captured = documentVersion;
    return {evaluate:async () => !closed && captured === documentVersion, dispose:async () => {}};
  };
  page.locator = () => ({elementHandle:async () => ({ownerFrame:async () => page, evaluate:async () => true, dispose:async () => {}})});
  const context = {
    pages: () => [page],
    close: async () => { closed = true; },
    tracing: { start: async () => {}, stop: async () => {} },
  };
  const source = (await fs.readFile(new URL('../inspector.mjs', import.meta.url), 'utf8'))
    .replace(/^import .*;\n/gm, '').split('try { fs.unlinkSync(socketPath); }')[0];
  const {handler,requestFailure} = await new (Object.getPrototypeOf(async function() {}).constructor)(
    'fs', 'net', 'chromium', 'observeFields', 'readValue', 'guardTarget', 'selectValue', 'process', 'Date', 'createLiveControl', 'serialDispatcher', 'createDocumentProvenance',
    source + '\nreturn {handler:observe,requestFailure:controlledRequestFailure};'
  )(
    { readFileSync: () => Buffer.from('evidence') },
    { createServer: () => ({ listen: () => {} }) },
    { launchPersistentContext: async (profile, options) => { launch = {profile, options}; return context; } },
    async () => [], async (_page, action) => {
      if (action.selector === '#missing') throw new Error('read failed');
      return action.selector === '#age' ? '30' : '10.62';
    },
    async (_page, action) => { if (action.selector === '#wrong') throw new Error('target mismatch'); },
    async () => {},
    {env:{TAP_BROWSER_LOCAL_DIR:localRoot, TAP_BROWSER_LOCAL_HEADLESS:headless, TAP_BROWSER_CONTROLLED:controlled?'1':undefined}},
    class extends Date { constructor() { super(now()); } }, createLiveControl, serialDispatcher, createDocumentProvenance,
  );
  return { handler, requestFailure, launch, crash: () => crash(), closed: () => closed };
}

test('finish retains successful evidence after closing the recording context', async () => {
  const { handler, closed } = await inspector();
  await handler({ type: 'read', selector: '#quote', name: 'premium' });
  const result = await handler({ type: 'finish' });
  assert.equal(result.error, null);
  const output = result.result_data.outputs[0];
  assert.equal(output.premium, '10.62');
  assert.equal(output.final_url, 'https://example.com/quote');
  assert.deepEqual(output.provenance.premium, {
    source_frame_id: result.documents[0].frame_id, source_document_id: result.documents[0].document_id,
    source_url: 'https://example.com/quote', observation_sequence: 1,
    observed_at: '2026-11-09T00:00:00.000Z',
  });
  assert.equal(Object.hasOwn(output, 'source_url'), false);
  assert.equal(Object.hasOwn(output, 'observed_at'), false);
  assert.ok(result.screenshot_data && result.trace_data && result.video_data);
  assert.equal(closed(), true);
});

test('recording cleanup remains available after a page crash', async () => {
  const { handler, crash, closed } = await inspector();
  crash();
  await assert.rejects(handler({ type: 'snapshot' }), /browser page crashed/);
  const result = await handler({ type: 'recording' });
  assert.equal(result.error, null);
  assert.ok(result.video_data);
  assert.equal(closed(), true);
});

test('a crash during observation is fatal even after an earlier recoverable error', async () => {
  const { handler } = await inspector({ crashDuringSnapshot: true });
  const result = await handler({ type: 'read', selector: '#wrong', name: 'premium' });
  assert.match(result.error, /^browser page crashed\n/);
  assert.match(result.error, /target mismatch/);
});


test('local visual debugging keeps the private profile and dynamic loopback CDP', async () => {
  const { launch } = await inspector({localRoot:'/tmp/owned-browser', headless:'false'});
  assert.equal(launch.profile, '/tmp/owned-browser/profile');
  assert.equal(launch.options.headless, false);
  assert.equal(launch.options.recordVideo.dir, '/tmp/owned-browser/videos');
  assert.deepEqual(launch.options.args, ['--remote-debugging-port=0']);
});


test('finish preserves each field read source and time across two pages', async () => {
  let time = '2026-11-09T01:00:00.000Z';
  const { handler } = await inspector({ now: () => time });
  await handler({ type: 'navigate', url: 'https://example.com/form' });
  const age = await handler({ type: 'read', selector: '#age', name: 'age' });
  time = '2026-11-09T01:01:00.000Z';
  await handler({ type: 'navigate', url: 'https://example.com/quote' });
  const premium = await handler({ type: 'read', selector: '#quote', name: 'premium' });
  time = '2026-11-09T01:02:00.000Z';
  const result = await handler({ type: 'finish' });
  assert.equal(result.error, null);
  const output = result.result_data.outputs[0];
  assert.equal(output.age, '30');
  assert.equal(output.premium, '10.62');
  assert.equal(output.final_url, 'https://example.com/quote');
  assert.equal(Object.hasOwn(output, 'source_url'), false);
  assert.equal(Object.hasOwn(output, 'observed_at'), false);
  assert.deepEqual(output.provenance, {
    age: { source_frame_id:age.extracted.source_frame_id, source_document_id:age.extracted.source_document_id, source_url: 'https://example.com/form', observation_sequence: 2, observed_at: '2026-11-09T01:00:00.000Z' },
    premium: { source_frame_id:premium.extracted.source_frame_id, source_document_id:premium.extracted.source_document_id, source_url: 'https://example.com/quote', observation_sequence: 4, observed_at: '2026-11-09T01:01:00.000Z' },
  });
  assert.deepEqual(age.extracted, { name: 'age', value: '30', ...output.provenance.age });
  assert.deepEqual(premium.extracted, { name: 'premium', value: '10.62', ...output.provenance.premium });
  assert.equal(age.extracted.observation_sequence, age.sequence);
  assert.equal(premium.extracted.observation_sequence, premium.sequence);
});

for (const [selector, error] of [['#wrong', /target mismatch/], ['#missing', /read failed/]]) {
  test(`failed reread ${selector} removes the old value and its provenance`, async () => {
    const { handler } = await inspector();
    await handler({ type: 'read', selector: '#age', name: 'age' });
    await handler({ type: 'read', selector: '#quote', name: 'premium' });
    const failed = await handler({ type: 'read', selector, name: 'age' });
    assert.match(failed.error, error);
    assert.equal(failed.extracted, null);
    const result = await handler({ type: 'finish' });
    assert.equal(result.error, null);
    const output = result.result_data.outputs[0];
    assert.equal(Object.hasOwn(output, 'age'), false);
    assert.ok(output.provenance, 'remaining values must retain field provenance');
    assert.equal(Object.hasOwn(output.provenance, 'age'), false);
    assert.equal(output.premium, '10.62');
    assert.deepEqual(Object.keys(output.provenance), ['premium']);
  });
}

test('finish rejects an aggregate whose only read failed its target guard on retry', async () => {
  const { handler } = await inspector();
  await handler({ type: 'read', selector: '#quote', name: 'premium' });
  await handler({ type: 'read', selector: '#wrong', name: 'premium' });
  const result = await handler({ type: 'finish' });
  assert.match(result.error, /no observed business results/);
  assert.equal(result.result_data, null);
});

test('output names cannot overwrite provenance metadata', async () => {
  const { handler } = await inspector();
  await handler({ type: 'read', selector: '#quote', name: 'premium' });
  for (const name of ['provenance', 'final_url', 'source_url', 'observed_at', 'observation_sequence']) {
    const result = await handler({ type: 'read', selector: '#age', name });
    assert.match(result.error, /invalid output name/);
    assert.equal(result.extracted, null);
  }
  const result = await handler({ type: 'finish' });
  assert.equal(result.error, null);
  assert.equal(result.result_data.outputs[0].premium, '10.62');
  assert.deepEqual(Object.keys(result.result_data.outputs[0].provenance), ['premium']);
});

test('prototype-like output names retain their own values and provenance', async () => {
  const { handler } = await inspector();
  await handler({ type: 'read', selector: '#age', name: '__proto__' });
  const result = await handler({ type: 'finish' });
  assert.equal(result.error, null);
  const output = JSON.parse(JSON.stringify(result.result_data.outputs[0]));
  assert.equal(Object.hasOwn(output, '__proto__'), true);
  assert.equal(output.__proto__, '30');
  assert.equal(output.provenance.__proto__.source_url, 'https://example.com/quote');
});

test('read provenance sequence matches its response even when the later snapshot fails', async () => {
  const { handler } = await inspector({ snapshotError: true });
  const result = await handler({ type: 'read', selector: '#quote', name: 'premium' });
  assert.match(result.error, /snapshot unavailable/);
  assert.equal(result.extracted.observation_sequence, result.sequence);
});

 test('controlled inspector exposes neither raw CDP nor continuous video', async()=>{
  const {launch}=await inspector({localRoot:'/tmp/controlled',controlled:true});
  assert.deepEqual(launch.options.args,[]);
  assert.equal(launch.options.recordVideo,undefined);
});

for(const [message,category] of [
  ['target not actionable','target_not_actionable'],
  ['target obscured or outside viewport','target_obscured_or_outside_viewport'],
  ['bound target changed','target_changed'],
  ['bound target type changed','target_changed'],
  ['sensitive target unavailable','sensitive_target'],
  ['human capability expired','authority_expired'],
  ['controlled scroll did not settle','scroll_settle_timeout'],
  ['frame target proof changed','frame_target_changed'],
]) test(`controlled rejection reports only the fixed ${category} category for ${message}`,async()=>{
  const {handler}=await inspector({controlled:true});
  for(const prefix of ['', 'elementHandle.evaluate: Error: ', 'jsHandle.evaluate: Error: ']) {
    const result=await handler({type:'click',selector:'#safe'},async()=>{throw new Error(`${prefix}${message}\nPRIVATE_STACK_MARKER`);});
    assert.equal(result.error,`controlled action outcome unknown: ${category}`);
    assert.equal(result.extracted,undefined);
    assert.deepEqual(result.fields,[]);
    assert.equal(result.bounded_snapshot,'');
    assert.equal(JSON.stringify(result).includes('PRIVATE_STACK_MARKER'),false);
  }
});

test('unknown controlled exceptions never project exception text or input values',async()=>{
  const {handler}=await inspector({controlled:true});
  for(const message of ['PRIVATE_VALUE_MARKER', 'target not actionable PRIVATE_VALUE_MARKER', 'PRIVATE_VALUE_MARKER\ntarget not actionable', 'controlled action outcome unknown', 'constructor']) {
    const result=await handler({type:'fill',selector:'#safe',value:'PRIVATE_VALUE_MARKER'},async()=>{throw new Error(message);});
    assert.equal(result.error,'controlled action outcome unknown');
    assert.equal(JSON.stringify(result).includes('PRIVATE_VALUE_MARKER'),false);
    assert.deepEqual(result.fields,[]);
  }
});

for(const [message,category] of [
  ['stale frame; frame unavailable','frame_unavailable'],
  ['stale frame; control generation changed','frame_control_generation_changed'],
  ['stale frame; frame expired','frame_expired'],
  ['stale frame; page revision changed','frame_revision_changed'],
  ['frame target proof changed','frame_target_changed'],
  ['capture fence timed out','capture_timeout'],
  ['capture fence restoration failed','capture_restore_failed'],
  ['viewport mismatch','viewport_mismatch'],
  ['stale or missing control generation','control_generation_stale'],
  ['human capability expired','authority_expired'],
  ['target missing or ambiguous','target_missing_or_ambiguous'],
  ['sensitive target unavailable','sensitive_target'],
  ['nested frame target unsupported in live control','nested_frame_unsupported'],
]) test(`outer controlled request rejection reports only fixed category ${category}`,async()=>{
  const {requestFailure}=await inspector({controlled:true});
  for(const prefix of ['', 'elementHandle.evaluate: Error: ', 'jsHandle.evaluate: Error: ']) {
    const result=requestFailure(new Error(`${prefix}${message}\nPRIVATE_SELECTOR_VALUE_MARKER`));
    assert.equal(result,`controlled browser request rejected: ${category}`);
    assert.equal(result.includes('PRIVATE_SELECTOR_VALUE_MARKER'),false);
  }
});

test('outer request catch leaves unrecognized and post-action errors generic',async()=>{
  const {requestFailure}=await inspector({controlled:true});
  for(const message of ['PRIVATE_VALUE_MARKER','stale frame; page revision changed PRIVATE_VALUE_MARKER','PRIVATE_VALUE_MARKER\nstale frame; page revision changed','controlled scroll did not settle','controlled action outcome unknown','constructor']) {
    assert.equal(requestFailure(new Error(message)),'controlled browser request rejected');
  }
});

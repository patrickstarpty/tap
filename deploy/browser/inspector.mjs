import fs from 'node:fs';
import { createDocumentProvenance } from './document-provenance.mjs';
import {createLiveControl, serialDispatcher, preActuationFailure} from './live-control.mjs';
import net from 'node:net';
import { observeFields, readValue, guardTarget, selectValue } from './page-actions.mjs';
import { chromium } from '@playwright/test';

const controlled = process.env.TAP_BROWSER_CONTROLLED === '1';
const localRoot = process.env.TAP_BROWSER_LOCAL_DIR;
const socketPath = process.env.TAP_BROWSER_SOCKET || '/tmp/inspector.sock';
const tracePath = localRoot ? `${localRoot}/session-trace.zip` : '/tmp/session-trace.zip';
const proxy = process.env.TAP_BROWSER_PROXY;
const context = await chromium.launchPersistentContext(localRoot ? `${localRoot}/profile` : '/home/browser/profile', {
  headless: !localRoot || process.env.TAP_BROWSER_LOCAL_HEADLESS !== 'false', chromiumSandbox: true, proxy: { server: proxy },
  ...(localRoot ? {ignoreHTTPSErrors:true} : {}),
  ...(process.env.TAP_BROWSER_LOCAL_EXECUTABLE_PATH ? {executablePath:process.env.TAP_BROWSER_LOCAL_EXECUTABLE_PATH} : {}),
  args: controlled ? [] : [localRoot ? '--remote-debugging-port=0' : '--remote-debugging-port=9222'],
  viewport: { width: 1280, height: 800 },
  ...(!controlled ? {recordVideo: { dir: localRoot ? `${localRoot}/videos` : '/tmp/browser-videos', size: { width: 1280, height: 800 } }} : {}),
});
const page = context.pages()[0] || await context.newPage();
const documentProvenance = createDocumentProvenance(page);
let pageCrashed = false;
page.on('crash', () => { pageCrashed = true; });
// Chromium binds CDP to loopback; Docker publishes only the task's host-loopback port.
if (!localRoot && !controlled) net.createServer(client => {
  const upstream = net.connect(9222, '127.0.0.1');
  client.pipe(upstream).pipe(client);
  client.on('error', () => upstream.destroy());
  upstream.on('error', () => client.destroy());
  client.on('close', () => upstream.destroy());
}).listen(9223, '0.0.0.0');
let sequence = 0;
const resultValues = Object.create(null);
const resultProvenance = Object.create(null);
const reservedOutputNames = new Set(['final_url', 'provenance', 'source_url', 'observed_at', 'observation_sequence', 'source_frame_id', 'source_document_id']);
let finished = false;
let video_data = null;
async function finishRecording() {
  if (controlled) { await context.close(); return; }
  if (video_data) return;
  const video = page.video();
  await context.close();
  video_data = fs.readFileSync(await video.path()).toString('base64');
}
await context.tracing.start({screenshots:true, snapshots:true});
let traceActive = true;
const bounded = (value, max = 16_000) => String(value || '').slice(0, max);
const controlledFailureCategories = new Map([
  ['controlled navigation wait timeout','navigation_wait_timeout'],
  ['controlled navigation wait failed','navigation_wait_failed'],
  ['target not actionable','target_not_actionable'],
  ['target obscured or outside viewport','target_obscured_or_outside_viewport'],
  ['bound target changed','target_changed'],
  ['bound target type changed','target_changed'],
  ['sensitive target unavailable','sensitive_target'],
  ['human capability expired','authority_expired'],
  ['controlled scroll did not settle','scroll_settle_timeout'],
  ['frame target proof changed','frame_target_changed'],
]);
function controlledFailure(error) {
  // Playwright wraps fixed callback errors with this prefix and a stack. Never
  // project arbitrary exception text, locators or controlled input arguments.
  const message=typeof error?.message==='string'?error.message.split('\n',1)[0].replace(/^(?:elementHandle|jsHandle)\.evaluate: Error: /,''):'';
  const category=controlledFailureCategories.get(message);
  return 'controlled action outcome unknown'+(category?`: ${category}`:'');
}
const controlledRequestFailureCategories = new Map([
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
]);
function controlledRequestFailure(error) {
  // Diagnostic categories only: this does not assert retry safety or change a
  // receipt's unknown status. Arbitrary errors never cross the private socket.
  const message=typeof error?.message==='string'?error.message.split('\n',1)[0].replace(/^(?:elementHandle|jsHandle)\.evaluate: Error: /,''):'';
  const category=controlledRequestFailureCategories.get(message);
  return 'controlled browser request rejected'+(category?`: ${category}`:'');
}

async function observe(action, actuate) {
  if (pageCrashed && action.type !== 'recording') throw new Error('browser page crashed');
  let screenshot_ref = null;
  let screenshot_data = null;
  let action_error = null;
  // Only this trusted direct inspector can prove that no write call began.
  // Controlled input paths retain their separate, conservative outcome fence.
  let write_started = !controlled && !actuate && ['navigate','click','fill','select','press','scroll'].includes(action.type) ? false : undefined;
  let extracted = null;
  let trace_data = null;
  let result_data = null;
  try {
  if (action.type === 'recording') {
    const documents = await documentProvenance.snapshot();
    await finishRecording();
    return {session_id:process.env.TAP_BROWSER_SESSION, sequence:++sequence, url:page.url(), title:'', bounded_snapshot:'', fields:[], documents, screenshot_ref:null, video_data, error:null};
  }
  if (!['navigate', 'snapshot', 'click', 'fill', 'select', 'press', 'wait', 'screenshot', 'read', 'finish', 'scroll'].includes(action.type)) {
    throw new Error('unsupported action');
  }
  if (finished) throw new Error('browser session already finished');
  if (action.type === 'read') {
    if (typeof action.name !== 'string' || !/^[A-Za-z0-9_\-]{1,80}$/.test(action.name) || reservedOutputNames.has(action.name)) throw new Error('invalid output name');
    // A failed reread must remove its old evidence even if the target guard fails.
    delete resultValues[action.name];
    delete resultProvenance[action.name];
  }
  if (!actuate && !controlled && ['click','fill','select','press'].includes(action.type)) {
    // Parse and resolve the locator read-only. A syntax/strict-target error here
    // cannot have applied a browser write; do not turn it into unknown outcome.
    const count = await page.locator(action.selector).count();
    if (count === 0) throw new Error('no target matched before write; take a fresh snapshot and use its aria-ref, or wait explicitly for an observed stable selector');
    if (count > 1) throw new Error('strict mode violation: target is ambiguous before write');
  }
  if (['click','fill','select','press','read','wait'].includes(action.type)) await guardTarget(page, action);
  if (!actuate && action.type === 'navigate') {
    write_started = true;
    await page.goto(action.url, { timeout: 45_000, waitUntil: 'domcontentloaded' });
  }
  if(controlled && actuate) {
    // Never trace raw controlled values, including failed evaluation arguments.
    // Only a later safe agent segment is retained as evidence.
    if(traceActive) { await context.tracing.stop(); traceActive=false; }
    await actuate();
    if(action.actor_kind==='agent') { await context.tracing.start({screenshots:true,snapshots:true}); traceActive=true; }
  }
  if (!actuate && !controlled && ['click','fill','select','press'].includes(action.type)) {
    if (action.type === 'fill') {
      // Inspect the exact target only. A combobox/container is not an input;
      // never guess a child, follow a label, or rewrite the model's operation.
      const target = await page.locator(action.selector).evaluate(element => ({
        tag: element.tagName.toLowerCase(), type: String(element.type || '').toLowerCase(),
        readonly: element.readOnly === true || element.getAttribute('aria-readonly') === 'true',
        contenteditable: element.isContentEditable === true,
      }));
      if (target.readonly) throw new Error('target is readonly before write; inspect current controls instead of fill. For a readonly date widget, open the observed widget and choose current year, month and day controls; use select only on an observed supported choice control');
      // Match the pinned Playwright fill API's native input types, including
      // date/color/range controls. Visibility and action-time failures remain
      // Playwright's responsibility once dispatch starts.
      const fillTypes = ['email','number','password','search','tel','text','url','color','date','time','datetime-local','month','range','week'];
      if (target.tag === 'input' ? !fillTypes.includes(target.type) : target.tag !== 'textarea' && !target.contenteditable) {
        throw new Error('target is not a fill-editable control before write; take a fresh snapshot to choose the actual input or an observed select action, not a non-editable container; do not repeat fill on this target');
      }
    }
  }
  if (write_started === false) write_started = true;
  if (!actuate && action.type === 'click') await page.locator(action.selector).click({ timeout: 30_000 });
  if (!actuate && action.type === 'fill') await page.locator(action.selector).fill(bounded(action.value, 4000), { timeout: 30_000 });
  if (!actuate && action.type === 'scroll') await page.mouse.wheel(action.delta_x, action.delta_y);
  if (!actuate && action.type === 'select') await selectValue(page, action);
  if (!actuate && action.type === 'press') await page.locator(action.selector).press(bounded(action.key, 100), { timeout: 30_000 });
  if (action.type === 'wait') await page.locator(action.selector).waitFor({ timeout: 30_000 });
  if (action.type === 'read') {
    const {value, ...source} = await documentProvenance.read(action, readValue);
    const provenance = {...source, observation_sequence:sequence + 1, observed_at:new Date().toISOString()};
    extracted = {name:action.name, value, ...provenance};
    resultValues[action.name] = value;
    resultProvenance[action.name] = provenance;
  }
  if (action.type === 'finish') {
    if(controlled&&!traceActive) throw new Error('safe trace unavailable after failed controlled write');
    if (!Object.keys(resultValues).length) throw new Error('no observed business results');
    // Provenance records historical reads; final_url only describes the final page.
    result_data = {outputs:[{...resultValues, final_url:page.url(), provenance:{...resultProvenance}}]};
    if (Buffer.byteLength(JSON.stringify(result_data)) > 32768) throw new Error('business output too large');
    screenshot_data = (await page.screenshot({fullPage:false,timeout:30000})).toString('base64');
    await context.tracing.stop({path:tracePath});
    const trace = fs.readFileSync(tracePath);
    if (trace.length > 5000000) throw new Error('session trace exceeds evidence limit');
    trace_data = trace.toString('base64');
    const documents = await documentProvenance.snapshot();
    await finishRecording();
    finished = true;
    // Recording closes the context; the final DOM-backed result and evidence are
    // already captured. Do not query a closed page and misclassify completion.
    return { session_id: process.env.TAP_BROWSER_SESSION, sequence: ++sequence,
      url: bounded(page.url(), 2048), title: '', bounded_snapshot: '', fields: [],
      screenshot_ref, screenshot_data, trace_data, result_data, extracted, video_data, documents, error: null };
  }
  if (action.type === 'screenshot') {
    const image = await page.screenshot({ fullPage: false, timeout: 30_000 });
    screenshot_data = image.toString('base64');
    screenshot_ref = null;
  }
  } catch (error) {
    if(controlled && actuate) {
      return {session_id:process.env.TAP_BROWSER_SESSION,sequence:++sequence,url:bounded(page.url(),2048),title:'',bounded_snapshot:'',fields:[],documents:await documentProvenance.snapshot(),screenshot_ref:null,error:controlledFailure(error)};
    }
    action_error = bounded(error.message, 1000);
  }
  sequence++;
  const fields = await observeFields(page, action);
  // Official Playwright CLI uses this same AI snapshot API and aria-ref engine.
  // Re-observe after every action so references describe the current page.
  const scoped = action.type === 'snapshot' && action.selector;
  const snapshotRoot = scoped ? page.locator(action.selector) : page;
  let scopeFailed = false;
  let snapshot = await snapshotRoot.ariaSnapshot({ mode: 'ai', timeout: 30_000 }).catch(error => {
    scopeFailed = true;
    action_error ||= bounded(error.message, 1000);
    return '';
  });
  if (scoped) {
    // Playwright replaces this frame's ref map with each AI snapshot. Restore
    // the full-page maps so observing a scope cannot invalidate other DOM refs.
    const fullSnapshot = await page.ariaSnapshot({ mode: 'ai', timeout: 30_000 }).catch(error => {
      action_error ||= bounded(error.message, 1000);
      return '';
    });
    // Keep the scoped error, but return usable context for the next decision.
    if (scopeFailed) snapshot = fullSnapshot;
  }
  return { session_id: process.env.TAP_BROWSER_SESSION, sequence, url: bounded(page.url(), 2048),
    documents: await documentProvenance.snapshot(),
    title: bounded(await page.title().catch(() => ''), 256), bounded_snapshot: bounded(snapshot), fields,
    screenshot_ref, screenshot_data, trace_data, result_data, extracted, video_data, ...(write_started !== undefined ? {write_started} : {}), error: pageCrashed ? 'browser page crashed' + (action_error ? '\n' + action_error : '') : action_error };
}

const liveControl = controlled ? createLiveControl({page,observe,onControl:async kind=>{
  // Stop without a path discards the old segment. Never record human inputs.
  if(traceActive) { await context.tracing.stop(); traceActive=false; }
  if(kind==='agent') { await context.tracing.start({screenshots:true,snapshots:true}); traceActive=true; }
}}) : null;
let socketQueue=Promise.resolve();
function dispatch(action) {
  if(liveControl) return liveControl.dispatch(action);
  const pending=socketQueue.then(()=>observe(action));
  socketQueue=pending.catch(()=>{});
  return pending;
}

// The private local root is newly created; never unlink a different owner's socket.
try { fs.unlinkSync(socketPath); } catch {}
net.createServer(client => {
  let data = '';
  let received = false;
  client.on('data', async chunk => {
    if(received) return;
    data += chunk;
    if (data.length > 16_384) { client.destroy(); return; }
    if (!data.endsWith('\n')) return;
    received = true;
    try { client.end(JSON.stringify(await dispatch(JSON.parse(data)))); }
    catch (error) {
      const prewrite=controlled?preActuationFailure(error):null;
      client.end(JSON.stringify({ session_id: process.env.TAP_BROWSER_SESSION,
        sequence: ++sequence, url: bounded(page.url(), 2048), title: '', bounded_snapshot: '',
        fields: [], documents: [], screenshot_ref: null,
        ...(prewrite?{write_started:false,extracted:{pre_actuation_failure:prewrite}}:{}),
        error: prewrite ? (prewrite.category==='target_missing'?'controlled target missing before actuation':'controlled target ambiguous before actuation')
          : controlled ? controlledRequestFailure(error) : bounded(error.message, 1000) }));
    }
  });
}).listen(socketPath, () => fs.chmodSync(socketPath, 0o600));
if (localRoot) process.once('SIGTERM', async () => {
  await context.close().catch(() => {});
  process.exit(0);
});

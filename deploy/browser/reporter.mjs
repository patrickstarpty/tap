import fs from 'node:fs';

const MAX_BUSINESS_BYTES = 32768;
const MAX_FAILURE_BYTES = 2000;

export function summarize({ passed, failed, skipped }) {
  return { status: passed > 0 && failed === 0 && skipped === 0 ? 'passed' : 'failed', passed, failed, skipped };
}

export function boundedFailure(error) {
  const raw = typeof error?.message === 'string' ? error.message : '';
  const withoutPaths = raw.replace(/\/(?:private|Users|home|opt|tmp|var)\/[^\s:]+/g, '[path]');
  return Buffer.from(withoutPaths).subarray(0, MAX_FAILURE_BYTES).toString('utf8').replace(/\uFFFD$/u, '');
}

export function collectBusinessOutputs(attachments, previous = []) {
  const outputs = [];
  for (const attachment of attachments) {
    if (attachment.name !== 'tapper-result') continue;
    if (attachment.contentType !== 'application/json' || !Buffer.isBuffer(attachment.body)) {
      throw new Error('tapper-result requires an in-memory application/json body');
    }
    let value;
    try {
      value = JSON.parse(attachment.body.toString('utf8'));
    } catch {
      throw new Error('tapper-result is invalid JSON');
    }
    if (value === null || typeof value !== 'object') {
      throw new Error('tapper-result must contain structured JSON');
    }
    outputs.push(value);
    if (Buffer.byteLength(JSON.stringify([...previous, ...outputs])) > MAX_BUSINESS_BYTES) {
      throw new Error('tapper-result outputs exceed 32768 bytes');
    }
  }
  return outputs;
}

export default class TrustedReporter {
  constructor() {
    this.nonce = process.env.TAP_BROWSER_REPORT_NONCE;
    delete process.env.TAP_BROWSER_REPORT_NONCE;
    this.tests = [];
    this.errors = [];
  }
  onError(error) {
    if (this.errors.length < 4) this.errors.push(boundedFailure(error) || 'Playwright collection failed');
  }
  onTestEnd(test, result) {
    const walk = (steps) => steps.flatMap(step => [{ title: step.title, status: step.error ? 'failed' : 'passed', duration: step.duration,
      ...(step.error ? { error: boundedFailure(step.error) } : {}) }, ...walk(step.steps || [])]);
    let outputs = [];
    let outputError;
    try {
      outputs = collectBusinessOutputs(result.attachments, this.tests.flatMap(item => item.outputs));
    } catch (error) {
      outputError = boundedFailure(error);
    }
    this.tests.push({ title: test.title, status: outputError ? 'failed' : result.status, steps: walk(result.steps), outputs,
      ...(outputError ? { output_error: outputError } : {}),
      ...(result.error ? { error: boundedFailure(result.error) } : {}),
      attachments: result.attachments.filter(a => a.path && a.name !== 'tapper-result').map(a => ({ name: a.name, path: a.path })) });
  }
  buildReceipt(evidence) {
    const passed = this.tests.filter(t => t.status === 'passed').length;
    const failed = this.tests.filter(t => ['failed', 'timedOut', 'interrupted'].includes(t.status)).length;
    const skipped = this.tests.filter(t => t.status === 'skipped').length;
    const summary = summarize({ passed, failed, skipped });
    return { ...summary, status: this.errors.length ? 'failed' : summary.status,
      tests: this.tests.map(({ attachments, ...rest }) => rest),
      steps: this.tests.flatMap(t => t.steps), outputs: this.tests.flatMap(t => t.outputs),
      errors: this.errors, evidence, nonce: this.nonce };
  }
  async onEnd() {
    const evidence = [];
    for (const test of this.tests) {
      for (const attachment of test.attachments) {
        const kind = attachment.name.includes('trace') ? 'trace' :
          attachment.name.includes('screenshot') ? 'screenshot' : null;
        if (kind && fs.existsSync(attachment.path)) {
          const name = `${kind}-${evidence.length}${kind === 'trace' ? '.zip' : '.png'}`;
          fs.copyFileSync(attachment.path, `/opt/browser/output/${name}`);
          evidence.push({ kind, name });
        }
      }
    }
    const receipt = this.buildReceipt(evidence);
    fs.writeFileSync('/opt/browser/output/result.json.tmp', JSON.stringify(receipt), { mode: 0o600 });
    fs.renameSync('/opt/browser/output/result.json.tmp', '/opt/browser/output/result.json');
  }
}

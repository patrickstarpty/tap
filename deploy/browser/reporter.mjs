import fs from 'node:fs';

export function summarize({ passed, failed, skipped }) {
  return { status: passed > 0 && failed === 0 && skipped === 0 ? 'passed' : 'failed', passed, failed, skipped };
}

export default class TrustedReporter {
  constructor() {
    this.nonce = process.env.TAP_BROWSER_REPORT_NONCE;
    delete process.env.TAP_BROWSER_REPORT_NONCE;
    this.tests = [];
  }
  onTestEnd(test, result) {
    const walk = (steps) => steps.flatMap(step => [{ title: step.title, status: step.error ? 'failed' : 'passed', duration: step.duration }, ...walk(step.steps || [])]);
    this.tests.push({ title: test.title, status: result.status, steps: walk(result.steps),
      attachments: result.attachments.filter(a => a.path).map(a => ({ name: a.name, path: a.path })) });
  }
  async onEnd() {
    const passed = this.tests.filter(t => t.status === 'passed').length;
    const failed = this.tests.filter(t => ['failed', 'timedOut', 'interrupted'].includes(t.status)).length;
    const skipped = this.tests.filter(t => t.status === 'skipped').length;
    const summary = summarize({ passed, failed, skipped });
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
    const receipt = { ...summary, tests: this.tests.map(({ attachments, ...rest }) => rest),
      steps: this.tests.flatMap(t => t.steps), evidence, nonce: this.nonce };
    fs.writeFileSync('/opt/browser/output/result.json.tmp', JSON.stringify(receipt), { mode: 0o600 });
    fs.renameSync('/opt/browser/output/result.json.tmp', '/opt/browser/output/result.json');
  }
}

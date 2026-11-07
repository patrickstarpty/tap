import crypto from 'node:crypto';
import { spawn } from 'node:child_process';
import fs from 'node:fs';

let input = '';
for await (const chunk of process.stdin) input += chunk;
const config = JSON.parse(input);
const deadline = Math.min(600, Math.max(1, Number(config.deadline_seconds)));
const nonce = crypto.randomBytes(32).toString('hex');
const child = spawn(process.execPath, ['/opt/browser/node_modules/@playwright/test/cli.js', 'test',
  '/opt/browser/frozen/flow.spec.ts', '--config=/opt/browser/playwright.config.mjs', '--workers=1'], {
  cwd: '/opt/browser', env: { ...process.env, TAP_BROWSER_REPORT_NONCE: nonce,
    TAP_BROWSER_TEST_TIMEOUT: String(deadline * 1000) }, stdio: ['ignore', 'pipe', 'pipe'],
});
let stderr = '';
child.stderr.on('data', chunk => { stderr += chunk; if (stderr.length > 16_000) stderr = stderr.slice(-16_000); });
child.stdout.resume();
const timer = setTimeout(() => child.kill('SIGKILL'), deadline * 1000);
const code = await new Promise(resolve => child.on('exit', resolve));
clearTimeout(timer);
const path = '/opt/browser/output/result.json';
if (!fs.existsSync(path)) {
  process.stderr.write(stderr.slice(-4000));
  process.exit(1);
}
const result = JSON.parse(fs.readFileSync(path, 'utf8'));
if (result.nonce !== nonce) process.exit(1);
process.stdout.write(JSON.stringify({ nonce, status: result.status }));
if (code !== 0 || result.status !== 'passed') process.exitCode = 1;

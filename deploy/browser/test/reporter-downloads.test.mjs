import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import TrustedReporter from '../reporter.mjs';

test('download attachment becomes bounded binary evidence with sanitized metadata', async () => {
  const output = fs.mkdtempSync(path.join(os.tmpdir(), 'reporter-download-'));
  const prior = process.env.TAP_BROWSER_OUTPUT_DIR;
  process.env.TAP_BROWSER_OUTPUT_DIR = output;
  try {
    const reporter = new TrustedReporter();
    reporter.onTestEnd({title:'files'}, {status:'passed', steps:[], attachments:[
      {name:'lca-download:save:../../report\r\n.txt',contentType:'application/octet-stream',body:Buffer.from('download bytes')},
    ]});
    await reporter.onEnd();
    const receipt = JSON.parse(fs.readFileSync(path.join(output,'result.json')));
    assert.equal(receipt.status, 'passed');
    assert.deepEqual(receipt.evidence.map(({kind,filename,step_id})=>({kind,filename,step_id})), [{kind:'download',filename:'report.txt',step_id:'save'}]);
    assert.equal(fs.readFileSync(path.join(output,receipt.evidence[0].name),'utf8'), 'download bytes');
    assert.match(receipt.evidence[0].sha256,/^[a-f0-9]{64}$/);
  } finally { if(prior===undefined) delete process.env.TAP_BROWSER_OUTPUT_DIR; else process.env.TAP_BROWSER_OUTPUT_DIR=prior; fs.rmSync(output,{recursive:true,force:true}); }
});

test('oversized or path-backed downloads fail closed without evidence bytes', () => {
  for (const attachment of [
    {name:'lca-download:save:x',contentType:'application/octet-stream',body:Buffer.alloc(5000001)},
    {name:'lca-download:save:x',contentType:'application/octet-stream',path:'/etc/passwd'},
  ]) {
    const reporter = new TrustedReporter();
    reporter.onTestEnd({title:'files'}, {status:'passed',steps:[],attachments:[attachment]});
    assert.equal(reporter.buildReceipt([]).status,'failed');
    assert.match(reporter.buildReceipt([]).tests[0].output_error,/download/i);
  }
});

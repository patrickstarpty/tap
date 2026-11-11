import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { exportFiles } from '../export.mjs';

test('export includes bounded regular files and rejects symlinks', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'browser-export-'));
  try {
    fs.writeFileSync(path.join(root, 'result.json'), '{}');
    assert.equal(exportFiles(root)[0].name, 'result.json');
    fs.symlinkSync('/etc/passwd', path.join(root, 'link'));
    assert.throws(() => exportFiles(root));
  } finally { fs.rmSync(root, { recursive: true, force: true }); }
});


test('export preserves captured download binary bytes without treating filename as a path', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'browser-download-export-'));
  try {
    fs.writeFileSync(path.join(root, 'download-0.bin'), 'selected result');
    assert.deepEqual(exportFiles(root), [{name:'download-0.bin',data:Buffer.from('selected result').toString('base64')}]);
    fs.writeFileSync(path.join(root, 'arbitrary.bin'), 'bad');
    assert.throws(() => exportFiles(root), /unknown output/);
  } finally { fs.rmSync(root, {recursive:true,force:true}); }
});

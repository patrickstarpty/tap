import fs from 'node:fs';

export function exportFiles(root) {
  const files = [];
  let total = 0;
  for (const entry of fs.readdirSync(root, { withFileTypes: true })) {
    if (entry.isSymbolicLink()) throw new Error('symlink output denied');
    if (entry.isDirectory()) {
      if (entry.name !== 'test-results') throw new Error('unknown output directory');
      continue;
    }
    if (!entry.isFile() || !/^(result\.json|(?:screenshot|trace)-\d+\.(?:png|zip))$/.test(entry.name)) {
      throw new Error('unknown output file');
    }
    if (files.length >= 32) throw new Error('too many output files');
    const path = `${root}/${entry.name}`;
    const fd = fs.openSync(path, fs.constants.O_RDONLY | fs.constants.O_NOFOLLOW);
    try {
      const stat = fs.fstatSync(fd);
      if (!stat.isFile()) throw new Error('nonregular output denied');
      total += stat.size;
      if (total > 16_000_000) throw new Error('output too large');
      files.push({ name: entry.name, data: fs.readFileSync(fd).toString('base64') });
    } finally {
      fs.closeSync(fd);
    }
  }
  return files;
}

if (process.argv[1] === '/opt/browser/export.mjs') {
  process.stdout.write(JSON.stringify(exportFiles('/opt/browser/output')));
}

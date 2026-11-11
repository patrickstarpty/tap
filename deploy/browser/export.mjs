import fs from 'node:fs';
import { fileURLToPath } from 'node:url';

export function exportFiles(root) {
  const files = [];
  let total = 0;
  for (const entry of fs.readdirSync(root, { withFileTypes: true })) {
    if (entry.isSymbolicLink()) throw new Error('symlink output denied');
    if (entry.isDirectory()) {
      if (entry.name !== 'test-results') throw new Error('unknown output directory');
      continue;
    }
    if (!entry.isFile() || !/^(result\.json|(?:screenshot|trace|video)-\d+\.(?:png|zip|webm)|download-\d+\.bin)$/.test(entry.name)) {
      throw new Error('unknown output file');
    }
    if (files.length >= 32) throw new Error('too many output files');
    const path = `${root}/${entry.name}`;
    const fd = fs.openSync(path, fs.constants.O_RDONLY | fs.constants.O_NOFOLLOW);
    try {
      const stat = fs.fstatSync(fd);
      if (!stat.isFile()) throw new Error('nonregular output denied');
      if (entry.name.startsWith('download-') && stat.size > 5000000) throw new Error('download output too large');
      total += stat.size;
      if (total > 80_000_000) throw new Error('output too large');
      files.push({ name: entry.name, data: fs.readFileSync(fd).toString('base64') });
    } finally {
      fs.closeSync(fd);
    }
  }
  return files;
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  process.stdout.write(JSON.stringify(exportFiles(process.env.TAP_BROWSER_OUTPUT_DIR || '/opt/browser/output')));
}

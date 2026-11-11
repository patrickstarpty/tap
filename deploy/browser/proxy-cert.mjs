import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import net from 'node:net';
import path from 'node:path';

const root = process.env.TAP_BROWSER_LOCAL_DIR ? path.join(process.env.TAP_BROWSER_LOCAL_DIR, 'ca') : '/tmp/tapper-task-ca';

function openssl(...args) {
  execFileSync('openssl', args, { stdio: 'ignore', timeout: 10_000 });
}

export function createTaskCertificates(origins, { publicWeb = false } = {}) {
  const names = [...new Set(origins.filter(x => x.startsWith('https://')).map(x => new URL(x).hostname))];
  if (!names.length && !publicWeb) return { ca: null, certificates: new Map(), getCertificate: () => undefined };
  if (names.length > 16 || names.some(x => x.length > 253 || !/^[a-z0-9.-]+$/.test(x))) {
    throw new Error('invalid certificate host');
  }
  fs.mkdirSync(root, { recursive: true, mode: 0o700 });
  const caKey = path.join(root, 'ca.key');
  const caCert = path.join(root, 'ca.crt');
  openssl('req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-keyout', caKey,
    '-out', caCert, '-days', '1', '-subj', '/CN=Tapper Task CA',
    '-addext', 'basicConstraints=critical,CA:TRUE',
    '-addext', 'keyUsage=critical,keyCertSign,cRLSign');
  fs.chmodSync(caKey, 0o600);
  const certificates = new Map();
  function getCertificate(name) {
    if (certificates.has(name)) return certificates.get(name);
    if (!publicWeb && !names.includes(name)) return undefined;
    if (certificates.size >= 128 || name.length > 253 || !/^[a-z0-9.-]+$/.test(name)) throw new Error('certificate host limit');
    const index = certificates.size;
    const key = path.join(root, `leaf-${index}.key`);
    const csr = path.join(root, `leaf-${index}.csr`);
    const cert = path.join(root, `leaf-${index}.crt`);
    const ext = path.join(root, `leaf-${index}.ext`);
    const san = net.isIP(name) === 4 ? `IP:${name}` : `DNS:${name}`;
    fs.writeFileSync(ext, `basicConstraints=critical,CA:FALSE\nkeyUsage=digitalSignature,keyEncipherment\nextendedKeyUsage=serverAuth\nsubjectAltName=${san}\n`, { mode: 0o600 });
    openssl('req', '-newkey', 'ec', '-pkeyopt', 'ec_paramgen_curve:P-256', '-nodes', '-keyout', key, '-out', csr,
      '-subj', `/CN=${name}`);
    openssl('x509', '-req', '-in', csr, '-CA', caCert, '-CAkey', caKey,
      '-set_serial', String(index + 100), '-out', cert, '-days', '1', '-extfile', ext);
    fs.chmodSync(key, 0o600);
    const identity = { key: fs.readFileSync(key), cert: fs.readFileSync(cert) };
    certificates.set(name, identity);
    return identity;
  }
  names.forEach(getCertificate);
  return { ca: fs.readFileSync(caCert, 'utf8'), certificates, getCertificate };
}

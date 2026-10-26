// Místní náhrada Funnelu. Jen testovací Docker; neposílá nic mimo stroj.
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const http = require('node:http');
const https = require('node:https');
const { execFileSync } = require('node:child_process');
const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'jrc-test-tls-'));
execFileSync('openssl', ['req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1',
  '-keyout', path.join(dir, 'key.pem'), '-out', path.join(dir, 'cert.pem'),
  '-subj', '/CN=127.0.0.1', '-addext', 'subjectAltName=IP:127.0.0.1'], { stdio: 'ignore' });
https.createServer({ key: fs.readFileSync(path.join(dir, 'key.pem')),
  cert: fs.readFileSync(path.join(dir, 'cert.pem')) }, (req, res) => {
  const headers = { ...req.headers, 'x-forwarded-proto': 'https',
    'x-forwarded-for': req.headers['x-test-client-ip'] || '203.0.113.9' };
  delete headers['x-test-client-ip'];
  delete headers['x-real-ip'];
  const upstream = http.request({ host: '127.0.0.1', port: 13443,
    path: req.url, method: req.method, headers }, response => {
    res.writeHead(response.statusCode, response.headers);
    response.pipe(res);
  });
  upstream.on('error', () => { res.writeHead(502); res.end(); });
  req.on('aborted', () => upstream.destroy());
  req.pipe(upstream);
}).listen(14163, '127.0.0.1', () => console.log('Místní HTTPS proxy připravena.'));

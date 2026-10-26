const fs = require('node:fs');
const crypto = require('node:crypto');
const values = {
  PGPASSWORD: crypto.randomBytes(32).toString('hex'),
  AWS_ACCESS_KEY_ID: 'GK' + crypto.randomBytes(12).toString('hex'),
  AWS_SECRET_ACCESS_KEY: crypto.randomBytes(32).toString('hex'),
  GARAGE_RPC_SECRET: crypto.randomBytes(32).toString('hex'),
  JRC_BB_SECRET_KEY: crypto.randomBytes(48).toString('hex'),
  JRC_BB_ENCRYPTION_KEY_SALT: crypto.randomBytes(32).toString('hex'),
  JRC_FF_JWT_SECRET: crypto.randomBytes(48).toString('hex'),
  JRC_FF_SYNC_PASSWORD: crypto.randomBytes(32).toString('hex'),
  JRC_FF_SSO_SECRET: crypto.randomBytes(48).toString('hex'),
  JRC_FF_S3_ACCESS_KEY: 'GK' + crypto.randomBytes(12).toString('hex'),
  JRC_FF_S3_SECRET_KEY: crypto.randomBytes(32).toString('hex'),
  JRC_BB_S3_ACCESS_KEY: 'GK' + crypto.randomBytes(12).toString('hex'),
  JRC_BB_S3_SECRET_KEY: crypto.randomBytes(32).toString('hex'),
  JRC_UID: process.getuid(),
  JRC_GID: process.getgid(),
  JRC_MODE: process.env.JRC_MODE || 'prod',
  JRC_PUBLIC_ORIGIN: process.env.JRC_PUBLIC_ORIGIN || `http://127.0.0.1:${process.env.JRC_APP_PORT || '13000'}`,
  JRC_BASE_PATH: process.env.JRC_BASE_PATH ?? '/jrc',
  JRC_SMTP_HOST: '',
  JRC_SMTP_PORT: '',
  JRC_SMTP_USER: '',
  JRC_SMTP_PASSWORD: '',
  JRC_SMTP_FROM: '',
  JRC_APP_PORT: process.env.JRC_APP_PORT || '13000',
  JRC_POSTGRES_PORT: process.env.JRC_POSTGRES_PORT || '5432',
  JRC_S3_PORT: process.env.JRC_S3_PORT || '13900',
  JRC_BB_PORT: process.env.JRC_BB_PORT || '18011',
};
const template = fs.readFileSync('.env.example', 'utf8');
const exists = fs.existsSync('.env');
const original = exists ? fs.readFileSync('.env', 'utf8') : '';
let content = original;
if (exists) {
  const port = /^JRC_APP_PORT=(.*)$/m.exec(original)?.[1] || '13000';
  values.JRC_PUBLIC_ORIGIN = `http://127.0.0.1:${port}`;
}
for (const line of template.split('\n')) {
  const match = /^(\w+)=(.*)$/.exec(line);
  if (!match) continue;
  const [, key, fallback] = match;
  // Tolerantní hledání: `KEY=…`, `export KEY=…`, mezery kolem `=` (review F2-B01: nepřepsat existující heslo).
  const tolerant = new RegExp(`^[ \\t]*(?:export[ \\t]+)?${key}[ \\t]*=[ \\t]*(.*)$`, 'm');
  const anyFound = tolerant.exec(content);
  if (anyFound && (anyFound[1].trim() !== '' || key === 'JRC_BASE_PATH')) continue;
  const existing = new RegExp(`^[ \\t]*(?:export[ \\t]+)?${key}[ \\t]*=(.*)$`, 'm');
  const found = existing.exec(content);
  // Pro nový worktree respektuj předané porty. Při doplnění existujícího
  // souboru ponech výchozí nastavení; nikdy nepřepisuj jeho vlastní hodnoty.
  const value = (key === 'JRC_PUBLIC_ORIGIN' || !exists) && values[key] !== undefined ? values[key] : (fallback || values[key]);
  if (value === undefined) throw new Error(`Chybí generátor hodnoty ${key}`);
  if (found) content = content.replace(existing, `${key}=${value}`);
  else content += `${content && !content.endsWith('\n') ? '\n' : ''}${key}=${value}\n`;
}
if (content !== original) {
  const temporary = '.jrc-env.init.env.local';
  try {
    fs.writeFileSync(temporary, content, { mode: 0o600, flag: 'wx' });
    fs.renameSync(temporary, '.env');
  } finally {
    if (fs.existsSync(temporary)) fs.unlinkSync(temporary);
  }
}
fs.chmodSync('.env', 0o600);
console.log(exists ? '.env doplněn; existující hodnoty zachovány (600).' : 'Vytvořen .env s náhodnými lokálními hesly (600).');

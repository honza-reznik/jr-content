const fs = require('node:fs');
const crypto = require('node:crypto');
if (fs.existsSync('.env')) {
  console.log('.env již existuje; zůstává beze změny.');
  process.exit(0);
}
const values = {
  PGPASSWORD: crypto.randomBytes(32).toString('hex'),
  AWS_ACCESS_KEY_ID: 'GK' + crypto.randomBytes(12).toString('hex'),
  AWS_SECRET_ACCESS_KEY: crypto.randomBytes(32).toString('hex'),
  GARAGE_RPC_SECRET: crypto.randomBytes(32).toString('hex'),
  JRC_UID: process.getuid(),
  JRC_GID: process.getgid(),
};
const template = fs.readFileSync('.env.example', 'utf8');
const content = template.replace(/^(\w+)=$/gm, (line, key) => {
  if (!(key in values)) throw new Error(`Chybí generátor hodnoty ${key}`);
  return `${key}=${values[key]}`;
});
fs.writeFileSync('.env', content, { mode: 0o600, flag: 'wx' });
console.log('Vytvořen .env s náhodnými lokálními hesly (oprávnění 600).');

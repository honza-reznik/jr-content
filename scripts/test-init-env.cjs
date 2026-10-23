// Test generátoru běží jen v Dockeru; testovací tajemství neopustí /tmp.
const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { execFileSync } = require('node:child_process');
const temporary = fs.mkdtempSync(path.join(os.tmpdir(), 'jrc-env-test-'));
const generator = path.resolve('scripts/init-env.cjs');
try {
  fs.copyFileSync('.env.example', path.join(temporary, '.env.example'));
  const run = () => execFileSync(process.execPath, [generator], { cwd: temporary });
  const read = () => fs.readFileSync(path.join(temporary, '.env'), 'utf8');
  run();
  const fresh = read();
  assert.match(fresh, /^JRC_BB_SECRET_KEY=[a-f0-9]{96}$/m);
  assert.match(fresh, /^JRC_BB_ENCRYPTION_KEY_SALT=[a-f0-9]{64}$/m);
  assert.match(fresh, /^JRC_FF_JWT_SECRET=[a-f0-9]{96}$/m);
  assert.match(fresh, /^JRC_FF_SYNC_PASSWORD=[a-f0-9]{64}$/m);
  assert.match(fresh, /^JRC_FF_S3_ACCESS_KEY=GK[a-f0-9]{24}$/m);
  assert.match(fresh, /^JRC_FF_S3_SECRET_KEY=[a-f0-9]{64}$/m);
  assert.match(fresh, /^JRC_BB_S3_ACCESS_KEY=GK[a-f0-9]{24}$/m);
  assert.match(fresh, /^JRC_BB_S3_SECRET_KEY=[a-f0-9]{64}$/m);
  assert.match(fresh, /^JRC_S3_APP_QUOTA=20GiB$/m);
  // Tři samostatné klíče Garage: jrc-local, jrc-video a jrc-app.
  const keys = ['AWS_ACCESS_KEY_ID', 'JRC_FF_S3_ACCESS_KEY', 'JRC_BB_S3_ACCESS_KEY']
    .map(name => new RegExp(`^${name}=(.*)$`, 'm').exec(fresh)[1]);
  assert.equal(new Set(keys).size, 3);
  assert.equal(fs.statSync(path.join(temporary, '.env')).mode & 0o777, 0o600);
  run();
  assert.equal(read(), fresh);
  const existingPassword = crypto.randomBytes(32).toString('hex');
  fs.writeFileSync(path.join(temporary, '.env'), `# starší F0\nPGPASSWORD=${existingPassword}\nJRC_BB_SECRET_KEY=\n`);
  run();
  const upgraded = read();
  assert(upgraded.startsWith(`# starší F0\nPGPASSWORD=${existingPassword}\n`));
  assert.match(upgraded, /^JRC_BB_SECRET_KEY=[a-f0-9]{96}$/m);
  assert.match(upgraded, /^JRC_BB_ENCRYPTION_KEY_SALT=[a-f0-9]{64}$/m);
  assert.match(upgraded, /^JRC_BB_S3_ACCESS_KEY=GK[a-f0-9]{24}$/m);
  run();
  assert.equal(read(), upgraded);
  console.log('OK .env: nové hodnoty, doplnění starého souboru, zachování hesel, opakování a práva 600');
} finally {
  fs.rmSync(temporary, { recursive: true, force: true });
}

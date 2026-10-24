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
  const run = (env = process.env) => execFileSync(process.execPath, [generator], { cwd: temporary, env });
  const read = () => fs.readFileSync(path.join(temporary, '.env'), 'utf8');
  run({ ...process.env, JRC_APP_PORT: '13223', JRC_POSTGRES_PORT: '15473', JRC_S3_PORT: '13943', JRC_BB_PORT: '18043' });
  const fresh = read();
  assert.match(fresh, /^JRC_APP_PORT=13223$/m);
  assert.match(fresh, /^JRC_POSTGRES_PORT=15473$/m);
  assert.match(fresh, /^JRC_S3_PORT=13943$/m);
  assert.match(fresh, /^JRC_BB_PORT=18043$/m);
  assert.match(fresh, /^JRC_BB_SECRET_KEY=[a-f0-9]{96}$/m);
  assert.match(fresh, /^JRC_BB_ENCRYPTION_KEY_SALT=[a-f0-9]{64}$/m);
  assert.match(fresh, /^JRC_FF_JWT_SECRET=[a-f0-9]{96}$/m);
  assert.match(fresh, /^JRC_FF_SYNC_PASSWORD=[a-f0-9]{64}$/m);
  assert.match(fresh, /^JRC_FF_SSO_SECRET=[a-f0-9]{96}$/m);
  assert.notEqual(/^JRC_FF_SSO_SECRET=(.*)$/m.exec(fresh)[1], /^JRC_FF_JWT_SECRET=(.*)$/m.exec(fresh)[1]);
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
  run({ ...process.env, JRC_APP_PORT: '19999', JRC_S3_PORT: '19998' });
  assert.equal(read(), fresh);
  const existingPassword = crypto.randomBytes(32).toString('hex');
  fs.writeFileSync(path.join(temporary, '.env'), `# starší F0\nPGPASSWORD=${existingPassword}\nJRC_APP_PORT=13000\nJRC_S3_PORT=13900\nJRC_BB_SECRET_KEY=\n`);
  run({ ...process.env, JRC_APP_PORT: '19999', JRC_S3_PORT: '19998' });
  const upgraded = read();
  assert(upgraded.startsWith(`# starší F0\nPGPASSWORD=${existingPassword}\n`));
  assert.match(upgraded, /^JRC_APP_PORT=13000$/m);
  assert.match(upgraded, /^JRC_S3_PORT=13900$/m);
  assert.match(upgraded, /^JRC_BB_SECRET_KEY=[a-f0-9]{96}$/m);
  assert.match(upgraded, /^JRC_BB_ENCRYPTION_KEY_SALT=[a-f0-9]{64}$/m);
  assert.match(upgraded, /^JRC_FF_SSO_SECRET=[a-f0-9]{96}$/m);
  assert.match(upgraded, /^JRC_BB_S3_ACCESS_KEY=GK[a-f0-9]{24}$/m);
  run();
  assert.equal(read(), upgraded);
  console.log('OK .env: nové hodnoty, doplnění starého souboru, zachování hesel, opakování a práva 600');
} finally {
  fs.rmSync(temporary, { recursive: true, force: true });
}

const net = require('node:net');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const { Resolver } = require('node:dns').promises;
function connects(host, port) {
  return new Promise(resolve => {
    const socket = net.connect({ host, port });
    const finish = connected => { socket.destroy(); resolve(connected); };
    socket.setTimeout(2000, () => finish(false));
    socket.once('error', () => finish(false));
    socket.once('connect', () => finish(true));
  });
}
(async () => {
  const routes = fs.readFileSync('/proc/net/route', 'utf8').trim().split('\n').slice(1);
  assert(!routes.some(row => row.trim().split(/\s+/)[1] === '00000000'),
    'Služba má výchozí IPv4 trasu');
  const routes6 = fs.readFileSync('/proc/net/ipv6_route', 'utf8').trim().split('\n');
  assert(!routes6.some(row => {
    const fields = row.trim().split(/\s+/);
    // Kernel uvádí i nedosažitelnou výchozí trasu lo (RTF_REJECT), ta není cestou ven.
    return fields[0] === '0'.repeat(32) && fields[1] === '00'
      && !(parseInt(fields[8], 16) & 0x200);
  }), 'Služba má výchozí IPv6 trasu');
  assert(await connects('jrc-postgres', 5432), 'Nefunguje ani vnitřní spojení');
  for (const host of ['1.1.1.1', '2606:4700:4700::1111']) {
    assert.equal(await connects(host, 443), false, `Služba má internet přes ${host}`);
  }
  const resolver = new Resolver({ timeout: 2000, tries: 1 });
  await assert.rejects(resolver.resolve4('example.com'), 'Služba překládá veřejné DNS');
})().catch(error => { console.error(error); process.exitCode = 1; });

const net = require('node:net');
const assert = require('node:assert/strict');
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
  assert(await connects('jrc-postgres', 5432), 'Nefunguje ani vnitřní spojení');
  for (const host of ['1.1.1.1', '2606:4700:4700::1111']) {
    assert.equal(await connects(host, 443), false, `Služba má internet přes ${host}`);
  }
})().catch(error => { console.error(error); process.exitCode = 1; });

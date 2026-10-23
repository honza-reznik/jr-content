// Dočasný vstup do F2-B03. Cíle jsou pevné, provoz včetně WebSocketů a S3 podpisů
// se přenáší beze změny; klient nemůže zadat jiný cíl ani použít HTTP CONNECT.
const net = require('node:net');

for (const [port, host, targetPort] of [
  [18022, 'jrc-ff-api', 8000],
  [13022, 'jrc-ff-web', 3000],
  [18011, 'jrc-bb-web', 8000],
  [15432, 'jrc-postgres', 5432],
  [13900, 'jrc-s3', 3900],
]) {
  net.createServer(client => {
    const upstream = net.connect({ host, port: targetPort });
    client.on('error', () => upstream.destroy());
    upstream.on('error', () => client.destroy());
    client.on('close', () => upstream.destroy());
    upstream.on('close', () => client.destroy());
    client.pipe(upstream).pipe(client);
  }).listen(port, '0.0.0.0');
}

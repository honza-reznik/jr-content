// Pouze port úložiště S3 pro podepsané adresy z prohlížeče; FreeFrame i Brightbean
// používají HTTP proxy, PostgreSQL zůstává jen ve vnitřní síti. Cíl je pevný, provoz včetně WebSocketů a S3 podpisů
// se přenáší beze změny; klient nemůže zadat jiný cíl ani použít HTTP CONNECT.
const net = require('node:net');

for (const [port, host, targetPort] of [
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

// Jen pro test: NODE_OPTIONS předá sledování i dětem Next.js při studeném startu.
// Zaznamenáme i neúspěšný pokus, aby blokovaná síť neskryla regresi.
const fs = require('node:fs');
const net = require('node:net');
const dns = require('node:dns');
const record = (kind, host) => fs.appendFileSync(process.env.JRC_NETWORK_AUDIT,
  JSON.stringify({ kind, host, pid: process.pid }) + '\n');

const connect = net.Socket.prototype.connect;
net.Socket.prototype.connect = function (...args) {
  const first = Array.isArray(args[0]) ? args[0][0] : args[0];
  const options = typeof first === 'object' ? first : { host: args[1] };
  if (!options?.path) record('connect', options?.host || 'localhost');
  return connect.apply(this, args);
};
const lookup = dns.lookup;
dns.lookup = function (host, ...args) {
  record('dns', host);
  return lookup.call(this, host, ...args);
};

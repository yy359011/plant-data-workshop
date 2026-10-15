import assert from 'node:assert/strict';
import test from 'node:test';

process.env.PLANT_API_PORT = '8776';
process.env.PLANT_DEV_PORT = '5183';
process.env.PLANT_DEV_HOST = '0.0.0.0';
const {default: config} = await import('../vite.config.mjs');

test('source server binds configured LAN port and loopback API without silent port fallback', () => {
  assert.equal(config.server.host, '0.0.0.0');
  assert.equal(config.server.port, 5183);
  assert.equal(config.server.strictPort, true);
  assert.equal(config.server.proxy['/api'].target, 'http://127.0.0.1:8776');
  assert.equal(config.server.proxy['/api'].changeOrigin, true);
  assert.equal(config.server.proxy['/api'].xfwd, true);
  assert.notEqual(config.server.allowedHosts, true);
});

test('proxy overwrites forged forwarded headers with actual remote client and host', () => {
  let handler;
  config.server.proxy['/api'].configure({on(event, callback) {assert.equal(event, 'proxyReq'); handler=callback;}});
  const headers = new Map([['X-Forwarded-For', '127.0.0.1'], ['X-Forwarded-Host', 'localhost:5183'], ['X-Forwarded-Proto', 'https']]);
  handler({setHeader: (key, value) => headers.set(key, value)}, {socket: {remoteAddress:'192.168.2.21'}, headers: {host:'192.168.2.12:5183', origin:'http://192.168.2.12:5183'}});
  assert.equal(headers.get('X-Forwarded-For'), '192.168.2.21');
  assert.equal(headers.get('X-Forwarded-Host'), '192.168.2.12:5183');
  assert.equal(headers.get('X-Forwarded-Proto'), 'http');
  assert.equal(headers.has('Origin'), false, 'original Origin must stay intact');
});

test('local frontend request preserves an actual loopback caller', () => {
  let handler;
  config.server.proxy['/api'].configure({on(_event, callback) {handler=callback;}});
  const headers = {};
  handler({setHeader: (key, value) => {headers[key]=value;}}, {socket:{remoteAddress:'127.0.0.1'}, headers:{host:'127.0.0.1:5183'}});
  assert.equal(headers['X-Forwarded-For'], '127.0.0.1');
  assert.equal(headers['X-Forwarded-Host'], '127.0.0.1:5183');
});

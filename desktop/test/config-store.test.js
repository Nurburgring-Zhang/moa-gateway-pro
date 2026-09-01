'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const { ConfigStore, validateConfig, deepMerge, DEFAULT_CONFIG } = require('../src/main/config-store');

function tmpFile(name) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'moa-cfg-test-'));
  return path.join(dir, name);
}

test('config-store: missing file yields documented defaults (port 8910)', () => {
  const store = new ConfigStore(tmpFile('config.json'));
  const cfg = store.load();
  assert.equal(cfg.gateway.port, 8910);
  assert.equal(cfg.gateway.host, '127.0.0.1');
  assert.equal(cfg.gateway.autoStart, true);
  assert.equal(cfg.restart.enabled, true);
  assert.equal(cfg.ui.minimizeToTray, true);
  assert.deepEqual(store.loadWarnings, []);
});

test('config-store: save/load round-trip persists patches', () => {
  const file = tmpFile('config.json');
  const store = new ConfigStore(file);
  store.load();
  store.save({ gateway: { port: 9501, pythonPath: 'C:\\py\\python.exe' } });

  const reloaded = new ConfigStore(file).load();
  assert.equal(reloaded.gateway.port, 9501);
  assert.equal(reloaded.gateway.pythonPath, 'C:\\py\\python.exe');
  // untouched fields keep defaults
  assert.equal(reloaded.gateway.host, '127.0.0.1');
});

test('config-store: save is atomic (no .tmp leftovers, valid JSON on disk)', () => {
  const file = tmpFile('config.json');
  const store = new ConfigStore(file);
  store.load();
  store.save({ gateway: { port: 9001 } });
  const leftovers = fs.readdirSync(path.dirname(file)).filter((f) => f.includes('.tmp-'));
  assert.deepEqual(leftovers, []);
  const parsed = JSON.parse(fs.readFileSync(file, 'utf8'));
  assert.equal(parsed.gateway.port, 9001);
});

test('config-store: corrupt JSON is quarantined and defaults are used', () => {
  const file = tmpFile('config.json');
  fs.writeFileSync(file, '{"gateway": {{{ not json', 'utf8');
  const store = new ConfigStore(file);
  const cfg = store.load();
  assert.equal(cfg.gateway.port, 8910);
  assert.ok(store.loadWarnings.some((w) => w.includes('corrupt')));
  // quarantined copy exists, original path is gone
  const dir = path.dirname(file);
  assert.ok(fs.readdirSync(dir).some((f) => f.includes('.corrupt-')));
  assert.equal(fs.existsSync(file), false);
});

test('config-store: invalid patch is rejected wholesale (file untouched)', () => {
  const file = tmpFile('config.json');
  const store = new ConfigStore(file);
  store.load();
  store.save({ gateway: { port: 9100 } });
  assert.throws(() => store.save({ gateway: { port: 99999 } }), /invalid config/);
  // state and disk unchanged
  assert.equal(store.get().gateway.port, 9100);
  assert.equal(JSON.parse(fs.readFileSync(file, 'utf8')).gateway.port, 9100);
});

test('config-store: get() returns deep copies (no caller mutation leaks)', () => {
  const store = new ConfigStore(tmpFile('config.json'));
  store.load();
  const a = store.get();
  a.gateway.port = 1;
  assert.equal(store.get().gateway.port, 8910);
});

test('validateConfig: fixes bad types and reports each problem', () => {
  const { config, errors } = validateConfig({
    gateway: { port: 'not-a-number', host: '', autoStart: 'yes', readyTimeoutMs: 10 },
    health: { failureThreshold: 0 },
    ui: { theme: 'neon' },
    system: { openAtLogin: 'maybe' },
  });
  assert.equal(config.gateway.port, DEFAULT_CONFIG.gateway.port);
  assert.equal(config.gateway.host, '127.0.0.1');
  assert.equal(config.gateway.autoStart, true);
  assert.equal(config.health.failureThreshold, DEFAULT_CONFIG.health.failureThreshold);
  assert.equal(config.ui.theme, 'dark');
  assert.equal(config.system.openAtLogin, false);
  assert.ok(errors.length >= 6, `errors=${errors.join(' | ')}`);
});

test('validateConfig: accepts a fully valid config unchanged', () => {
  const input = {
    gateway: { port: 12345, host: '127.0.0.1', autoStart: false, readyTimeoutMs: 30000 },
    restart: { enabled: false, baseMs: 500, factor: 3, maxMs: 4000, maxAttempts: 2 },
    logs: { maxLines: 1000, maxBytes: 100000 },
    ui: { minimizeToTray: false, theme: 'dark' },
    system: { openAtLogin: true },
  };
  const { config, errors } = validateConfig(input);
  assert.deepEqual(errors, []);
  assert.equal(config.gateway.port, 12345);
  assert.equal(config.restart.factor, 3);
  assert.equal(config.system.openAtLogin, true);
});

test('deepMerge: nested objects merge, scalars and arrays replace', () => {
  const base = { a: { b: 1, c: 2 }, list: [1, 2], s: 'x' };
  const out = deepMerge(base, { a: { c: 9 }, list: [9], s: 'y', extra: true });
  assert.deepEqual(out, { a: { b: 1, c: 9 }, list: [9], s: 'y', extra: true });
  // base untouched
  assert.equal(base.a.c, 2);
});

test('config-store: requires a file path', () => {
  assert.throws(() => new ConfigStore(''), TypeError);
  assert.throws(() => new ConfigStore(null), TypeError);
});

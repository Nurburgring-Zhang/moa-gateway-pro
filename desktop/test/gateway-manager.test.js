'use strict';

/**
 * gateway-manager.test.js — integration tests for the service lifecycle.
 *
 * These tests spawn REAL child processes (node running
 * test/fixtures/fake-gateway.js, an actual HTTP server honoring MOA_HOST /
 * MOA_PORT and /health/ready) through the GatewayManager state machine, so
 * spawn, health polling, port self-healing, crash restart with backoff and
 * tree-kill are all exercised for real — no stubbed manager internals.
 */

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const net = require('node:net');

const { GatewayManager, STATES, httpGetJson } = require('../src/main/gateway-manager');
const { ConfigStore } = require('../src/main/config-store');
const { LogStore } = require('../src/main/log-store');
const { isPortFree } = require('../src/main/port-probe');

const FIXTURE = path.join(__dirname, 'fixtures', 'fake-gateway.js');

function tmpDir(prefix) {
  return fs.mkdtempSync(path.join(os.tmpdir(), prefix));
}

function makeConfigStore(overrides = {}) {
  const file = path.join(tmpDir('moa-mgr-cfg-'), 'config.json');
  const store = new ConfigStore(file);
  store.load();
  store.save({
    gateway: { port: 23000, host: '127.0.0.1', readyTimeoutMs: 5000, autoStart: false },
    health: { pollIntervalMs: 250, startingPollIntervalMs: 250, failureThreshold: 2 },
    restart: { enabled: true, baseMs: 100, factor: 2, maxMs: 400, maxAttempts: 3 },
    logs: { maxLines: 1000, maxBytes: 500000 },
    ...overrides,
  });
  return store;
}

function makeManager({ configStore, extraEnv = {} }) {
  const logStore = new LogStore({ maxLines: 1000, maxBytes: 500000 });
  Object.assign(process.env, extraEnv);
  const manager = new GatewayManager({
    configStore,
    logStore,
    buildCommand: () => ({ command: process.execPath, args: [FIXTURE] }),
    resolveRepo: async () => ({ path: tmpDir('moa-mgr-repo-'), source: 'test', valid: true }),
    resolvePython: async () => ({ path: process.execPath, source: 'test', version: 'node-test' }),
  });
  return { manager, logStore };
}

function clearEnv(keys) {
  for (const k of keys) delete process.env[k];
}

function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

async function waitFor(cond, timeoutMs = 20000, intervalMs = 50) {
  const t0 = Date.now();
  while (Date.now() - t0 < timeoutMs) {
    if (cond()) return true;
    await sleep(intervalMs); // eslint-disable-line no-await-in-loop
  }
  return cond();
}

test('manager: start -> running -> stop releases the port', async () => {
  const configStore = makeConfigStore();
  configStore.save({ gateway: { port: 23100 } });
  const { manager, logStore } = makeManager({ configStore });
  try {
    await manager.start();
    const becameRunning = await waitFor(() => manager.state === STATES.RUNNING);
    assert.ok(becameRunning, `state=${manager.state} lastError=${manager.lastError}`);

    const status = manager.getStatus();
    assert.equal(status.state, 'running');
    assert.equal(status.port, 23100);
    assert.ok(status.pid > 0);
    assert.equal(status.url, 'http://127.0.0.1:23100/');
    assert.ok(status.uptimeMs >= 0);

    // The child's stdout was captured into the log store.
    const allText = logStore.snapshot().map((e) => e.text).join('\n');
    assert.ok(allText.includes('[fake-gateway] listening'), 'child stdout should be captured');

    // The readiness endpoint really answers over HTTP.
    const ready = await httpGetJson('http://127.0.0.1:23100/health/ready', 3000);
    assert.equal(ready.statusCode, 200);
    assert.equal(ready.body.status, 'healthy');

    await manager.stop();
    assert.equal(manager.state, STATES.STOPPED);
    await sleep(100);
    assert.equal(await isPortFree(23100), true, 'port must be released after stop');
  } finally {
    await manager.dispose();
  }
});

test('manager: occupied preferred port self-heals to the next free port', async () => {
  const configStore = makeConfigStore();
  configStore.save({ gateway: { port: 23200 } });
  // Occupy the preferred port with a real listener.
  const blocker = net.createServer();
  await new Promise((resolve) => blocker.listen(23200, '127.0.0.1', resolve));

  const { manager, logStore } = makeManager({ configStore });
  try {
    await manager.start();
    const becameRunning = await waitFor(() => manager.state === STATES.RUNNING);
    assert.ok(becameRunning, `state=${manager.state} lastError=${manager.lastError}`);
    assert.ok(manager.port > 23200, `expected port > 23200, got ${manager.port}`);
    const logged = logStore.snapshot().map((e) => e.text).join('\n');
    assert.ok(logged.includes('busy'), 'port conflict should be logged');
    await manager.stop();
  } finally {
    await manager.dispose();
    await new Promise((resolve) => blocker.close(resolve));
  }
});

test('manager: transient crash is auto-restarted and recovers', async () => {
  const stateDir = tmpDir('moa-mgr-state-');
  const configStore = makeConfigStore();
  configStore.save({ gateway: { port: 23300 } });
  const { manager } = makeManager({
    configStore,
    extraEnv: { CRASH_FIRST_RUN: '1', STATE_DIR: stateDir },
  });
  try {
    await manager.start();
    // The first instance can pass readiness before its injected crash fires,
    // so require BOTH a completed restart cycle and the recovered state —
    // waiting on RUNNING alone could return before the crash even happens.
    const recovered = await waitFor(
      () => manager.restartCount >= 1 && manager.state === STATES.RUNNING,
      25000,
    );
    assert.ok(recovered, `state=${manager.state} restartCount=${manager.restartCount} lastError=${manager.lastError}`);
    assert.ok(manager.restartCount >= 1, `restartCount=${manager.restartCount}`);
    assert.equal(manager.state, STATES.RUNNING);
    await manager.stop();
  } finally {
    clearEnv(['CRASH_FIRST_RUN', 'STATE_DIR']);
    await manager.dispose();
  }
});

test('manager: persistent crashes exhaust the backoff and end in failed', async () => {
  const configStore = makeConfigStore();
  configStore.save({
    gateway: { port: 23400 },
    restart: { enabled: true, baseMs: 60, factor: 2, maxMs: 240, maxAttempts: 2 },
  });
  const { manager } = makeManager({
    configStore,
    extraEnv: { CRASH_AFTER_MS: '50' },
  });
  try {
    await manager.start();
    const becameFailed = await waitFor(() => manager.state === STATES.FAILED, 25000);
    assert.ok(becameFailed, `state=${manager.state}`);
    assert.ok(manager.restartCount >= 2, `restartCount=${manager.restartCount}`);
    assert.ok(/gave up after 2 restart attempts/.test(manager.lastError || ''), manager.lastError);
  } finally {
    clearEnv(['CRASH_AFTER_MS']);
    await manager.dispose();
  }
});

test('manager: readiness timeout terminates the process and reports failed', async () => {
  const configStore = makeConfigStore();
  configStore.save({
    gateway: { port: 23500, readyTimeoutMs: 5000 },
    restart: { enabled: false },
  });
  const { manager, logStore } = makeManager({
    configStore,
    extraEnv: { ALWAYS_503: '1' },
  });
  try {
    await manager.start();
    const becameFailed = await waitFor(() => manager.state === STATES.FAILED, 20000);
    assert.ok(becameFailed, `state=${manager.state}`);
    const logged = logStore.snapshot().map((e) => e.text).join('\n');
    assert.ok(logged.includes('readiness timeout'), 'timeout should be logged');
    assert.equal(await isPortFree(23500), true, 'process must be killed after readiness timeout');
  } finally {
    clearEnv(['ALWAYS_503']);
    await manager.dispose();
  }
});

test('manager: stop() during backoff cancels the pending restart', async () => {
  const configStore = makeConfigStore();
  configStore.save({
    gateway: { port: 23600 },
    restart: { enabled: true, baseMs: 1200, factor: 1, maxMs: 1200, maxAttempts: 5 },
  });
  const { manager } = makeManager({
    configStore,
    extraEnv: { CRASH_AFTER_MS: '50' },
  });
  try {
    await manager.start();
    const inBackoff = await waitFor(() => manager.state === STATES.BACKOFF);
    assert.ok(inBackoff, `state=${manager.state}`);
    assert.ok(manager.nextRestartAt > Date.now());

    await manager.stop();
    assert.equal(manager.state, STATES.STOPPED);
    // Wait past the cancelled restart window: nothing must respawn.
    await sleep(1600);
    assert.equal(manager.state, STATES.STOPPED, 'no restart may happen after explicit stop');
    assert.equal(manager.child, null);
  } finally {
    clearEnv(['CRASH_AFTER_MS']);
    await manager.dispose();
  }
});

test('manager: start is idempotent while already running', async () => {
  const configStore = makeConfigStore();
  configStore.save({ gateway: { port: 23700 } });
  const { manager } = makeManager({ configStore });
  try {
    await manager.start();
    await waitFor(() => manager.state === STATES.RUNNING);
    const firstPid = manager.pid;
    await manager.start(); // must not double-spawn
    assert.equal(manager.pid, firstPid);
    await manager.stop();
  } finally {
    await manager.dispose();
  }
});

test('manager: missing repo/python resolvers produce a clear failed state', async () => {
  const configStore = makeConfigStore();
  const logStore = new LogStore();
  const manager = new GatewayManager({
    configStore,
    logStore,
    resolveRepo: async () => ({ path: null, source: 'none', valid: false, candidates: ['/nowhere'] }),
    resolvePython: async () => ({ path: null, source: 'none', candidates: [] }),
  });
  try {
    await manager.start();
    assert.equal(manager.state, STATES.FAILED);
    assert.ok(manager.lastError.includes('MOA Gateway repo not found'));
    assert.ok(manager.lastError.includes('/nowhere'));
  } finally {
    await manager.dispose();
  }
});

'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const net = require('node:net');

const { isPortFree, findFreePort, MIN_PORT, MAX_PORT } = require('../src/main/port-probe');

function listen(port, host = '127.0.0.1') {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.once('error', reject);
    server.listen(port, host, () => resolve(server));
  });
}

function close(server) {
  return new Promise((resolve) => server.close(() => resolve()));
}

test('port-probe: reports a genuinely free port as free', async () => {
  // Bind port 0 to let the OS hand us a free port, then release it.
  const server = await listen(0);
  const port = server.address().port;
  await close(server);
  assert.equal(await isPortFree(port), true);
});

test('port-probe: reports an occupied port as busy', async () => {
  const server = await listen(0);
  const port = server.address().port;
  try {
    assert.equal(await isPortFree(port), false);
  } finally {
    await close(server);
  }
  // After release it becomes free again.
  assert.equal(await isPortFree(port), true);
});

test('port-probe: detects a port bound on 0.0.0.0 as busy on 127.0.0.1', async () => {
  const server = await listen(0, '0.0.0.0');
  try {
    const port = server.address().port;
    assert.equal(await isPortFree(port, '127.0.0.1'), false);
  } finally {
    // Always release the blocker: an open server would keep the test-runner
    // process alive and hang the whole suite.
    await close(server);
  }
});

test('port-probe: self-connect TIME_WAIT does not poison the probe', async () => {
  // Windows assigns ephemeral source ports sequentially from the same range
  // we probe, so an outbound connect can get source port == destination port
  // and complete via TCP simultaneous open (a "self-connection"). That leaves
  // a TIME_WAIT entry on the port; the probe must still report it as free.
  const server = await listen(0);
  const port = server.address().port;
  await close(server);

  const self = net.connect({ port, host: '127.0.0.1', localPort: port });
  const connected = await new Promise((resolve) => {
    self.once('connect', () => resolve(true));
    self.once('error', () => resolve(false));
    setTimeout(() => resolve(false), 3000).unref();
  });
  self.destroy();
  assert.equal(connected, true, 'self-connection should establish (sanity)');
  assert.equal(await isPortFree(port), true, 'port must be free despite self-connect TIME_WAIT');
});

test('port-probe: findFreePort returns the start port when it is free', async () => {
  const server = await listen(0);
  const port = server.address().port;
  await close(server);
  const found = await findFreePort(port);
  assert.equal(found, port);
});

test('port-probe: findFreePort scans upward past occupied ports', async () => {
  // Occupy a contiguous block, then ensure the scan jumps over all of them.
  const base = await (async () => {
    const s = await listen(0);
    const p = s.address().port;
    await close(s);
    return p;
  })();
  const blockers = [];
  try {
    const occupyCount = 3;
    for (let i = 0; i < occupyCount; i += 1) {
      try {
        blockers.push(await listen(base + i));
      } catch {
        // If the OS reused something, abort this scenario gracefully.
        break;
      }
    }
    if (blockers.length === occupyCount) {
      const found = await findFreePort(base, { maxProbes: 50 });
      assert.ok(found >= base + occupyCount, `found=${found}, base=${base}`);
      assert.equal(await isPortFree(found), true);
    }
  } finally {
    for (const b of blockers) await close(b);
  }
});

test('port-probe: findFreePort returns null when the range is exhausted', async () => {
  const server = await listen(0);
  const port = server.address().port;
  try {
    const found = await findFreePort(port, { maxProbes: 1 });
    assert.equal(found, null);
  } finally {
    await close(server);
  }
});

test('port-probe: findFreePort returns null past MAX_PORT', async () => {
  const found = await findFreePort(MAX_PORT, { maxProbes: 5 });
  // MAX_PORT itself may be free or busy; either a valid port or null is fine,
  // but scanning must never exceed MAX_PORT.
  assert.ok(found === null || found <= MAX_PORT);
});

test('port-probe: rejects out-of-range ports', async () => {
  await assert.rejects(() => isPortFree(0), RangeError);
  await assert.rejects(() => isPortFree(70000), RangeError);
  await assert.rejects(() => isPortFree(-5), RangeError);
  await assert.rejects(() => isPortFree(1.5), RangeError);
  await assert.rejects(() => findFreePort(0), RangeError);
  await assert.rejects(() => findFreePort(99999), RangeError);
  assert.equal(MIN_PORT, 1);
  assert.equal(MAX_PORT, 65535);
});

test('port-probe: rejects invalid maxProbes', async () => {
  await assert.rejects(() => findFreePort(8080, { maxProbes: 0 }), RangeError);
  await assert.rejects(() => findFreePort(8080, { maxProbes: -1 }), RangeError);
});

'use strict';

/**
 * port-probe.js — TCP port availability probing for the gateway service.
 *
 * Pure Node module (uses only node:net): unit-testable with plain `node --test`.
 *
 * Strategy: actually attempt to bind the port on the target host. Binding is
 * the only reliable way to detect conflicts on Windows — connect()-based
 * probes miss sockets in TIME_WAIT and sockets bound with exclusive
 * ownership. If our probe server can bind, uvicorn can bind too.
 */

const net = require('node:net');

const MIN_PORT = 1;
const MAX_PORT = 65535;

function assertPort(port) {
  if (!Number.isInteger(port) || port < MIN_PORT || port > MAX_PORT) {
    throw new RangeError(`port must be an integer in [${MIN_PORT}, ${MAX_PORT}], got ${port}`);
  }
}

/**
 * Check whether a TCP port is free on `host`.
 *
 * Two-phase check (both phases are required on Windows):
 *   1. Bind phase — try to bind the port. If binding fails (EADDRINUSE etc.)
 *      the port is definitely busy.
 *   2. Connect phase — a successful bind is NOT sufficient proof on Windows:
 *      when another process holds the port on the wildcard address (0.0.0.0),
 *      a bind on a specific interface (127.0.0.1) can still succeed and the
 *      two listeners would then share incoming connections. To detect that,
 *      we release our probe socket and attempt a TCP connect: if a
 *      connection is still accepted, some other listener owns the port.
 *
 * Validation errors (out-of-range port) reject the returned promise.
 *
 * @param {number} port
 * @param {string} [host='127.0.0.1']
 * @param {number} [timeoutMs=4000] how long to wait for each phase
 * @returns {Promise<boolean>} true if the port can be used (i.e. is free)
 */
async function isPortFree(port, host = '127.0.0.1', timeoutMs = 4000) {
  assertPort(port);
  const bindable = await canBind(port, host, timeoutMs);
  if (!bindable) return false;
  const someoneListening = await hasListener(port, host, timeoutMs);
  return !someoneListening;
}

/** Phase 1: can we bind a server socket to host:port? */
function canBind(port, host, timeoutMs) {
  return new Promise((resolve) => {
    const server = net.createServer();
    let settled = false;
    const finish = (free) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      resolve(free);
    };
    const timer = setTimeout(() => {
      // Bind is taking unusually long; treat as not free to be safe.
      try { server.close(); } catch { /* ignore */ }
      finish(false);
    }, timeoutMs);
    server.unref();
    server.once('error', (err) => {
      if (err && (err.code === 'EADDRINUSE' || err.code === 'EACCES' || err.code === 'EADDRNOTAVAIL')) {
        finish(false);
      } else {
        // Unexpected error: report not-free so the caller moves on.
        finish(false);
      }
    });
    server.once('listening', () => {
      try {
        server.close(() => finish(true));
      } catch {
        finish(true);
      }
      // In case close() never fires its callback, resolve anyway shortly.
      setTimeout(() => finish(true), 50).unref();
    });
    try {
      server.listen(port, host);
    } catch {
      finish(false);
    }
  });
}

/**
 * Phase 2: is anything accepting connections on host:port right now?
 * Wildcard listeners are invisible to a specific-interface bind probe on
 * Windows, so we connect after releasing our own probe socket. A refused
 * connection means nobody is listening; an accepted one (or a timeout, which
 * we conservatively treat as "someone is there") means the port is shared.
 *
 * Self-connection hazard: Windows picks ephemeral source ports sequentially
 * from the same dynamic range we probe, so the OS can assign our outbound
 * socket a source port EQUAL to the destination port. The SYN then loops
 * back into the connecting socket (TCP simultaneous open) and the connect
 * "succeeds" although nothing is listening. We detect that case via
 * socket.localPort and retry once: the self 4-tuple is in TIME_WAIT by then,
 * so the retry either gets refused (port truly free) or is accepted by a
 * real listener from a different source port.
 */
function hasListener(port, host, timeoutMs) {
  const target = connectTargetFor(host);

  const attempt = (retriesLeft) => new Promise((resolve) => {
    let socket;
    try {
      socket = net.connect({ port, host: target });
    } catch {
      resolve(false);
      return;
    }
    let settled = false;
    const finish = (listening) => {
      if (settled) return;
      settled = true;
      try { socket.destroy(); } catch { /* already gone */ }
      resolve(listening);
    };
    socket.once('connect', () => {
      const selfConnect = socket.localPort === port && socket.localAddress === target;
      if (selfConnect && retriesLeft > 0) {
        // Disambiguate: see the block comment above. Lock this attempt so no
        // stale event from the destroyed socket can settle the outer promise.
        settled = true;
        try { socket.destroy(); } catch { /* already gone */ }
        attempt(retriesLeft - 1).then(resolve);
        return;
      }
      // A self-connection with no retries left still means no real listener.
      finish(!selfConnect);
    });
    socket.once('error', () => finish(false)); // ECONNREFUSED etc. -> nobody there
    socket.setTimeout(Math.max(250, timeoutMs), () => finish(true));
    socket.unref();
  });

  return attempt(1);
}

/** Wildcard bind addresses are not valid connect destinations; map to loopback. */
function connectTargetFor(host) {
  if (host === '0.0.0.0') return '127.0.0.1';
  if (host === '::') return '::1';
  return host;
}

/**
 * Find a free TCP port starting at `startPort` and scanning upward.
 *
 * @param {number} startPort preferred port
 * @param {object} [opts]
 * @param {string} [opts.host='127.0.0.1']
 * @param {number} [opts.maxProbes=200] how many consecutive ports to try
 * @returns {Promise<number|null>} a free port, or null if none found in range
 */
async function findFreePort(startPort, { host = '127.0.0.1', maxProbes = 200 } = {}) {
  assertPort(startPort);
  if (!Number.isInteger(maxProbes) || maxProbes < 1) {
    throw new RangeError(`maxProbes must be a positive integer, got ${maxProbes}`);
  }
  for (let i = 0; i < maxProbes; i += 1) {
    const candidate = startPort + i;
    if (candidate > MAX_PORT) return null;
    // eslint-disable-next-line no-await-in-loop
    if (await isPortFree(candidate, host)) return candidate;
  }
  return null;
}

module.exports = { isPortFree, findFreePort, MIN_PORT, MAX_PORT };

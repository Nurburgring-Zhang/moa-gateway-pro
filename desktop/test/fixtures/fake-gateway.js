'use strict';

/**
 * test/fixtures/fake-gateway.js — a REAL HTTP process used by
 * gateway-manager.test.js. It honors the same contract the desktop client
 * depends on in production:
 *
 *   - binds MOA_HOST / MOA_PORT (the documented gateway env overrides)
 *   - GET /health/ready -> 200 {"status":"healthy"} when ready, 503 before
 *   - GET /              -> tiny HTML page (webui stand-in)
 *
 * Fault injection (all via env, mirroring failure modes of a real service):
 *   READY_DELAY_MS=n   readiness flips to 200 only after n ms
 *   ALWAYS_503=1       readiness never succeeds
 *   CRASH_AFTER_MS=n   process exits with code 3 after n ms
 *   CRASH_FIRST_RUN=1  exit(3) on the FIRST launch only (counter file in
 *                      STATE_DIR) — simulates a transient crash that the
 *                      restart policy must recover from
 */

const http = require('node:http');
const fs = require('node:fs');
const path = require('node:path');

const host = process.env.MOA_HOST || '127.0.0.1';
const port = Number(process.env.MOA_PORT || 0);
const readyDelayMs = Number(process.env.READY_DELAY_MS || 0);
const always503 = process.env.ALWAYS_503 === '1';
const crashAfterMs = Number(process.env.CRASH_AFTER_MS || 0);
const crashFirstRun = process.env.CRASH_FIRST_RUN === '1';
const stateDir = process.env.STATE_DIR || '';

console.log(`[fake-gateway] booting pid=${process.pid} on ${host}:${port}`);

if (crashFirstRun && stateDir) {
  const marker = path.join(stateDir, 'crashed-once');
  if (!fs.existsSync(marker)) {
    fs.mkdirSync(stateDir, { recursive: true });
    fs.writeFileSync(marker, String(Date.now()), 'utf8');
    console.log('[fake-gateway] simulating transient crash (first run)');
    setTimeout(() => process.exit(3), 400);
  }
}

if (crashAfterMs > 0) {
  setTimeout(() => {
    console.log('[fake-gateway] simulating crash now');
    process.exit(3);
  }, crashAfterMs);
}

const startedAt = Date.now();

const server = http.createServer((req, res) => {
  if (req.url === '/health/ready') {
    const ready = !always503 && Date.now() - startedAt >= readyDelayMs;
    res.writeHead(ready ? 200 : 503, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify(ready
      ? { status: 'healthy', components: {} }
      : { status: 'not_ready' }));
    return;
  }
  if (req.url === '/health/live') {
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ status: 'alive' }));
    return;
  }
  res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' });
  res.end('<html><body>fake-gateway webui</body></html>');
});

server.on('error', (err) => {
  console.error(`[fake-gateway] server error: ${err.message}`);
  process.exit(4);
});

server.listen(port, host, () => {
  console.log(`[fake-gateway] listening on http://${host}:${server.address().port}/`);
});

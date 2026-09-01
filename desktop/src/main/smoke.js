'use strict';

/**
 * smoke.js — headless self-test executed via `electron . --smoke-test`.
 *
 * Exercises every main-process subsystem WITHOUT creating a BrowserWindow,
 * so it can run on machines without an interactive desktop session:
 *   1. ConfigStore round-trip (real file in userData)
 *   2. RingBuffer caps
 *   3. BackoffPolicy schedule
 *   4. Port probe against a real occupied socket
 *   5. LogStore line-splitting + export
 *   6. SecretStore round-trip with the REAL Electron safeStorage (DPAPI)
 *   7. StaticServer serving the real renderer over loopback HTTP
 *   8. Gateway repo / Python discovery (reported; dev layout expected)
 *
 * Prints `[SMOKE] PASS` or `[SMOKE] FAIL …` and returns the exit code.
 */

const path = require('node:path');
const fs = require('node:fs');
const os = require('node:os');
const net = require('node:net');
const http = require('node:http');

const { ConfigStore } = require('./config-store');
const { RingBuffer } = require('./ring-buffer');
const { BackoffPolicy } = require('./backoff');
const { isPortFree, findFreePort } = require('./port-probe');
const { LogStore } = require('./log-store');
const { SecretStore } = require('./secret-store');
const { StaticServer } = require('./static-server');
const paths = require('./paths');

function httpGet(url) {
  return new Promise((resolve, reject) => {
    const req = http.get(url, { timeout: 4000 }, (res) => {
      let raw = '';
      res.on('data', (d) => { raw += d; });
      res.on('end', () => resolve({ statusCode: res.statusCode, raw }));
    });
    req.on('error', reject);
    req.on('timeout', () => req.destroy(new Error('timeout')));
  });
}

/**
 * @param {object} electronApis { app, safeStorage }
 * @returns {Promise<number>} exit code
 */
async function runSmoke({ app, safeStorage }) {
  const results = [];
  let failures = 0;
  const check = (name, ok, detail = '') => {
    results.push({ name, ok, detail });
    if (!ok) failures += 1;
    console.log(`[SMOKE] ${ok ? 'ok  ' : 'FAIL'} ${name}${detail ? ` — ${detail}` : ''}`);
  };

  const scratch = fs.mkdtempSync(path.join(os.tmpdir(), 'moa-smoke-'));

  try {
    // 1. ConfigStore -----------------------------------------------------
    {
      const file = path.join(scratch, 'cfg.json');
      const store = new ConfigStore(file);
      const defaults = store.load();
      check('config: defaults load', defaults.gateway.port === 8910, `port=${defaults.gateway.port}`);
      store.save({ gateway: { port: 9123 } });
      const again = new ConfigStore(file).load();
      check('config: save/load round-trip', again.gateway.port === 9123, `port=${again.gateway.port}`);
      fs.writeFileSync(file, '{broken json', 'utf8');
      const corrupt = new ConfigStore(file).load();
      check('config: corrupt file falls back to defaults', corrupt.gateway.port === 8910);
    }

    // 2. RingBuffer ------------------------------------------------------
    {
      const rb = new RingBuffer({ maxLines: 3, maxBytes: 1024 });
      for (let i = 0; i < 10; i += 1) rb.push({ text: `line-${i}` });
      const arr = rb.toArray();
      check('ring-buffer: line cap', arr.length === 3 && arr[0].text === 'line-7' && arr[2].text === 'line-9',
        `kept=${arr.map((e) => e.text).join(',')}`);
    }

    // 3. Backoff ---------------------------------------------------------
    {
      const bp = new BackoffPolicy({ baseMs: 100, factor: 2, maxMs: 400, maxAttempts: 4 });
      const seq = [];
      let d = bp.nextDelay();
      while (d != null) { seq.push(d); d = bp.nextDelay(); }
      const expected = '100,200,400,400';
      check('backoff: schedule + exhaustion', seq.join(',') === expected && bp.exhausted, `seq=${seq.join(',')}`);
    }

    // 4. Port probe ------------------------------------------------------
    {
      const freePort = await findFreePort(20000, { maxProbes: 50 });
      check('port-probe: finds a free port', Number.isInteger(freePort), `port=${freePort}`);
      const blocker = net.createServer();
      await new Promise((resolve) => blocker.listen(freePort, '127.0.0.1', resolve));
      const busy = await isPortFree(freePort);
      const next = await findFreePort(freePort, { maxProbes: 50 });
      blocker.close();
      check('port-probe: detects occupied port', busy === false, `port=${freePort}`);
      check('port-probe: scans past occupied port', next !== null && next > freePort, `next=${next}`);
    }

    // 5. LogStore --------------------------------------------------------
    {
      const ls = new LogStore({ maxLines: 100, maxBytes: 65536 });
      ls.append('stdout', 'hello ');
      ls.append('stdout', 'world\r\nsecond line\r\n');
      ls.append('stderr', 'err1\npartial');
      ls.flushAll();
      const snap = ls.snapshot();
      const texts = snap.map((e) => `${e.stream}:${e.text}`).join('|');
      check('log-store: chunked line splitting',
        texts === 'stdout:hello world|stdout:second line|stderr:err1|stderr:partial', texts);
      const exportPath = ls.exportTo(path.join(scratch, 'export.txt'));
      const exported = fs.readFileSync(exportPath, 'utf8');
      check('log-store: export to file', exported.includes('hello world') && exported.includes('err1'),
        `${exported.length} bytes`);
    }

    // 6. SecretStore with real safeStorage -------------------------------
    {
      const available = safeStorage.isEncryptionAvailable();
      check('secrets: safeStorage encryption available', available === true, String(available));
      if (available) {
        const file = path.join(scratch, 'secrets.json');
        const store = new SecretStore({ filePath: file, safeStorage });
        store.set('SMOKE_TEST_KEY', 'smoke-value-123');
        const onDisk = fs.readFileSync(file, 'utf8');
        check('secrets: ciphertext on disk (no plaintext)',
          !onDisk.includes('smoke-value-123') && onDisk.includes('SMOKE_TEST_KEY'));
        const back = new SecretStore({ filePath: file, safeStorage }).get('SMOKE_TEST_KEY');
        check('secrets: decrypt round-trip', back === 'smoke-value-123');
        const { env } = new SecretStore({ filePath: file, safeStorage }).allAsEnv();
        check('secrets: env injection shape', env.SMOKE_TEST_KEY === 'smoke-value-123');
      }
    }

    // 7. StaticServer ----------------------------------------------------
    {
      const rendererDir = path.join(app.getAppPath(), 'src', 'renderer');
      const server = new StaticServer({ rootDir: rendererDir });
      const { url } = await server.listen(0);
      const res = await httpGet(`${url}index.html`);
      const blocked = await httpGet(`${url}..%2f..%2fmain.js`).catch((e) => ({ statusCode: 0, raw: String(e) }));
      await server.close();
      check('static-server: serves renderer shell', res.statusCode === 200 && res.raw.includes('MOA Gateway'),
        `status=${res.statusCode}`);
      check('static-server: traversal blocked', blocked.statusCode === 404, `status=${blocked.statusCode}`);
    }

    // 8. Discovery -------------------------------------------------------
    {
      const repo = await paths.resolveGatewayRepo({ appPath: app.getAppPath(), resourcesPath: process.resourcesPath || '' });
      check('discovery: gateway repo found', Boolean(repo.path), repo.path || `tried ${(repo.candidates || []).join(' ; ')}`);
      const py = await paths.resolvePython({ repoPath: repo.path || '' });
      check('discovery: python interpreter found', Boolean(py.path),
        py.path ? `${py.path} (${py.version}, source=${py.source})` : 'none of the candidates worked');
    }
  } catch (err) {
    failures += 1;
    console.log(`[SMOKE] FAIL uncaught error — ${err.stack || err}`);
  } finally {
    try { fs.rmSync(scratch, { recursive: true, force: true }); } catch { /* scratch cleanup best-effort */ }
  }

  const passed = results.filter((r) => r.ok).length;
  console.log(`[SMOKE] ${passed}/${results.length} checks passed${failures ? `, ${failures} FAILED` : ''}`);
  console.log(failures === 0 ? '[SMOKE] PASS' : '[SMOKE] FAIL');
  return failures === 0 ? 0 : 1;
}

module.exports = { runSmoke };

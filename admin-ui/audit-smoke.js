/**
 * audit-smoke.js — MoA Gateway Pro admin-ui smoke test (regression for P1 fix)
 *
 * Flow:
 *   1. Precheck: gateway alive at http://127.0.0.1:8910, admin password file
 *      present. Either missing → print SKIP-LIVE and exit 0 (not a failure).
 *   2. Start the built admin-ui (`next start`) on a local port.
 *   3. puppeteer-core: login → /dashboard → page.reload() (hard refresh) →
 *      assert we stay on /dashboard and the stats cards render.
 *      This is the regression check for the "hard refresh drops session" bug:
 *      before the fix, the first request after reload went out with an empty
 *      token → 401 → valid token wiped + redirect to /login.
 *
 * Run: node audit-smoke.js   (from admin-ui/, after `npm run build`)
 */

'use strict';

const fs = require('fs');
const path = require('path');
const http = require('http');
const { spawn } = require('child_process');

const GATEWAY_URL = 'http://127.0.0.1:8910';
const PASSWORD_FILE = path.join(__dirname, '..', 'data', '.admin_password');
const UI_PORT = 3100;
const UI_URL = `http://127.0.0.1:${UI_PORT}`;
const CHROME_PATH = 'C:/Program Files/Google/Chrome/Application/chrome.exe';
const ADMIN_USERNAME = 'admin';
const STATS_MARKER = '总请求量'; // first stats card title on /dashboard

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function skipLive(reason) {
  console.log(`SKIP-LIVE: ${reason}`);
  process.exit(0);
}

function httpProbe(url, timeoutMs = 3000) {
  return new Promise((resolve) => {
    const req = http.get(url, (res) => {
      res.resume();
      resolve(true); // any response status means the service is up
    });
    req.setTimeout(timeoutMs, () => {
      req.destroy();
      resolve(false);
    });
    req.on('error', () => resolve(false));
  });
}

async function waitForHttp(url, timeoutMs = 60000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (await httpProbe(url, 2000)) return true;
    await sleep(1000);
  }
  return false;
}

function startUiServer() {
  // Requires a prior `npm run build` (production server serves .next output).
  if (!fs.existsSync(path.join(__dirname, '.next'))) {
    console.error('FAIL: .next build output missing — run `npm run build` first.');
    process.exit(1);
  }
  const child = spawn('npx', ['next', 'start', '-p', String(UI_PORT)], {
    cwd: __dirname,
    shell: true,
    stdio: 'ignore',
    detached: false,
  });
  child.on('error', (err) => {
    console.error('FAIL: could not start UI server:', err.message);
    process.exit(1);
  });
  return child;
}

async function main() {
  // --- Prechecks (SKIP-LIVE paths) -----------------------------------------
  if (!(await httpProbe(`${GATEWAY_URL}/health`))) {
    skipLive(`gateway not reachable at ${GATEWAY_URL}`);
  }
  if (!fs.existsSync(PASSWORD_FILE)) {
    skipLive(`admin password file not found: ${PASSWORD_FILE}`);
  }
  const password = fs.readFileSync(PASSWORD_FILE, 'utf8').trim();
  if (!password) {
    skipLive('admin password file is empty');
  }
  if (!fs.existsSync(CHROME_PATH)) {
    skipLive(`Chrome executable not found at ${CHROME_PATH}`);
  }

  // --- Bring up the UI server ----------------------------------------------
  const ui = startUiServer();
  let browser = null;
  let failed = false;

  const cleanup = () => {
    try {
      if (browser) browser.close();
    } catch (_) {}
    try {
      if (ui && !ui.killed) ui.kill();
    } catch (_) {}
  };
  process.on('exit', cleanup);
  process.on('SIGINT', () => {
    cleanup();
    process.exit(130);
  });

  try {
    if (!(await waitForHttp(UI_URL, 60000))) {
      throw new Error(`UI server did not become ready at ${UI_URL} within 60s`);
    }

    const puppeteer = require('puppeteer-core');
    browser = await puppeteer.launch({
      executablePath: CHROME_PATH,
      headless: 'new',
      args: ['--no-sandbox', '--disable-dev-shm-usage'],
    });
    const page = await browser.newPage();
    page.setDefaultTimeout(30000);

    // 1) Login ---------------------------------------------------------------
    await page.goto(`${UI_URL}/login`, { waitUntil: 'networkidle0' });
    await page.type('#username', ADMIN_USERNAME);
    await page.type('#password', password);
    await Promise.all([
      page.waitForFunction(() => window.location.pathname.startsWith('/dashboard'), {
        timeout: 30000,
      }),
      page.click('button[type="submit"]'),
    ]);
    console.log('PASS: login redirected to /dashboard');

    // 2) Dashboard loads with stats cards ------------------------------------
    await page.waitForFunction(
      (marker) => document.body.innerText.includes(marker),
      { timeout: 30000 },
      STATS_MARKER
    );
    console.log('PASS: stats cards visible after login');

    // 3) HARD REFRESH — the P1 regression check ------------------------------
    await page.reload({ waitUntil: 'networkidle0' });
    // Give any (buggy) 401 → /login redirect time to happen.
    await sleep(2000);

    const urlAfterReload = page.url();
    if (!urlAfterReload.includes('/dashboard')) {
      throw new Error(
        `after hard refresh expected URL to stay on /dashboard, got: ${urlAfterReload}`
      );
    }
    console.log('PASS: still on /dashboard after hard refresh');

    const tokenSurvived = await page.evaluate(
      () => !!localStorage.getItem('moa_admin_token')
    );
    if (!tokenSurvived) {
      throw new Error('after hard refresh the stored token was wiped from localStorage');
    }
    console.log('PASS: token still present in localStorage after hard refresh');

    await page.waitForFunction(
      (marker) => document.body.innerText.includes(marker),
      { timeout: 30000 },
      STATS_MARKER
    );
    console.log('PASS: stats cards visible after hard refresh');

    console.log('SMOKE-OK: all assertions passed');
  } catch (err) {
    failed = true;
    console.error(`FAIL: ${err && err.message ? err.message : err}`);
  } finally {
    cleanup();
  }
  process.exit(failed ? 1 : 0);
}

main();

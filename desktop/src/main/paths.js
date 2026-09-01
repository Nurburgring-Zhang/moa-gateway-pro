'use strict';

/**
 * paths.js — discovery of the MOA Gateway repo and a usable Python interpreter.
 *
 * Pure Node module (node:fs/node:path/node:child_process only): no Electron
 * import, so the discovery rules are unit-testable against fixture trees.
 *
 * Layout assumptions (matching the real repo):
 *   <repo>/moa_gateway/server.py   — gateway package (ROOT_DIR = <repo>)
 *   <repo>/config.yaml             — gateway configuration
 *   <repo>/../venv/Scripts/python.exe — the project virtualenv (preferred)
 */

const fs = require('node:fs');
const path = require('node:path');
const { spawn } = require('node:child_process');

const IS_WIN = process.platform === 'win32';

/** True when `dir` looks like a MOA Gateway repo checkout. */
function isGatewayRepo(dir) {
  if (typeof dir !== 'string' || dir.trim() === '') return false;
  try {
    return (
      fs.existsSync(path.join(dir, 'moa_gateway', 'server.py')) &&
      fs.existsSync(path.join(dir, 'moa_gateway', '__init__.py'))
    );
  } catch {
    return false;
  }
}

/** True when `file` exists and looks like an executable python. */
function isPythonExecutable(file) {
  try {
    if (!fs.existsSync(file)) return false;
    const stat = fs.statSync(file);
    return stat.isFile();
  } catch {
    return false;
  }
}

/**
 * Candidate gateway repo paths, best first.
 *
 * @param {object} ctx
 * @param {string} [ctx.configuredRepoPath] user override from desktop config
 * @param {string} [ctx.appPath] Electron app.getPath('appPath') — desktop/ in dev, app.asar when packaged
 * @param {string} [ctx.resourcesPath] Electron process.resourcesPath (packaged builds)
 * @param {string} [ctx.cwd] override for process.cwd() (tests)
 * @returns {Array<{path: string, source: string}>}
 */
function candidateRepoPaths({ configuredRepoPath = '', appPath = '', resourcesPath = '', cwd } = {}) {
  const out = [];
  const push = (p, source) => {
    if (typeof p === 'string' && p.trim() !== '') out.push({ path: path.resolve(p), source });
  };

  // 1. Explicit user configuration always wins.
  push(configuredRepoPath, 'user-configured');

  // 2. Packaged build: gateway source bundled under resources/gateway
  //    (electron-builder extraResources — see electron-builder.yml).
  if (resourcesPath) push(path.join(resourcesPath, 'gateway'), 'bundled-resources');

  // 3. Development layout: desktop/ lives directly inside the gateway repo.
  if (appPath) push(path.resolve(appPath, '..'), 'dev-layout');

  // 4. Last resort: parent of the working directory.
  push(path.resolve(cwd || process.cwd(), '..'), 'cwd-parent');

  // De-duplicate while preserving order.
  const seen = new Set();
  return out.filter((c) => {
    const key = c.path.toLowerCase();
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

/**
 * Resolve the gateway repo: first candidate that passes isGatewayRepo().
 * @returns {Promise<{path: string, source: string} | {path: null, source: string, candidates: string[]}>}
 */
async function resolveGatewayRepo(ctx = {}) {
  const candidates = candidateRepoPaths(ctx);
  for (const c of candidates) {
    if (isGatewayRepo(c.path)) return { ...c, valid: true };
  }
  return {
    path: null,
    source: 'none',
    valid: false,
    candidates: candidates.map((c) => c.path),
  };
}

/**
 * Candidate python interpreter paths for a given repo, best first.
 * Order: user override → project venv (../venv, ./venv, ./.venv) → bare 'python'.
 *
 * @param {object} ctx
 * @param {string} [ctx.configuredPythonPath]
 * @param {string} [ctx.repoPath] resolved gateway repo path
 * @returns {Array<{path: string, source: string}>}
 */
function candidatePythonPaths({ configuredPythonPath = '', repoPath = '' } = {}) {
  const out = [];
  const push = (p, source) => {
    if (typeof p === 'string' && p.trim() !== '') out.push({ path: p, source });
  };

  push(configuredPythonPath, 'user-configured');

  if (repoPath) {
    const repo = path.resolve(repoPath);
    const venvNames = ['venv', '.venv'];
    for (const name of venvNames) {
      // Conventional project venv one level above the repo (this project's layout)…
      push(path.join(repo, '..', name, 'Scripts', 'python.exe'), `${name}-above-repo`);
      // …and inside the repo.
      push(path.join(repo, name, 'Scripts', 'python.exe'), `${name}-in-repo`);
    }
    if (!IS_WIN) {
      for (const name of venvNames) {
        push(path.join(repo, '..', name, 'bin', 'python'), `${name}-above-repo`);
        push(path.join(repo, name, 'bin', 'python'), `${name}-in-repo`);
      }
    }
  }

  // System interpreters (resolved via PATH at spawn time).
  push(IS_WIN ? 'python' : 'python3', 'system-path');
  if (IS_WIN) push('py', 'py-launcher');

  const seen = new Set();
  return out.filter((c) => {
    const key = c.path.toLowerCase();
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

/**
 * Resolve a python interpreter. Absolute-path candidates must exist on disk;
 * bare names ('python', 'py') are verified by running `<name> --version`.
 *
 * @returns {Promise<{path: string, source: string, version: string} | {path: null, source: string, candidates: Array<{path:string,source:string,reason:string}>}>}
 */
async function resolvePython(ctx = {}) {
  const candidates = candidatePythonPaths(ctx);
  const rejected = [];
  for (const c of candidates) {
    const isBare = c.path === 'python' || c.path === 'python3' || c.path === 'py';
    if (!isBare && !isPythonExecutable(c.path)) {
      rejected.push({ ...c, reason: 'not found on disk' });
      continue; // eslint-disable-line no-continue
    }
    // eslint-disable-next-line no-await-in-loop
    const version = await getPythonVersion(c.path);
    if (version) return { path: c.path, source: c.source, version };
    rejected.push({ ...c, reason: '--version probe failed' });
  }
  return { path: null, source: 'none', candidates: rejected };
}

/**
 * Run `<python> --version` with a timeout; returns e.g. "Python 3.12.11" or null.
 * @param {string} pythonPath
 * @param {number} [timeoutMs=8000]
 */
function getPythonVersion(pythonPath, timeoutMs = 8000) {
  return new Promise((resolvePromise) => {
    let settled = false;
    const finish = (v) => {
      if (!settled) {
        settled = true;
        resolvePromise(v);
      }
    };
    let child;
    try {
      child = spawn(pythonPath, ['--version'], {
        windowsHide: true,
        shell: false,
        timeout: timeoutMs,
      });
    } catch {
      finish(null);
      return;
    }
    let out = '';
    const timer = setTimeout(() => {
      try { child.kill(); } catch { /* ignore */ }
      finish(null);
    }, timeoutMs);
    timer.unref();
    child.stdout.on('data', (d) => { out += d.toString(); });
    child.stderr.on('data', (d) => { out += d.toString(); }); // py launcher prints version to stderr
    child.on('error', () => finish(null));
    child.on('close', () => {
      const m = out.match(/Python\s+([0-9][^\s]*)/i);
      finish(m ? `Python ${m[1]}` : null);
    });
  });
}

module.exports = {
  isGatewayRepo,
  isPythonExecutable,
  candidateRepoPaths,
  resolveGatewayRepo,
  candidatePythonPaths,
  resolvePython,
  getPythonVersion,
  IS_WIN,
};

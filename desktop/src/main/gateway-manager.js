'use strict';

/**
 * gateway-manager.js — lifecycle manager for the Python MOA Gateway child process.
 *
 * Responsibilities:
 *   - spawn `python -m uvicorn moa_gateway.server:app --host <host> --port <port>`
 *     with cwd = gateway repo, injecting MOA_HOST/MOA_PORT (the gateway's
 *     documented env overrides, see moa_gateway/config.py load_settings) and
 *     the user's safeStorage-encrypted API keys as env vars
 *   - port-conflict self-healing: probe before spawn, scan upward when busy
 *   - health monitoring: poll GET /health/ready, drive state transitions
 *   - crash auto-restart with exponential backoff (bounded attempts)
 *   - clean shutdown: on Windows the venv launcher spawns a child interpreter
 *     that owns the socket, so we kill the whole process tree (taskkill /T)
 *
 * No Electron imports here — everything is injected, which keeps the module
 * testable under plain `node --test` (see test/gateway-manager.test.js).
 */

const EventEmitter = require('node:events');
const http = require('node:http');
const { spawn, execFile } = require('node:child_process');
const { BackoffPolicy } = require('./backoff');
const { findFreePort } = require('./port-probe');
const paths = require('./paths');

const STATES = Object.freeze({
  STOPPED: 'stopped',
  STARTING: 'starting',
  RUNNING: 'running',
  DEGRADED: 'degraded',
  STOPPING: 'stopping',
  BACKOFF: 'backoff',
  FAILED: 'failed',
});

/** Default spawn command: the exact line documented for this gateway. */
function defaultBuildCommand({ pythonPath, host, port }) {
  return {
    command: pythonPath,
    args: ['-m', 'uvicorn', 'moa_gateway.server:app', '--host', host, '--port', String(port)],
  };
}

/**
 * Minimal JSON-over-HTTP GET with timeout.
 * @param {string} url
 * @param {number} timeoutMs
 * @returns {Promise<{statusCode:number, body:any, raw:string}>}
 */
function httpGetJson(url, timeoutMs = 4000) {
  return new Promise((resolve, reject) => {
    const req = http.get(url, { timeout: timeoutMs }, (res) => {
      let raw = '';
      res.setEncoding('utf8');
      res.on('data', (d) => {
        raw += d;
        if (raw.length > 1024 * 1024) {
          req.destroy(new Error('response too large'));
        }
      });
      res.on('end', () => {
        let body = raw;
        try { body = JSON.parse(raw); } catch { /* keep raw text */ }
        resolve({ statusCode: res.statusCode || 0, body, raw });
      });
    });
    req.on('timeout', () => req.destroy(new Error(`timeout after ${timeoutMs}ms`)));
    req.on('error', (err) => reject(err));
  });
}

class GatewayManager extends EventEmitter {
  /**
   * @param {object} deps
   * @param {object} deps.configStore ConfigStore instance
   * @param {object} deps.logStore LogStore instance
   * @param {object} [deps.secretStore] SecretStore instance (optional)
   * @param {(ctx:{pythonPath:string, host:string, port:number})=>{command:string,args:string[]}} [deps.buildCommand]
   *        spawn command factory — injectable for tests; default runs uvicorn
   * @param {(ctx:object)=>Promise<object>} [deps.resolveRepo] injectable repo resolver
   * @param {(ctx:object)=>Promise<object>} [deps.resolvePython] injectable python resolver
   * @param {string} [deps.platform] injectable process.platform (tests)
   */
  constructor(deps = {}) {
    super();
    if (!deps.configStore) throw new TypeError('GatewayManager requires configStore');
    if (!deps.logStore) throw new TypeError('GatewayManager requires logStore');
    this.configStore = deps.configStore;
    this.logStore = deps.logStore;
    this.secretStore = deps.secretStore || null;
    this.buildCommand = deps.buildCommand || defaultBuildCommand;
    this.resolveRepo = deps.resolveRepo || ((ctx) => paths.resolveGatewayRepo(ctx));
    this.resolvePython = deps.resolvePython || ((ctx) => paths.resolvePython(ctx));
    this.platform = deps.platform || process.platform;

    this.state = STATES.STOPPED;
    this.child = null;
    this.pid = null;
    this.port = null;
    this.host = null;
    this.pythonPath = null;
    this.repoPath = null;
    this.startedAt = null;
    this.lastReadyAt = null;
    this.lastError = null;
    this.lastExitCode = null;
    this.restartCount = 0;
    this.nextRestartAt = null;
    this.healthDetail = null;
    this.consecutiveFailures = 0;
    this.lastHealthCheckAt = null;

    this._backoff = null;
    this._restartTimer = null;
    this._healthTimer = null;
    this._portConflictHint = false;
    this._stderrTail = '';
    this._disposed = false;
  }

  /** Machine-readable snapshot for IPC + UI badge. */
  getStatus() {
    const cfg = this.configStore.get();
    return {
      state: this.state,
      pid: this.pid,
      host: this.host,
      port: this.port,
      url: this.port ? `http://${this.host}:${this.port}/` : null,
      readyUrl: this.port ? `http://${this.host}:${this.port}/health/ready` : null,
      pythonPath: this.pythonPath,
      repoPath: this.repoPath,
      startedAt: this.startedAt,
      uptimeMs: this.startedAt && this.state !== STATES.STOPPED ? Date.now() - this.startedAt : 0,
      restartCount: this.restartCount,
      lastError: this.lastError,
      lastExitCode: this.lastExitCode,
      nextRestartAt: this.nextRestartAt,
      consecutiveFailures: this.consecutiveFailures,
      lastHealthCheckAt: this.lastHealthCheckAt,
      lastReadyAt: this.lastReadyAt,
      healthDetail: this.healthDetail,
      configuredPort: cfg.gateway.port,
      restartEnabled: cfg.restart.enabled,
      states: STATES,
    };
  }

  _setState(next, extra = {}) {
    const prev = this.state;
    this.state = next;
    Object.assign(this, extra);
    if (next !== STATES.BACKOFF) this.nextRestartAt = null;
    this.logStore.system(`service state: ${prev} -> ${next}${extra.lastError ? ` (${extra.lastError})` : ''}`);
    this.emit('status', this.getStatus());
  }

  /**
   * Start the gateway. Idempotent: returns current status when already
   * starting/running.
   */
  async start() {
    if (this._disposed) throw new Error('manager is disposed');
    if (this.state === STATES.STARTING || this.state === STATES.RUNNING || this.state === STATES.STOPPING) {
      return this.getStatus();
    }
    this._cancelRestartTimer();
    this.lastError = null;

    const cfg = this.configStore.get();

    // 1. Locate the gateway repo.
    const repo = await this.resolveRepo({
      configuredRepoPath: cfg.gateway.repoPath,
      appPath: this._appPath || '',
      resourcesPath: this._resourcesPath || '',
    });
    if (!repo.path) {
      this._setState(STATES.FAILED, {
        lastError: 'MOA Gateway repo not found. Set the repo path in Settings. Tried: ' +
          (repo.candidates || []).join(', '),
      });
      return this.getStatus();
    }

    // 2. Locate a python interpreter with the gateway's dependencies.
    const py = await this.resolvePython({
      configuredPythonPath: cfg.gateway.pythonPath,
      repoPath: repo.path,
    });
    if (!py.path) {
      this._setState(STATES.FAILED, {
        lastError: 'No usable Python interpreter found. Set the Python path in Settings. Tried: ' +
          (py.candidates || []).map((c) => `${c.path} (${c.reason})`).join('; '),
      });
      return this.getStatus();
    }

    // 3. Pick a free port (self-heal conflicts by scanning upward).
    const desired = this._portConflictHint ? cfg.gateway.port + 1 : cfg.gateway.port;
    this._portConflictHint = false;
    const port = await findFreePort(desired, { host: cfg.gateway.host, maxProbes: 200 });
    if (port == null) {
      this._setState(STATES.FAILED, {
        lastError: `no free TCP port found starting at ${desired} (200 ports probed)`,
      });
      return this.getStatus();
    }
    if (port !== cfg.gateway.port) {
      this.logStore.system(`port ${cfg.gateway.port} is busy — using ${port} instead (auto-resolved)`);
    }

    // 4. Build env: documented MOA_* overrides + stored API keys.
    const env = {
      ...process.env,
      MOA_HOST: cfg.gateway.host,
      MOA_PORT: String(port),
      PYTHONIOENCODING: 'utf-8',
      PYTHONUNBUFFERED: '1',
    };
    if (this.secretStore) {
      try {
        const { env: secretEnv, skipped } = this.secretStore.allAsEnv();
        Object.assign(env, secretEnv);
        const names = Object.keys(secretEnv);
        if (names.length > 0) this.logStore.system(`injecting stored API keys into service env: ${names.join(', ')}`);
        if (skipped.length > 0) this.logStore.system(`WARNING: could not decrypt stored keys: ${skipped.join(', ')}`);
      } catch (err) {
        this.logStore.system(`WARNING: secret store unavailable (${err.message}); starting without stored keys`);
      }
    }

    // 5. Spawn.
    const { command, args } = this.buildCommand({ pythonPath: py.path, host: cfg.gateway.host, port });
    this.logStore.system(`spawning: ${command} ${args.join(' ')}  (cwd=${repo.path})`);
    let child;
    try {
      child = spawn(command, args, {
        cwd: repo.path,
        env,
        windowsHide: true,
        stdio: ['ignore', 'pipe', 'pipe'],
      });
    } catch (err) {
      this._setState(STATES.FAILED, { lastError: `spawn failed: ${err.message}` });
      return this.getStatus();
    }

    this.child = child;
    this.pid = child.pid || null;
    this.port = port;
    this.host = cfg.gateway.host;
    this.pythonPath = py.path;
    this.repoPath = repo.path;
    this.startedAt = Date.now();
    this.consecutiveFailures = 0;
    this._stderrTail = '';
    this._setState(STATES.STARTING);

    child.stdout.setEncoding('utf8');
    child.stderr.setEncoding('utf8');
    child.stdout.on('data', (d) => this.logStore.append('stdout', d));
    child.stderr.on('data', (d) => {
      this.logStore.append('stderr', d);
      this._stderrTail = (this._stderrTail + d).slice(-8192);
    });
    child.on('error', (err) => {
      this.logStore.system(`process error: ${err.message}`);
      if (err.code === 'ENOENT') {
        this.lastError = `Python interpreter not executable: ${this.pythonPath} (${err.message})`;
      }
    });
    child.on('exit', (code, signal) => this._handleExit(code, signal));

    this._scheduleHealthTick(true);
    return this.getStatus();
  }

  /** Stop the gateway and do not restart it. */
  async stop() {
    this._cancelRestartTimer();
    if (this.state === STATES.STOPPED && !this.child) return this.getStatus();
    this._setState(STATES.STOPPING);
    this._stopHealthLoop();
    const child = this.child;
    if (!child) {
      this._setState(STATES.STOPPED);
      return this.getStatus();
    }
    await this._killTree(child);
    // _handleExit performs the final transition; guard against races where
    // the exit event was already consumed.
    if (this.child === child) {
      this.child = null;
      this.pid = null;
      this._setState(STATES.STOPPED);
    }
    return this.getStatus();
  }

  /** Stop, then start again (fresh port probe included). */
  async restart() {
    this.logStore.system('restart requested');
    await this.stop();
    return this.start();
  }

  /** Release timers and kill the child (app quit). */
  async dispose() {
    this._disposed = true;
    this._cancelRestartTimer();
    this._stopHealthLoop();
    if (this.child) {
      try { await this._killTree(this.child); } catch { /* ignore on shutdown */ }
    }
    this.child = null;
    this.pid = null;
    this.state = STATES.STOPPED;
  }

  // ------------------------------------------------------------------
  // internals
  // ------------------------------------------------------------------

  _handleExit(code, signal) {
    const child = this.child;
    this.child = null;
    this.pid = null;
    this.logStore.flushAll();
    this.lastExitCode = code;
    this._stopHealthLoop();

    if (this.state === STATES.STOPPING || this.state === STATES.STOPPED) {
      this.logStore.system(`process exited (code=${code}, signal=${signal}) — stopped`);
      this._setState(STATES.STOPPED);
      return;
    }

    // Detect port conflicts from uvicorn's stderr so the next attempt moves
    // to a different port instead of crashing in a loop.
    if (/address already in use|errno 10048|EADDRINUSE|error while attempting to listen/i.test(this._stderrTail)) {
      this._portConflictHint = true;
      this.logStore.system('detected port conflict in service output — next start will pick another port');
    }

    this.restartCount += 1;
    const cfg = this.configStore.get();
    const reason = `process exited unexpectedly (code=${code}, signal=${signal})`;
    this.logStore.system(reason);

    if (!cfg.restart.enabled) {
      this._setState(STATES.FAILED, { lastError: `${reason}; auto-restart is disabled` });
      return;
    }

    if (!this._backoff) {
      this._backoff = new BackoffPolicy({
        baseMs: cfg.restart.baseMs,
        factor: cfg.restart.factor,
        maxMs: cfg.restart.maxMs,
        maxAttempts: cfg.restart.maxAttempts,
      });
    }
    const delay = this._backoff.nextDelay();
    if (delay == null) {
      this._setState(STATES.FAILED, {
        lastError: `${reason}; gave up after ${this._backoff.maxAttempts} restart attempts`,
      });
      return;
    }
    this.nextRestartAt = Date.now() + delay;
    this._setState(STATES.BACKOFF, { lastError: reason });
    this.logStore.system(`auto-restart in ${Math.round(delay / 1000)}s (attempt ${this._backoff.attempts}/${this._backoff.maxAttempts})`);
    this._restartTimer = setTimeout(() => {
      this._restartTimer = null;
      this.start().catch((err) => {
        this._setState(STATES.FAILED, { lastError: `restart failed: ${err.message}` });
      });
    }, delay);
    this._restartTimer.unref();
  }

  _cancelRestartTimer() {
    if (this._restartTimer) {
      clearTimeout(this._restartTimer);
      this._restartTimer = null;
    }
    this.nextRestartAt = null;
  }

  _stopHealthLoop() {
    if (this._healthTimer) {
      clearTimeout(this._healthTimer);
      this._healthTimer = null;
    }
  }

  _scheduleHealthTick(immediate = false) {
    this._stopHealthLoop();
    const cfg = this.configStore.get();
    const interval = this.state === STATES.STARTING
      ? cfg.health.startingPollIntervalMs
      : cfg.health.pollIntervalMs;
    const run = () => this._healthTick();
    if (immediate) {
      this._healthTimer = setTimeout(run, 250);
    } else {
      this._healthTimer = setTimeout(run, interval);
    }
    this._healthTimer.unref();
  }

  async _healthTick() {
    this._healthTimer = null;
    if (this._disposed) return;
    if (![STATES.STARTING, STATES.RUNNING, STATES.DEGRADED].includes(this.state)) return;
    if (!this.child || !this.port) return;

    const cfg = this.configStore.get();
    const url = `http://${this.host}:${this.port}/health/ready`;
    let ok = false;
    try {
      const res = await httpGetJson(url, 4000);
      this.lastHealthCheckAt = Date.now();
      if (res.statusCode === 200) {
        ok = true;
        this.healthDetail = res.body;
        this.lastReadyAt = Date.now();
      } else {
        this.healthDetail = { status: `http_${res.statusCode}`, body: res.body };
      }
    } catch (err) {
      this.lastHealthCheckAt = Date.now();
      this.healthDetail = { status: 'unreachable', error: err.message };
    }

    if (ok) {
      this.consecutiveFailures = 0;
      if (this.state === STATES.STARTING) {
        if (this._backoff) this._backoff.reset();
        this._setState(STATES.RUNNING);
        this.logStore.system(`gateway is READY at http://${this.host}:${this.port}/ (pid ${this.pid})`);
      } else if (this.state === STATES.DEGRADED) {
        this._setState(STATES.RUNNING);
        this.logStore.system('gateway recovered — readiness restored');
      }
    } else {
      this.consecutiveFailures += 1;
      if (this.state === STATES.STARTING) {
        const elapsed = Date.now() - (this.startedAt || Date.now());
        if (elapsed > cfg.gateway.readyTimeoutMs) {
          this.logStore.system(`readiness timeout after ${Math.round(elapsed / 1000)}s — terminating the process so the restart policy can take over`);
          this.lastError = `gateway did not become ready within ${Math.round(elapsed / 1000)}s`;
          const child = this.child;
          if (child) this._killTree(child).catch(() => {});
          return; // exit event drives the state machine
        }
      } else if (this.state === STATES.RUNNING && this.consecutiveFailures >= cfg.health.failureThreshold) {
        this._setState(STATES.DEGRADED, {
          lastError: `readiness check failed ${this.consecutiveFailures} times in a row`,
        });
      }
    }
    this._scheduleHealthTick(false);
  }

  /**
   * Kill the child's entire process tree.
   * Windows: the venv python.exe launcher spawns a child interpreter that
   * owns the listening socket — killing only the direct child would orphan
   * the server, so we use taskkill /T (tree).
   */
  _killTree(child) {
    return new Promise((resolve) => {
      if (!child || child.exitCode !== null || child.killed) {
        resolve();
        return;
      }
      const pid = child.pid;
      let done = false;
      const finish = () => {
        if (!done) {
          done = true;
          resolve();
        }
      };
      child.once('exit', finish);
      const watchdog = setTimeout(() => {
        try { child.kill('SIGKILL'); } catch { /* already gone */ }
        setTimeout(finish, 1000).unref();
      }, 8000);
      watchdog.unref();

      if (this.platform === 'win32' && pid) {
        execFile('taskkill', ['/pid', String(pid), '/T', '/F'], { windowsHide: true }, (err) => {
          if (err) {
            this.logStore.system(`taskkill failed (${err.message}); falling back to direct kill`);
            try { child.kill(); } catch { /* already gone */ }
          }
        });
      } else {
        try { child.kill('SIGTERM'); } catch { /* already gone */ }
        setTimeout(() => {
          if (!done) {
            try { child.kill('SIGKILL'); } catch { /* already gone */ }
          }
        }, 5000).unref();
      }
      setTimeout(finish, 9000).unref();
    });
  }

  /** Diagnostics for the UI "auto-detect" button. */
  async probeEnvironment() {
    const cfg = this.configStore.get();
    const repo = await this.resolveRepo({
      configuredRepoPath: cfg.gateway.repoPath,
      appPath: this._appPath || '',
      resourcesPath: this._resourcesPath || '',
    });
    const python = await this.resolvePython({
      configuredPythonPath: cfg.gateway.pythonPath,
      repoPath: repo.path || cfg.gateway.repoPath,
    });
    return { repo, python };
  }

  /** Provided by main.js from Electron's app object. */
  setElectronPaths({ appPath, resourcesPath }) {
    this._appPath = appPath;
    this._resourcesPath = resourcesPath;
  }
}

module.exports = { GatewayManager, STATES, defaultBuildCommand, httpGetJson };

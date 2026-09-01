'use strict';

/**
 * config-store.js — persistent desktop-app configuration.
 *
 * Pure Node module (node:fs/node:path only, no Electron): unit-testable with
 * plain `node --test`. The Electron main process passes
 * `path.join(app.getPath('userData'), 'desktop-config.json')` as the file.
 *
 * Guarantees:
 *   - Atomic writes (tmp file + rename) so a crash never corrupts config.
 *   - Corrupt/partial files are quarantined (`*.corrupt-<ts>`) and defaults
 *     are returned instead of throwing.
 *   - Unknown keys are preserved on save (forward compatibility) but the
 *     validated shape is always what callers see.
 */

const fs = require('node:fs');
const path = require('node:path');

const DEFAULT_CONFIG = Object.freeze({
  gateway: Object.freeze({
    // Absolute path to python.exe. Empty string = auto-detect
    // (../venv/Scripts/python.exe next to the gateway repo, then PATH).
    pythonPath: '',
    // Absolute path to the MOA Gateway repo (the directory containing
    // moa_gateway/ and config.yaml). Empty string = auto-detect.
    repoPath: '',
    // Bind host for the managed gateway. Loopback by default: the desktop
    // client is a single-user local tool; exposing the gateway is the user's
    // explicit choice.
    host: '127.0.0.1',
    // Preferred port (settings.server.port default in moa_gateway is 8910).
    // If occupied, the manager scans upward for a free port automatically.
    port: 8910,
    // Start the gateway automatically when the desktop app launches.
    autoStart: true,
    // How long to wait for /health/ready before declaring a start failed.
    readyTimeoutMs: 120000,
  }),
  health: Object.freeze({
    // Polling interval for /health/ready once running.
    pollIntervalMs: 5000,
    // Polling interval while waiting for the service to become ready.
    startingPollIntervalMs: 1500,
    // Consecutive failed readiness checks before the badge flips to degraded.
    failureThreshold: 3,
  }),
  restart: Object.freeze({
    // Automatic crash-restart backoff (see backoff.js).
    enabled: true,
    baseMs: 1000,
    factor: 2,
    maxMs: 30000,
    maxAttempts: 8,
  }),
  logs: Object.freeze({
    maxLines: 5000,
    maxBytes: 2 * 1024 * 1024,
  }),
  ui: Object.freeze({
    minimizeToTray: true,
    theme: 'dark',
  }),
  system: Object.freeze({
    openAtLogin: false,
  }),
});

function isPlainObject(v) {
  return v !== null && typeof v === 'object' && !Array.isArray(v);
}

/** Deep-merge `patch` over `base` (objects only; scalars/arrays replace). */
function deepMerge(base, patch) {
  const out = { ...base };
  if (!isPlainObject(patch)) return out;
  for (const [key, value] of Object.entries(patch)) {
    if (isPlainObject(base[key]) && isPlainObject(value)) {
      out[key] = deepMerge(base[key], value);
    } else {
      out[key] = value;
    }
  }
  return out;
}

/**
 * Validate + normalize a raw config object.
 * Returns { config, errors } where `config` is always usable (invalid fields
 * fall back to defaults) and `errors` lists human-readable field problems.
 */
function validateConfig(raw) {
  const errors = [];
  const merged = deepMerge(structuredClone(DEFAULT_CONFIG), isPlainObject(raw) ? raw : {});

  const g = merged.gateway;
  if (typeof g.pythonPath !== 'string') {
    errors.push('gateway.pythonPath must be a string');
    g.pythonPath = DEFAULT_CONFIG.gateway.pythonPath;
  }
  if (typeof g.repoPath !== 'string') {
    errors.push('gateway.repoPath must be a string');
    g.repoPath = DEFAULT_CONFIG.gateway.repoPath;
  }
  if (typeof g.host !== 'string' || g.host.trim() === '') {
    errors.push('gateway.host must be a non-empty string');
    g.host = DEFAULT_CONFIG.gateway.host;
  }
  if (!Number.isInteger(g.port) || g.port < 1 || g.port > 65535) {
    errors.push(`gateway.port must be an integer in [1, 65535], got ${JSON.stringify(g.port)}`);
    g.port = DEFAULT_CONFIG.gateway.port;
  }
  if (typeof g.autoStart !== 'boolean') {
    errors.push('gateway.autoStart must be a boolean');
    g.autoStart = DEFAULT_CONFIG.gateway.autoStart;
  }
  if (!Number.isFinite(g.readyTimeoutMs) || g.readyTimeoutMs < 5000) {
    errors.push('gateway.readyTimeoutMs must be a number >= 5000');
    g.readyTimeoutMs = DEFAULT_CONFIG.gateway.readyTimeoutMs;
  }

  const h = merged.health;
  for (const key of ['pollIntervalMs', 'startingPollIntervalMs']) {
    if (!Number.isFinite(h[key]) || h[key] < 250) {
      errors.push(`health.${key} must be a number >= 250`);
      h[key] = DEFAULT_CONFIG.health[key];
    }
  }
  if (!Number.isInteger(h.failureThreshold) || h.failureThreshold < 1) {
    errors.push('health.failureThreshold must be a positive integer');
    h.failureThreshold = DEFAULT_CONFIG.health.failureThreshold;
  }

  const r = merged.restart;
  if (typeof r.enabled !== 'boolean') {
    errors.push('restart.enabled must be a boolean');
    r.enabled = DEFAULT_CONFIG.restart.enabled;
  }
  if (!Number.isFinite(r.baseMs) || r.baseMs <= 0) {
    errors.push('restart.baseMs must be > 0');
    r.baseMs = DEFAULT_CONFIG.restart.baseMs;
  }
  if (!Number.isFinite(r.factor) || r.factor < 1) {
    errors.push('restart.factor must be >= 1');
    r.factor = DEFAULT_CONFIG.restart.factor;
  }
  if (!Number.isFinite(r.maxMs) || r.maxMs < r.baseMs) {
    errors.push('restart.maxMs must be >= restart.baseMs');
    r.maxMs = DEFAULT_CONFIG.restart.maxMs;
  }
  if (!Number.isInteger(r.maxAttempts) || r.maxAttempts < 1) {
    errors.push('restart.maxAttempts must be a positive integer');
    r.maxAttempts = DEFAULT_CONFIG.restart.maxAttempts;
  }

  const l = merged.logs;
  if (!Number.isInteger(l.maxLines) || l.maxLines < 100) {
    errors.push('logs.maxLines must be an integer >= 100');
    l.maxLines = DEFAULT_CONFIG.logs.maxLines;
  }
  if (!Number.isFinite(l.maxBytes) || l.maxBytes < 65536) {
    errors.push('logs.maxBytes must be >= 65536');
    l.maxBytes = DEFAULT_CONFIG.logs.maxBytes;
  }

  const u = merged.ui;
  if (typeof u.minimizeToTray !== 'boolean') {
    errors.push('ui.minimizeToTray must be a boolean');
    u.minimizeToTray = DEFAULT_CONFIG.ui.minimizeToTray;
  }
  if (u.theme !== 'dark' && u.theme !== 'light') {
    errors.push(`ui.theme must be 'dark' or 'light', got ${JSON.stringify(u.theme)}`);
    u.theme = DEFAULT_CONFIG.ui.theme;
  }

  if (typeof merged.system.openAtLogin !== 'boolean') {
    errors.push('system.openAtLogin must be a boolean');
    merged.system.openAtLogin = DEFAULT_CONFIG.system.openAtLogin;
  }

  return { config: merged, errors };
}

class ConfigStore {
  /**
   * @param {string} filePath absolute path of the JSON config file
   */
  constructor(filePath) {
    if (typeof filePath !== 'string' || filePath.trim() === '') {
      throw new TypeError('ConfigStore requires a file path');
    }
    this.filePath = filePath;
    this._state = structuredClone(DEFAULT_CONFIG);
    this._loadErrors = [];
    this._loaded = false;
  }

  /** Warnings collected during the last load() (corruption, invalid fields). */
  get loadWarnings() {
    return this._loadErrors.slice();
  }

  /** Read config from disk (or return defaults when missing/corrupt). */
  load() {
    this._loadErrors = [];
    let rawText = null;
    try {
      rawText = fs.readFileSync(this.filePath, 'utf8');
    } catch (err) {
      if (err.code !== 'ENOENT') this._loadErrors.push(`read failed: ${err.message}`);
      this._state = structuredClone(DEFAULT_CONFIG);
      this._loaded = true;
      return this.get();
    }

    let parsed;
    try {
      parsed = JSON.parse(rawText);
    } catch (err) {
      // Quarantine the corrupt file so the user can inspect it, then fall
      // back to defaults instead of failing startup.
      const quarantine = `${this.filePath}.corrupt-${Date.now()}`;
      try {
        fs.renameSync(this.filePath, quarantine);
        this._loadErrors.push(`config was corrupt (${err.message}); moved to ${path.basename(quarantine)}`);
      } catch {
        this._loadErrors.push(`config was corrupt (${err.message}); using defaults`);
      }
      this._state = structuredClone(DEFAULT_CONFIG);
      this._loaded = true;
      return this.get();
    }

    const { config, errors } = validateConfig(parsed);
    this._loadErrors.push(...errors);
    this._state = config;
    this._loaded = true;
    return this.get();
  }

  /** Current config snapshot (deep copy). */
  get() {
    if (!this._loaded) this.load();
    return structuredClone(this._state);
  }

  /**
   * Apply a partial patch, validate, persist atomically, return the new full
   * config. Throws when persistence fails (caller surfaces it to the UI).
   * @param {object} patch
   * @returns {{config: object, errors: string[]}}
   */
  save(patch) {
    const candidate = deepMerge(this._loaded ? this._state : structuredClone(DEFAULT_CONFIG), patch);
    const { config, errors } = validateConfig(candidate);
    if (errors.length > 0) {
      // Reject invalid patches wholesale: never half-write bad config.
      throw new Error(`invalid config: ${errors.join('; ')}`);
    }
    const dir = path.dirname(this.filePath);
    fs.mkdirSync(dir, { recursive: true });
    const tmp = `${this.filePath}.tmp-${process.pid}-${Date.now()}`;
    fs.writeFileSync(tmp, JSON.stringify(config, null, 2), 'utf8');
    fs.renameSync(tmp, this.filePath);
    this._state = config;
    this._loaded = true;
    return { config: this.get(), errors: [] };
  }
}

module.exports = { ConfigStore, validateConfig, deepMerge, DEFAULT_CONFIG };

'use strict';

/**
 * preload.js — the ONLY bridge between the renderer and the main process.
 *
 * contextIsolation is ON and nodeIntegration is OFF: the renderer can never
 * touch Node or Electron internals directly. Everything goes through the
 * versioned, allow-listed API exposed as `window.moaGateway` below.
 *
 * Security notes:
 *   - secrets: plaintext values travel renderer→main exactly once (on save);
 *     main never returns plaintext, only names + timestamps.
 *   - event subscriptions return real unsubscribe functions and validate
 *     callbacks, so a compromised renderer cannot hook arbitrary channels.
 */

const { contextBridge, ipcRenderer } = require('electron');

const INVOKE_CHANNELS = new Set([
  'service:status', 'service:start', 'service:stop', 'service:restart',
  'logs:get', 'logs:clear', 'logs:export',
  'config:get', 'config:set',
  'secrets:available', 'secrets:list', 'secrets:set', 'secrets:delete',
  'autostart:get', 'autostart:set',
  'env:probe', 'app:info', 'app:open-external',
]);

const EVENT_CHANNELS = new Set([
  'service:status-changed',
  'logs:entries',
  'logs:cleared',
]);

function invoke(channel, payload) {
  if (!INVOKE_CHANNELS.has(channel)) {
    return Promise.reject(new Error(`blocked IPC channel: ${channel}`));
  }
  return ipcRenderer.invoke(channel, payload);
}

function subscribe(channel, callback) {
  if (!EVENT_CHANNELS.has(channel)) {
    throw new Error(`blocked IPC event channel: ${channel}`);
  }
  if (typeof callback !== 'function') {
    throw new TypeError('listener must be a function');
  }
  const listener = (_event, payload) => {
    try {
      callback(payload);
    } catch (err) {
      console.error(`[moaGateway] listener error on ${channel}:`, err);
    }
  };
  ipcRenderer.on(channel, listener);
  return () => ipcRenderer.removeListener(channel, listener);
}

const api = {
  /** Service lifecycle + status. */
  service: {
    status: () => invoke('service:status'),
    start: () => invoke('service:start'),
    stop: () => invoke('service:stop'),
    restart: () => invoke('service:restart'),
    /** @param {(status: object) => void} cb @returns {() => void} unsubscribe */
    onStatus: (cb) => subscribe('service:status-changed', cb),
  },

  /** Captured gateway logs. */
  logs: {
    get: (limit) => invoke('logs:get', { limit }),
    clear: () => invoke('logs:clear'),
    export: () => invoke('logs:export'),
    /** @param {(batch: {entries: Array}) => void} cb */
    onEntries: (cb) => subscribe('logs:entries', cb),
    onCleared: (cb) => subscribe('logs:cleared', cb),
  },

  /** Desktop configuration (persisted, validated). */
  config: {
    get: () => invoke('config:get'),
    set: (patch) => invoke('config:set', patch),
  },

  /**
   * API keys, encrypted with OS-level DPAPI (safeStorage) in main.
   * Plaintext is never read back — list() returns names/timestamps only.
   */
  secrets: {
    available: () => invoke('secrets:available'),
    list: () => invoke('secrets:list'),
    set: (name, value) => invoke('secrets:set', { name, value }),
    remove: (name) => invoke('secrets:delete', { name }),
  },

  /** OS login-item (open at login). */
  autostart: {
    get: () => invoke('autostart:get'),
    set: (openAtLogin) => invoke('autostart:set', { openAtLogin }),
  },

  /** Environment diagnostics (repo/python discovery). */
  env: {
    probe: () => invoke('env:probe'),
  },

  /** App metadata + controlled external links. */
  app: {
    info: () => invoke('app:info'),
    openExternal: (url) => invoke('app:open-external', { url }),
  },
};

contextBridge.exposeInMainWorld('moaGateway', api);

'use strict';

/**
 * ipc.js — all ipcMain handler registration for the desktop client.
 *
 * Channel surface (renderer → main, all via ipcRenderer.invoke):
 *   service:status | service:start | service:stop | service:restart
 *   logs:get | logs:clear | logs:export
 *   config:get | config:set
 *   secrets:available | secrets:list | secrets:set | secrets:delete
 *   autostart:get | autostart:set
 *   env:probe | app:info | app:open-external
 *
 * Push channels (main → renderer): service:status-changed, logs:entries,
 * logs:cleared — wired in main.js with batching.
 */

const { ipcMain, dialog, shell, app } = require('electron');

/**
 * @param {object} deps
 * @param {object} deps.manager GatewayManager
 * @param {object} deps.configStore ConfigStore
 * @param {object} deps.secretStore SecretStore
 * @param {object} deps.logStore LogStore
 * @param {() => Electron.BrowserWindow|null} deps.getWindow
 */
function registerIpc({ manager, configStore, secretStore, logStore, getWindow }) {
  // ------------------------------------------------------------- service
  ipcMain.handle('service:status', () => manager.getStatus());
  ipcMain.handle('service:start', () => manager.start());
  ipcMain.handle('service:stop', () => manager.stop());
  ipcMain.handle('service:restart', () => manager.restart());

  // ---------------------------------------------------------------- logs
  ipcMain.handle('logs:get', (_event, opts = {}) => {
    const limit = Number.isInteger(opts.limit) && opts.limit > 0 ? Math.min(opts.limit, 20000) : 1000;
    return {
      entries: logStore.snapshot(limit),
      total: logStore.length,
      dropped: logStore.dropped,
    };
  });
  ipcMain.handle('logs:clear', () => {
    logStore.clear();
    return { ok: true };
  });
  ipcMain.handle('logs:export', async () => {
    const win = getWindow();
    const stamp = new Date().toISOString().replace(/[:.]/g, '-');
    const { canceled, filePath } = await dialog.showSaveDialog(win, {
      title: 'Export gateway log',
      defaultPath: `moa-gateway-log-${stamp}.txt`,
      filters: [{ name: 'Text files', extensions: ['txt', 'log'] }],
    });
    if (canceled || !filePath) return { canceled: true };
    const written = logStore.exportTo(filePath);
    return { canceled: false, filePath: written };
  });

  // -------------------------------------------------------------- config
  ipcMain.handle('config:get', () => ({
    config: configStore.get(),
    loadWarnings: configStore.loadWarnings,
  }));
  ipcMain.handle('config:set', (_event, patch) => {
    const { config } = configStore.save(patch || {});
    const status = manager.getStatus();
    const active = ['starting', 'running', 'degraded', 'backoff'].includes(status.state);
    const needsRestart =
      active &&
      Boolean(
        (patch && patch.gateway && (
          patch.gateway.port !== undefined ||
          patch.gateway.host !== undefined ||
          patch.gateway.pythonPath !== undefined ||
          patch.gateway.repoPath !== undefined
        )),
      );
    return {
      config,
      needsRestart,
      message: needsRestart
        ? 'Settings saved. Restart the service to apply host/port/Python changes.'
        : 'Settings saved.',
    };
  });

  // ------------------------------------------------------------- secrets
  ipcMain.handle('secrets:available', () => secretStore.isAvailable());
  ipcMain.handle('secrets:list', () => secretStore.list());
  ipcMain.handle('secrets:set', (_event, payload = {}) => {
    secretStore.set(String(payload.name || ''), String(payload.value || ''));
    return { ok: true, entries: secretStore.list() };
  });
  ipcMain.handle('secrets:delete', (_event, payload = {}) => {
    const removed = secretStore.delete(String(payload.name || ''));
    return { ok: removed, entries: secretStore.list() };
  });

  // ------------------------------------------------------------ autostart
  ipcMain.handle('autostart:get', () => {
    let openAtLogin = false;
    try {
      openAtLogin = app.getLoginItemSettings().openAtLogin;
    } catch { /* not supported on this platform */ }
    return { openAtLogin };
  });
  ipcMain.handle('autostart:set', (_event, payload = {}) => {
    const openAtLogin = Boolean(payload.openAtLogin);
    app.setLoginItemSettings({ openAtLogin });
    // Persist the intent too, so a fresh profile can re-apply it.
    try {
      configStore.save({ system: { openAtLogin } });
    } catch { /* config persistence is best-effort here */ }
    let effective = openAtLogin;
    try {
      effective = app.getLoginItemSettings().openAtLogin;
    } catch { /* ignore */ }
    return { openAtLogin: effective };
  });

  // ------------------------------------------------------------- misc
  ipcMain.handle('env:probe', () => manager.probeEnvironment());
  ipcMain.handle('app:info', () => ({
    appVersion: app.getVersion(),
    electron: process.versions.electron,
    node: process.versions.node,
    chrome: process.versions.chrome,
    platform: process.platform,
    arch: process.arch,
    userData: app.getPath('userData'),
  }));
  ipcMain.handle('app:open-external', (_event, payload = {}) => {
    const url = String(payload.url || '');
    let parsed;
    try {
      parsed = new URL(url);
    } catch {
      throw new Error(`refusing to open invalid URL: ${url}`);
    }
    const loopback = parsed.hostname === '127.0.0.1' || parsed.hostname === 'localhost';
    if ((parsed.protocol !== 'http:' && parsed.protocol !== 'https:') || !loopback) {
      throw new Error(`refusing to open non-loopback URL: ${url}`);
    }
    return shell.openExternal(url);
  });
}

module.exports = { registerIpc };

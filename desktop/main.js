'use strict';

/**
 * main.js — Electron main-process entry for MOA Gateway Desktop.
 *
 * Wires together:
 *   - ConfigStore / LogStore / SecretStore (userData persistence)
 *   - GatewayManager (Python child lifecycle + health + restart)
 *   - StaticServer (serves the renderer shell over loopback http so the
 *     gateway webui iframe is same-scheme; see static-server.js)
 *   - IPC handlers, tray, single-instance lock, login-item (auto-start)
 *
 * Run modes:
 *   electron .                 normal GUI
 *   electron . --smoke-test    headless self-test (no window), exit 0/1
 */

const { app, BrowserWindow, safeStorage, Menu } = require('electron');
const path = require('node:path');

const { ConfigStore } = require('./src/main/config-store');
const { LogStore } = require('./src/main/log-store');
const { SecretStore } = require('./src/main/secret-store');
const { GatewayManager } = require('./src/main/gateway-manager');
const { StaticServer } = require('./src/main/static-server');
const { registerIpc } = require('./src/main/ipc');
const { createTray, resolveIconPath } = require('./src/main/tray');
const { runSmoke } = require('./src/main/smoke');

const SMOKE_MODE = process.argv.includes('--smoke-test');

// ---------------------------------------------------------------------------
// Global state (populated in whenReady)
// ---------------------------------------------------------------------------
let mainWindow = null;
let tray = null;
let configStore = null;
let logStore = null;
let secretStore = null;
let manager = null;
let staticServer = null;
let isQuitting = false;

function userDataFile(name) {
  return path.join(app.getPath('userData'), name);
}

// ---------------------------------------------------------------------------
// Single-instance lock: a second launch focuses the existing window instead of
// starting a duplicate service manager.
// ---------------------------------------------------------------------------
if (!SMOKE_MODE) {
  const gotLock = app.requestSingleInstanceLock();
  if (!gotLock) {
    app.quit();
  } else {
    app.on('second-instance', () => {
      if (mainWindow) {
        if (mainWindow.isMinimized()) mainWindow.restore();
        mainWindow.show();
        mainWindow.focus();
      }
    });
  }
}

// ---------------------------------------------------------------------------
// Broadcast helpers (main -> renderer)
// ---------------------------------------------------------------------------
function broadcast(channel, payload) {
  for (const win of BrowserWindow.getAllWindows()) {
    if (!win.isDestroyed()) {
      try { win.webContents.send(channel, payload); } catch { /* window closing */ }
    }
  }
}

let logBatch = [];
let logBatchTimer = null;
function flushLogBatch() {
  logBatchTimer = null;
  if (logBatch.length === 0) return;
  const entries = logBatch;
  logBatch = [];
  broadcast('logs:entries', { entries });
}
function queueLogEntry(entry) {
  logBatch.push(entry);
  if (logBatch.length >= 250) {
    if (logBatchTimer) { clearTimeout(logBatchTimer); logBatchTimer = null; }
    flushLogBatch();
  } else if (!logBatchTimer) {
    logBatchTimer = setTimeout(flushLogBatch, 200);
    logBatchTimer.unref();
  }
}

// ---------------------------------------------------------------------------
// Construction
// ---------------------------------------------------------------------------
function buildStores() {
  configStore = new ConfigStore(userDataFile('desktop-config.json'));
  configStore.load();
  for (const warning of configStore.loadWarnings) {
    console.warn(`[config] ${warning}`);
  }

  const cfg = configStore.get();
  logStore = new LogStore({ maxLines: cfg.logs.maxLines, maxBytes: cfg.logs.maxBytes });
  secretStore = new SecretStore({ filePath: userDataFile('secrets.enc.json'), safeStorage });

  manager = new GatewayManager({ configStore, logStore, secretStore });
  manager.setElectronPaths({ appPath: app.getAppPath(), resourcesPath: process.resourcesPath || '' });

  manager.on('status', (status) => broadcast('service:status-changed', status));
  logStore.on('entry', queueLogEntry);
  logStore.on('cleared', () => broadcast('logs:cleared', {}));
}

function createMainWindow() {
  const cfg = configStore.get();
  mainWindow = new BrowserWindow({
    width: 1280,
    height: 840,
    minWidth: 940,
    minHeight: 600,
    show: false,
    title: 'MOA Gateway Desktop',
    backgroundColor: '#0b0f17',
    autoHideMenuBar: true,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      spellcheck: false,
    },
  });

  mainWindow.removeMenu();
  Menu.setApplicationMenu(null);

  const shellUrl = staticServer ? staticServer.url : null;
  if (shellUrl) {
    mainWindow.loadURL(shellUrl);
  } else {
    // Explicit, visible degradation: static server failed to bind.
    mainWindow.loadURL(`data:text/html;charset=utf-8,${encodeURIComponent(
      '<body style="font-family:sans-serif;background:#0b0f17;color:#e6edf3;padding:2rem">' +
      '<h2>MOA Gateway Desktop</h2><p>The local UI server failed to start. Check the console output.</p></body>',
    )}`);
  }

  mainWindow.once('ready-to-show', () => mainWindow.show());

  // Minimize-to-tray: closing hides instead of quitting unless we are really
  // shutting down. This keeps the managed gateway running.
  mainWindow.on('close', (event) => {
    const current = configStore.get();
    if (!isQuitting && current.ui.minimizeToTray) {
      event.preventDefault();
      mainWindow.hide();
    }
  });

  mainWindow.on('minimize', (event) => {
    const current = configStore.get();
    if (current.ui.minimizeToTray) {
      event.preventDefault();
      mainWindow.hide();
    }
  });

  mainWindow.on('closed', () => { mainWindow = null; });
  return mainWindow;
}

function showMainWindow() {
  if (!mainWindow || mainWindow.isDestroyed()) {
    createMainWindow();
  } else {
    if (mainWindow.isMinimized()) mainWindow.restore();
    mainWindow.show();
  }
  mainWindow.focus();
}

function buildTray() {
  const iconPath = resolveIconPath(__dirname);
  tray = createTray({
    getWindow: () => mainWindow,
    manager,
    onShowWindow: showMainWindow,
    onQuit: () => quitApp(),
    iconPath,
  });
}

async function quitApp() {
  if (isQuitting) return;
  isQuitting = true;
  try { if (manager) await manager.dispose(); } catch { /* shutdown best-effort */ }
  try { if (staticServer) await staticServer.close(); } catch { /* ignore */ }
  try { if (tray) tray.destroy(); } catch { /* ignore */ }
  app.quit();
}

// ---------------------------------------------------------------------------
// App lifecycle
// ---------------------------------------------------------------------------
app.whenReady().then(async () => {
  app.setAppUserModelId('com.moa.gateway.desktop');

  // Headless smoke test: no window, no tray, just exercise the subsystems.
  if (SMOKE_MODE) {
    let code = 1;
    try {
      code = await runSmoke({ app, safeStorage });
    } catch (err) {
      console.error(`[SMOKE] FAIL uncaught — ${err.stack || err}`);
      code = 1;
    }
    app.exit(code);
    return;
  }

  buildStores();

  // Serve the renderer shell over loopback http (see static-server.js).
  staticServer = new StaticServer({ rootDir: path.join(__dirname, 'src', 'renderer') });
  try {
    await staticServer.listen(0);
    console.log(`[ui] renderer shell on ${staticServer.url}`);
  } catch (err) {
    console.error(`[ui] static server failed to bind: ${err.message}`);
    staticServer = null;
  }

  registerIpc({
    manager,
    configStore,
    secretStore,
    logStore,
    getWindow: () => mainWindow,
  });

  createMainWindow();
  buildTray();

  // Re-apply the persisted login-item intent on platforms where the OS may
  // have cleared it (e.g. after an app update changed the path).
  const cfg = configStore.get();
  if (cfg.system.openAtLogin) {
    try { app.setLoginItemSettings({ openAtLogin: true }); } catch { /* ignore */ }
  }

  // Auto-start the gateway if configured.
  if (cfg.gateway.autoStart) {
    logStore.system('auto-start enabled — launching gateway');
    manager.start().catch((err) => logStore.system(`auto-start failed: ${err.message}`));
  } else {
    logStore.system('auto-start disabled — gateway will start on demand');
  }

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createMainWindow();
    else showMainWindow();
  });
});

app.on('before-quit', () => { isQuitting = true; });

app.on('window-all-closed', () => {
  // On Windows we keep running in the tray so the managed gateway stays up.
  // If the tray is gone (destroyed) there is nothing left to do.
  if (process.platform !== 'win32') {
    quitApp();
  }
});

app.on('quit', async () => {
  try { if (manager) await manager.dispose(); } catch { /* ignore */ }
});

// Never let an unhandled rejection take down the service manager silently.
process.on('unhandledRejection', (reason) => {
  console.error('[unhandledRejection]', reason);
  try { if (logStore) logStore.system(`unhandled rejection: ${reason && reason.message ? reason.message : reason}`); } catch { /* ignore */ }
});
process.on('uncaughtException', (err) => {
  console.error('[uncaughtException]', err);
  try { if (logStore) logStore.system(`uncaught exception: ${err.message}`); } catch { /* ignore */ }
});

/* MoA Gateway Pro — Windows desktop entry (Electron).
 *
 * Responsibilities (real, not stubbed):
 *   1. Locate the bundled Python gateway (extraResources/gateway) and a Python
 *      interpreter, then spawn `uvicorn moa_gateway.server:app` as a child process.
 *   2. Poll the gateway /health endpoint until ready (or surface a real error).
 *   3. Open a BrowserWindow pointing at the gateway's web UI (admin console),
 *      which includes the 智能编排 (orchestration) panel.
 *
 * NOTE: building the distributable .exe (electron-builder) must be done on a
 * machine with Windows + Node; it is not produced in the audit sandbox.
 */
'use strict';

const { app, BrowserWindow, shell, dialog } = require('electron');
const { spawn } = require('child_process');
const path = require('path');
const http = require('http');

const PORT = process.env.MOA_PORT || 8910;
const HOST = '127.0.0.1';
const BASE_URL = `http://${HOST}:${PORT}`;

let mainWindow = null;
let gatewayProc = null;

function gatewayDir() {
  // Packaged: resources/gateway ; dev: repo root (parent of desktop/)
  return app.isPackaged
    ? path.join(process.resourcesPath, 'gateway')
    : path.resolve(__dirname, '..');
}

function findPython() {
  // Prefer a project venv, then common interpreter names.
  const candidates = process.platform === 'win32'
    ? ['python.exe', 'python3.exe', 'py.exe']
    : ['python3', 'python'];
  return candidates;
}

function startGateway() {
  const cwd = gatewayDir();
  const args = ['-m', 'uvicorn', 'moa_gateway.server:app', '--host', HOST, '--port', String(PORT)];
  const [py, ...fallbacks] = findPython();

  const env = { ...process.env };
  // Pass through required secrets if provided; otherwise the gateway auto-generates
  // an admin password into data/.admin_password (documented behavior).
  ['MOA_ADMIN_PASSWORD', 'MOA_GATEWAY_KEY', 'MOA_JWT_SECRET'].forEach((k) => {
    if (process.env[k]) env[k] = process.env[k];
  });

  gatewayProc = spawn(py, args, { cwd, env });
  gatewayProc.stdout.on('data', (d) => process.stdout.write(`[gateway] ${d}`));
  gatewayProc.stderr.on('data', (d) => process.stderr.write(`[gateway] ${d}`));
  gatewayProc.on('error', (err) => {
    dialog.showErrorBox('网关启动失败', `无法启动 Python 网关: ${err.message}\n请确认已安装 Python 与依赖 (pip install -r requirements.txt)。`);
  });
  gatewayProc.on('exit', (code) => {
    process.stderr.write(`[gateway] exited with code ${code}\n`);
  });
}

function waitForGateway(url, timeoutMs = 30000) {
  const start = Date.now();
  return new Promise((resolve, reject) => {
    const tryOnce = () => {
      const req = http.get(`${url}/health`, (res) => {
        resolve(true);
      });
      req.on('error', () => {
        if (Date.now() - start > timeoutMs) {
          reject(new Error('网关在超时时间内未就绪'));
        } else {
          setTimeout(tryOnce, 500);
        }
      });
      req.setTimeout(2000, () => { req.destroy(); });
    };
    tryOnce();
  });
}

async function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1280,
    height: 860,
    title: 'MoA Gateway Pro',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });

  // Open external links in the system browser, not inside the app.
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    if (url.startsWith(BASE_URL)) return { action: 'allow' };
    if (!/^https?:\/\//i.test(url)) return { action: 'deny' };  // file:, custom schemes blocked
    shell.openExternal(url);
    return { action: 'deny' };
  });

  mainWindow.loadURL(BASE_URL);
  mainWindow.on('closed', () => { mainWindow = null; });
}

app.whenReady().then(async () => {
  startGateway();
  try {
    await waitForGateway(BASE_URL);
  } catch (e) {
    dialog.showErrorBox('网关未就绪', `${e.message}\n将仍尝试打开界面。`);
  }
  await createWindow();
  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on('window-all-closed', () => {
  if (gatewayProc) {
    try { gatewayProc.kill(); } catch (_) { /* ignore */ }
  }
  if (process.platform !== 'darwin') app.quit();
});

app.on('before-quit', () => {
  if (gatewayProc) {
    try { gatewayProc.kill(); } catch (_) { /* ignore */ }
  }
});

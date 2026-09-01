'use strict';

/**
 * tray.js — system tray icon + menu.
 *
 * Tray menu: show console / start service / stop service / open in browser /
 * quit. Clicking the icon restores the main window. This module is
 * Electron-specific and is wired up in main.js; it holds no state of its own
 * beyond the Tray instance.
 */

const { Tray, Menu, nativeImage } = require('electron');
const path = require('node:path');

/**
 * @param {object} deps
 * @param {Electron.BrowserWindow} deps.getWindow returns the main window (may be null)
 * @param {object} deps.manager GatewayManager
 * @param {() => void} deps.onQuit hard-quit callback
 * @param {() => void} deps.onShowWindow restore + focus the main window
 * @param {string} [deps.iconPath] absolute path to a PNG/ICO tray icon
 * @returns {Electron.Tray}
 */
function createTray({ getWindow, manager, onQuit, onShowWindow, iconPath }) {
  let image;
  if (iconPath) {
    image = nativeImage.createFromPath(iconPath);
  }
  if (!image || image.isEmpty()) {
    // Fallback: a tiny programmatically drawn 16x16 dot so the tray entry
    // still exists even if assets are missing (explicitly degraded, visible).
    image = nativeImage.createEmpty();
  } else {
    image = image.resize({ width: 16, height: 16 });
  }

  const tray = new Tray(image);
  tray.setToolTip('MOA Gateway Desktop');

  const rebuildMenu = () => {
    const status = manager.getStatus();
    const running = status.state === 'running' || status.state === 'starting' || status.state === 'degraded';
    const menu = Menu.buildFromTemplate([
      { label: 'Show MOA Gateway Desktop', click: () => onShowWindow() },
      { type: 'separator' },
      {
        label: status.state === 'running' && status.url ? `Console: ${status.url}` : 'Console: unavailable',
        enabled: status.state === 'running' && Boolean(status.url),
        click: () => onShowWindow(),
      },
      { type: 'separator' },
      {
        label: 'Start service',
        enabled: !running && status.state !== 'stopping',
        click: () => { manager.start().catch(() => {}); },
      },
      {
        label: 'Stop service',
        enabled: running,
        click: () => { manager.stop().catch(() => {}); },
      },
      { type: 'separator' },
      { label: 'Quit', click: () => onQuit() },
    ]);
    tray.setContextMenu(menu);
    tray.setToolTip(`MOA Gateway Desktop — service: ${status.state}${status.port ? ` (port ${status.port})` : ''}`);
  };

  manager.on('status', () => {
    try { rebuildMenu(); } catch { /* tray may be destroyed during quit */ }
  });
  tray.on('double-click', () => onShowWindow());
  tray.on('click', () => {
    const win = getWindow();
    if (win && !win.isVisible()) onShowWindow();
  });

  rebuildMenu();
  return tray;
}

/** Resolve the tray/window icon shipped in assets/. */
function resolveIconPath(appRoot) {
  const candidates = [
    path.join(appRoot, 'assets', 'tray.png'),
    path.join(appRoot, 'assets', 'icon.png'),
  ];
  const fs = require('node:fs');
  for (const c of candidates) {
    try {
      if (fs.existsSync(c)) return c;
    } catch { /* keep looking */ }
  }
  return null;
}

module.exports = { createTray, resolveIconPath };

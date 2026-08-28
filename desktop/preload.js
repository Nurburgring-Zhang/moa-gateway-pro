/* Electron preload — context-isolated bridge.
 * Exposes a minimal, safe surface to the renderer. The admin UI talks to the
 * gateway over HTTP directly, so the preload only exposes app metadata.
 */
'use strict';

const { contextBridge } = require('electron');

contextBridge.exposeInMainWorld('moaDesktop', {
  isDesktop: true,
  platform: process.platform,
  version: process.versions.electron,
});

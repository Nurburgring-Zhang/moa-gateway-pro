'use strict';

/**
 * static-server.js — loopback static file server for the renderer shell.
 *
 * Why this exists: the shell embeds the gateway web console in an <iframe>
 * pointing at http://127.0.0.1:<gwPort>/. If the shell itself were loaded
 * from file:// (or a secure custom scheme), Chromium mixed-content rules
 * could block or degrade the http frame. Serving the shell over plain
 * http://127.0.0.1 keeps both origins same-scheme and the iframe reliable.
 *
 * Security posture: binds 127.0.0.1 only, path traversal is rejected, no
 * directory listings, responses are no-store. Pure node:http — unit-testable
 * with plain `node --test`.
 */

const http = require('node:http');
const fs = require('node:fs');
const path = require('node:path');

const CONTENT_TYPES = Object.freeze({
  '.html': 'text/html; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.mjs': 'text/javascript; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.jpeg': 'image/jpeg',
  '.gif': 'image/gif',
  '.svg': 'image/svg+xml',
  '.ico': 'image/x-icon',
  '.woff': 'font/woff',
  '.woff2': 'font/woff2',
  '.txt': 'text/plain; charset=utf-8',
  '.map': 'application/json; charset=utf-8',
});

class StaticServer {
  /**
   * @param {object} opts
   * @param {string} opts.rootDir absolute directory to serve
   * @param {string} [opts.host='127.0.0.1'] bind address (keep loopback!)
   */
  constructor({ rootDir, host = '127.0.0.1' }) {
    if (typeof rootDir !== 'string' || rootDir.trim() === '') {
      throw new TypeError('StaticServer requires rootDir');
    }
    this.rootDir = path.resolve(rootDir);
    this.host = host;
    this.server = null;
    this.port = null;
  }

  get url() {
    return this.port ? `http://${this.host}:${this.port}/` : null;
  }

  /** Start listening. port=0 lets the OS choose a free port. */
  listen(port = 0) {
    return new Promise((resolve, reject) => {
      const server = http.createServer((req, res) => this._handle(req, res));
      server.once('error', reject);
      server.listen(port, this.host, () => {
        this.server = server;
        this.port = server.address().port;
        resolve({ port: this.port, url: this.url });
      });
    });
  }

  close() {
    return new Promise((resolve) => {
      if (!this.server) {
        resolve();
        return;
      }
      this.server.close(() => {
        this.server = null;
        this.port = null;
        resolve();
      });
      // Do not linger on keep-alive sockets.
      if (typeof this.server.closeAllConnections === 'function') {
        this.server.closeAllConnections();
      }
    });
  }

  _handle(req, res) {
    try {
      if (req.method !== 'GET' && req.method !== 'HEAD') {
        this._send(res, 405, 'text/plain; charset=utf-8', Buffer.from('405 method not allowed'));
        return;
      }
      const reqPath = decodeURIComponent((req.url || '/').split('?')[0]);
      let rel = reqPath.replace(/\\/g, '/');
      if (rel.endsWith('/')) rel += 'index.html';
      const abs = path.resolve(this.rootDir, `.${rel}`);
      // Path traversal guard: resolved path must stay inside rootDir.
      const rootWithSep = this.rootDir + path.sep;
      if (abs !== this.rootDir && !abs.startsWith(rootWithSep)) {
        this._send(res, 404, 'text/plain; charset=utf-8', Buffer.from('404 not found'));
        return;
      }
      let stat;
      try {
        stat = fs.statSync(abs);
      } catch {
        this._send(res, 404, 'text/plain; charset=utf-8', Buffer.from('404 not found'));
        return;
      }
      let fileAbs = abs;
      if (stat.isDirectory()) {
        fileAbs = path.join(abs, 'index.html');
        try {
          stat = fs.statSync(fileAbs);
        } catch {
          this._send(res, 404, 'text/plain; charset=utf-8', Buffer.from('404 not found'));
          return;
        }
      }
      if (!stat.isFile()) {
        this._send(res, 404, 'text/plain; charset=utf-8', Buffer.from('404 not found'));
        return;
      }
      const type = CONTENT_TYPES[path.extname(fileAbs).toLowerCase()] || 'application/octet-stream';
      const body = fs.readFileSync(fileAbs);
      this._send(res, 200, type, body, req.method === 'HEAD');
    } catch (err) {
      try {
        this._send(res, 500, 'text/plain; charset=utf-8', Buffer.from(`500 ${err.message}`));
      } catch { /* socket already gone */ }
    }
  }

  _send(res, status, type, body, headOnly = false) {
    res.writeHead(status, {
      'Content-Type': type,
      'Content-Length': body.length,
      'Cache-Control': 'no-store',
      'X-Content-Type-Options': 'nosniff',
    });
    res.end(headOnly ? undefined : body);
  }
}

module.exports = { StaticServer, CONTENT_TYPES };

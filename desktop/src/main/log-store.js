'use strict';

/**
 * log-store.js — real-time capture of gateway stdout/stderr.
 *
 * Wraps the bounded RingBuffer and adds:
 *   - incremental line splitting across arbitrary chunk boundaries (handles
 *     \n, \r\n and lone \r; keeps a per-stream partial-line tail)
 *   - an EventEmitter 'entry' hook so the main process can fan log lines out
 *     to renderer windows as they arrive
 *   - export to a file on disk (for support bundles)
 *
 * The class itself never imports Electron; main.js wires the events through
 * webContents.send. Unit-testable with plain `node --test`.
 */

const EventEmitter = require('node:events');
const fs = require('node:fs');
const path = require('node:path');
const { RingBuffer } = require('./ring-buffer');

class LogStore extends EventEmitter {
  /**
   * @param {object} [opts]
   * @param {number} [opts.maxLines]
   * @param {number} [opts.maxBytes]
   * @param {number} [opts.maxLineLength=20000] hard cap per single line
   */
  constructor({ maxLines, maxBytes, maxLineLength = 20000 } = {}) {
    super();
    this.buffer = new RingBuffer({ maxLines, maxBytes });
    this.maxLineLength = maxLineLength;
    this._tails = new Map(); // stream -> incomplete trailing text
  }

  /** Number of retained lines. */
  get length() {
    return this.buffer.length;
  }

  get dropped() {
    return this.buffer.dropped;
  }

  /**
   * Feed a raw chunk from a child-process stream. Splits into lines and
   * appends complete ones; a trailing fragment without newline is buffered
   * until the next chunk (or flush()).
   *
   * @param {string} stream 'stdout' | 'stderr' | 'system'
   * @param {string|Buffer} chunk
   */
  append(stream, chunk) {
    const text = typeof chunk === 'string' ? chunk : chunk.toString('utf8');
    if (!text) return;
    const tail = this._tails.get(stream) || '';
    const combined = tail + text;
    // Split on \n, then strip trailing \r from each line (covers \r\n),
    // and also treat lone \r as a line break (progress bars etc.).
    const normalized = combined.replace(/\r\n/g, '\n').replace(/\r/g, '\n');
    const parts = normalized.split('\n');
    const newTail = parts.pop(); // may be '' when chunk ended with newline
    this._tails.set(stream, newTail || '');
    for (const line of parts) {
      this._pushLine(stream, line);
    }
  }

  _pushLine(stream, line) {
    let text = line;
    if (text.length > this.maxLineLength) {
      text = `${text.slice(0, this.maxLineLength)} …[line truncated]`;
    }
    const entry = this.buffer.push({ ts: Date.now(), stream, text });
    this.emit('entry', entry);
  }

  /** Push any buffered partial line for a stream (e.g. on process exit). */
  flush(stream) {
    const tail = this._tails.get(stream);
    if (tail) {
      this._tails.set(stream, '');
      this._pushLine(stream, tail);
    }
  }

  /** Flush all streams. */
  flushAll() {
    for (const stream of Array.from(this._tails.keys())) this.flush(stream);
  }

  /** Append a system-generated line (manager state transitions etc.). */
  system(text) {
    this.append('system', `${text}\n`);
  }

  /**
   * Most recent lines, oldest first.
   * @param {number} [limit]
   */
  snapshot(limit) {
    return this.buffer.toArray(limit);
  }

  /** Drop all retained lines and partial tails. */
  clear() {
    this.buffer.clear();
    this._tails.clear();
    this.emit('cleared');
  }

  /**
   * Write the retained log to `filePath` (parent dir must exist or be
   * creatable). Returns the absolute path written.
   * @param {string} filePath
   */
  exportTo(filePath) {
    if (typeof filePath !== 'string' || filePath.trim() === '') {
      throw new TypeError('exportTo requires a file path');
    }
    const abs = path.resolve(filePath);
    fs.mkdirSync(path.dirname(abs), { recursive: true });
    const header = [
      '# MOA Gateway Desktop — service log export',
      `# exported_at: ${new Date().toISOString()}`,
      `# lines: ${this.buffer.length} (dropped since start: ${this.buffer.dropped})`,
      '',
    ].join('\n');
    fs.writeFileSync(abs, header + this.buffer.toString() + '\n', 'utf8');
    return abs;
  }
}

module.exports = { LogStore };

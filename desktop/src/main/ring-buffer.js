'use strict';

/**
 * ring-buffer.js — fixed-capacity FIFO buffer for gateway log lines.
 *
 * Pure module (no Electron dependency): unit-testable with plain `node --test`.
 *
 * Two independent caps protect the renderer from OOM on chatty processes:
 *   - maxLines: hard cap on number of retained entries
 *   - maxBytes: soft cap on the summed UTF-8 payload size of retained entries
 *
 * Entries are plain objects: { ts: number (epoch ms), stream: 'stdout'|'stderr'|'system', text: string }.
 */

const DEFAULT_MAX_LINES = 5000;
const DEFAULT_MAX_BYTES = 2 * 1024 * 1024; // 2 MiB of log text

class RingBuffer {
  /**
   * @param {object} [opts]
   * @param {number} [opts.maxLines] maximum number of entries retained
   * @param {number} [opts.maxBytes] maximum summed payload bytes retained
   */
  constructor({ maxLines = DEFAULT_MAX_LINES, maxBytes = DEFAULT_MAX_BYTES } = {}) {
    if (!Number.isInteger(maxLines) || maxLines < 1) {
      throw new RangeError(`maxLines must be a positive integer, got ${maxLines}`);
    }
    if (!Number.isFinite(maxBytes) || maxBytes < 1) {
      throw new RangeError(`maxBytes must be a positive number, got ${maxBytes}`);
    }
    this.maxLines = maxLines;
    this.maxBytes = maxBytes;
    this._items = [];
    this._bytes = 0;
    this._dropped = 0;
  }

  /** Number of entries currently retained. */
  get length() {
    return this._items.length;
  }

  /** Total UTF-8 payload bytes currently retained. */
  get bytes() {
    return this._bytes;
  }

  /** Count of entries dropped since creation (or last clear) to satisfy caps. */
  get dropped() {
    return this._dropped;
  }

  /**
   * Append one entry. Enforces both caps by evicting oldest entries.
   * @param {{ts?: number, stream?: string, text: string}} entry
   */
  push(entry) {
    if (entry == null || typeof entry.text !== 'string') {
      throw new TypeError('entry.text must be a string');
    }
    const item = {
      ts: typeof entry.ts === 'number' ? entry.ts : Date.now(),
      stream: entry.stream || 'system',
      text: entry.text,
    };
    const size = Buffer.byteLength(item.text, 'utf8');

    // A single line larger than maxBytes is truncated rather than dropped so a
    // pathological line can never starve the log panel entirely.
    if (size > this.maxBytes) {
      const truncated = truncateToBytes(item.text, this.maxBytes);
      item.text = truncated + ' …[truncated]';
    }

    this._items.push(item);
    this._bytes += Buffer.byteLength(item.text, 'utf8');
    this._evict();
    return item;
  }

  _evict() {
    while (
      (this._items.length > this.maxLines || this._bytes > this.maxBytes) &&
      this._items.length > 1
    ) {
      const old = this._items.shift();
      this._bytes -= Buffer.byteLength(old.text, 'utf8');
      this._dropped += 1;
    }
  }

  /**
   * Return retained entries, oldest first.
   * @param {number} [limit] return only the most recent `limit` entries
   * @returns {Array<{ts:number, stream:string, text:string}>}
   */
  toArray(limit) {
    if (limit == null) return this._items.slice();
    if (!Number.isInteger(limit) || limit < 0) {
      throw new RangeError(`limit must be a non-negative integer, got ${limit}`);
    }
    return this._items.slice(Math.max(0, this._items.length - limit));
  }

  /** Render retained entries as plain text lines: `[ISO time] [stream] text`. */
  toString(limit) {
    return this.toArray(limit)
      .map((e) => `[${new Date(e.ts).toISOString()}] [${e.stream}] ${e.text}`)
      .join('\n');
  }

  /** Drop all entries and reset counters. */
  clear() {
    this._items = [];
    this._bytes = 0;
    this._dropped = 0;
  }
}

/** Truncate a string so its UTF-8 encoding is at most `maxBytes` bytes. */
function truncateToBytes(text, maxBytes) {
  const buf = Buffer.from(text, 'utf8');
  if (buf.length <= maxBytes) return text;
  // Slice may cut a multi-byte char; decode with replacement then trim the
  // trailing replacement character if present.
  let out = buf.subarray(0, maxBytes).toString('utf8');
  if (out.endsWith('\uFFFD')) out = out.slice(0, -1);
  return out;
}

module.exports = { RingBuffer, truncateToBytes, DEFAULT_MAX_LINES, DEFAULT_MAX_BYTES };

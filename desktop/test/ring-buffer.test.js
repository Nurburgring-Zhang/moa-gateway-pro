'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');

const { RingBuffer, truncateToBytes } = require('../src/main/ring-buffer');

test('ring-buffer: keeps insertion order', () => {
  const rb = new RingBuffer({ maxLines: 10, maxBytes: 1024 * 1024 });
  rb.push({ text: 'a' });
  rb.push({ text: 'b' });
  rb.push({ text: 'c' });
  assert.deepEqual(rb.toArray().map((e) => e.text), ['a', 'b', 'c']);
  assert.equal(rb.length, 3);
});

test('ring-buffer: evicts oldest entries beyond maxLines', () => {
  const rb = new RingBuffer({ maxLines: 3, maxBytes: 1024 * 1024 });
  for (let i = 0; i < 10; i += 1) rb.push({ text: `line-${i}` });
  const texts = rb.toArray().map((e) => e.text);
  assert.deepEqual(texts, ['line-7', 'line-8', 'line-9']);
  assert.equal(rb.dropped, 7);
});

test('ring-buffer: evicts by maxBytes even under the line cap', () => {
  const rb = new RingBuffer({ maxLines: 1000, maxBytes: 100 });
  for (let i = 0; i < 10; i += 1) rb.push({ text: 'x'.repeat(30) });
  assert.ok(rb.bytes <= 100, `bytes=${rb.bytes}`);
  assert.ok(rb.length >= 1);
  assert.ok(rb.dropped >= 7, `dropped=${rb.dropped}`);
  // newest is always retained
  assert.equal(rb.toArray().at(-1).text, 'x'.repeat(30));
});

test('ring-buffer: a single oversized line is truncated, not lost', () => {
  const rb = new RingBuffer({ maxLines: 10, maxBytes: 50 });
  rb.push({ text: 'y'.repeat(5000) });
  assert.equal(rb.length, 1);
  const entry = rb.toArray()[0];
  assert.ok(Buffer.byteLength(entry.text, 'utf8') <= 50 + Buffer.byteLength(' …[truncated]'));
  assert.ok(entry.text.endsWith('…[truncated]'));
});

test('ring-buffer: toArray(limit) returns the most recent lines', () => {
  const rb = new RingBuffer({ maxLines: 100, maxBytes: 1024 * 1024 });
  for (let i = 0; i < 20; i += 1) rb.push({ text: `n${i}` });
  assert.deepEqual(rb.toArray(3).map((e) => e.text), ['n17', 'n18', 'n19']);
  assert.deepEqual(rb.toArray(0), []);
});

test('ring-buffer: toString renders ISO timestamp + stream tag', () => {
  const rb = new RingBuffer({ maxLines: 10, maxBytes: 1024 });
  rb.push({ ts: Date.UTC(2026, 0, 2, 3, 4, 5), stream: 'stderr', text: 'boom' });
  const out = rb.toString();
  assert.ok(out.includes('[2026-01-02T03:04:05.000Z]'), out);
  assert.ok(out.includes('[stderr] boom'), out);
});

test('ring-buffer: clear resets entries, bytes and dropped counter', () => {
  const rb = new RingBuffer({ maxLines: 2, maxBytes: 1024 });
  rb.push({ text: 'one' });
  rb.push({ text: 'two' });
  rb.push({ text: 'three' });
  assert.equal(rb.dropped, 1);
  rb.clear();
  assert.equal(rb.length, 0);
  assert.equal(rb.bytes, 0);
  assert.equal(rb.dropped, 0);
});

test('ring-buffer: rejects invalid construction and invalid entries', () => {
  assert.throws(() => new RingBuffer({ maxLines: 0 }), RangeError);
  assert.throws(() => new RingBuffer({ maxBytes: -1 }), RangeError);
  const rb = new RingBuffer();
  assert.throws(() => rb.push({}), TypeError);
  assert.throws(() => rb.push(null), TypeError);
  assert.throws(() => rb.toArray(-1), RangeError);
});

test('ring-buffer: multibyte truncation never splits a code point', () => {
  // '中' is 3 bytes in UTF-8; cutting mid-character must not produce garbage
  const out = truncateToBytes('中中中中', 7);
  assert.ok(Buffer.byteLength(out, 'utf8') <= 7);
  assert.ok(!out.includes('\uFFFD'));
  assert.equal(out, '中中');
});

test('ring-buffer: default caps are sane (OOM protection)', () => {
  const rb = new RingBuffer();
  assert.equal(rb.maxLines, 5000);
  assert.equal(rb.maxBytes, 2 * 1024 * 1024);
});

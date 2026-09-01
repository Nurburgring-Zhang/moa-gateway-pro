'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const { LogStore } = require('../src/main/log-store');

test('log-store: splits chunks into lines across boundaries', () => {
  const ls = new LogStore();
  ls.append('stdout', 'hello ');
  ls.append('stdout', 'world\nsecond\n');
  const texts = ls.snapshot().map((e) => e.text);
  assert.deepEqual(texts, ['hello world', 'second']);
});

test('log-store: normalizes CRLF and lone CR', () => {
  const ls = new LogStore();
  ls.append('stderr', 'a\r\nb\rc\n');
  assert.deepEqual(ls.snapshot().map((e) => e.text), ['a', 'b', 'c']);
});

test('log-store: partial tail is flushed on demand (process exit case)', () => {
  const ls = new LogStore();
  ls.append('stdout', 'no newline yet');
  assert.equal(ls.snapshot().length, 0);
  ls.flush('stdout');
  assert.deepEqual(ls.snapshot().map((e) => e.text), ['no newline yet']);
});

test('log-store: keeps per-stream tails independent', () => {
  const ls = new LogStore();
  ls.append('stdout', 'out-partial');
  ls.append('stderr', 'err-partial');
  ls.append('stdout', '-done\n');
  ls.flushAll();
  const lines = ls.snapshot().map((e) => `${e.stream}:${e.text}`);
  assert.deepEqual(lines, ['stdout:out-partial-done', 'stderr:err-partial']);
});

test('log-store: emits entry events for live streaming', () => {
  const ls = new LogStore();
  const seen = [];
  ls.on('entry', (e) => seen.push(`${e.stream}:${e.text}`));
  ls.append('stdout', 'one\ntwo\n');
  assert.deepEqual(seen, ['stdout:one', 'stdout:two']);
});

test('log-store: system() tags manager-generated lines', () => {
  const ls = new LogStore();
  ls.system('state change');
  const [entry] = ls.snapshot();
  assert.equal(entry.stream, 'system');
  assert.equal(entry.text, 'state change');
});

test('log-store: caps are enforced by the underlying ring buffer', () => {
  const ls = new LogStore({ maxLines: 5, maxBytes: 100000 });
  for (let i = 0; i < 20; i += 1) ls.append('stdout', `line ${i}\n`);
  const snap = ls.snapshot();
  assert.equal(snap.length, 5);
  assert.equal(snap[0].text, 'line 15');
  assert.equal(snap[4].text, 'line 19');
  assert.ok(ls.dropped >= 15);
});

test('log-store: very long lines are truncated with a marker', () => {
  const ls = new LogStore({ maxLineLength: 100 });
  ls.append('stdout', `${'x'.repeat(5000)}\n`);
  const [entry] = ls.snapshot();
  assert.ok(entry.text.length <= 100 + ' …[line truncated]'.length);
  assert.ok(entry.text.endsWith('…[line truncated]'));
});

test('log-store: exportTo writes header + lines to disk', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'moa-log-test-'));
  const ls = new LogStore();
  ls.append('stdout', 'alpha\n');
  ls.append('stderr', 'beta\n');
  const out = ls.exportTo(path.join(dir, 'sub', 'log.txt'));
  const content = fs.readFileSync(out, 'utf8');
  assert.ok(content.includes('# MOA Gateway Desktop — service log export'));
  assert.ok(content.includes('[stdout] alpha'));
  assert.ok(content.includes('[stderr] beta'));
});

test('log-store: clear empties buffer and tails and emits cleared', () => {
  const ls = new LogStore();
  let cleared = false;
  ls.on('cleared', () => { cleared = true; });
  ls.append('stdout', 'x\npartial');
  ls.clear();
  assert.equal(ls.length, 0);
  ls.flushAll();
  assert.equal(ls.length, 0); // tail was cleared too
  assert.equal(cleared, true);
});

test('log-store: handles Buffer input', () => {
  const ls = new LogStore();
  ls.append('stdout', Buffer.from('buffered line\n', 'utf8'));
  assert.deepEqual(ls.snapshot().map((e) => e.text), ['buffered line']);
});

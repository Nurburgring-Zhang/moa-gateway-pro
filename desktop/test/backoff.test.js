'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');

const { BackoffPolicy } = require('../src/main/backoff');

test('backoff: exponential growth with cap', () => {
  const bp = new BackoffPolicy({ baseMs: 1000, factor: 2, maxMs: 30000, maxAttempts: 100 });
  assert.equal(bp.delayForAttempt(1), 1000);
  assert.equal(bp.delayForAttempt(2), 2000);
  assert.equal(bp.delayForAttempt(3), 4000);
  assert.equal(bp.delayForAttempt(4), 8000);
  assert.equal(bp.delayForAttempt(5), 16000);
  assert.equal(bp.delayForAttempt(6), 30000); // capped
  assert.equal(bp.delayForAttempt(10), 30000); // stays capped
});

test('backoff: nextDelay consumes attempts and eventually exhausts', () => {
  const bp = new BackoffPolicy({ baseMs: 10, factor: 2, maxMs: 1000, maxAttempts: 3 });
  assert.equal(bp.exhausted, false);
  assert.equal(bp.nextDelay(), 10);
  assert.equal(bp.nextDelay(), 20);
  assert.equal(bp.nextDelay(), 40);
  assert.equal(bp.exhausted, true);
  assert.equal(bp.remaining, 0);
  assert.equal(bp.nextDelay(), null); // no more restarts
  assert.equal(bp.nextDelay(), null);
});

test('backoff: reset restores the full budget', () => {
  const bp = new BackoffPolicy({ baseMs: 5, factor: 2, maxMs: 100, maxAttempts: 2 });
  bp.nextDelay();
  bp.nextDelay();
  assert.equal(bp.exhausted, true);
  bp.reset();
  assert.equal(bp.exhausted, false);
  assert.equal(bp.attempts, 0);
  assert.equal(bp.nextDelay(), 5);
});

test('backoff: deterministic when jitter is 0', () => {
  const a = new BackoffPolicy({ baseMs: 7, factor: 3, maxMs: 500, maxAttempts: 4, jitter: 0 });
  const b = new BackoffPolicy({ baseMs: 7, factor: 3, maxMs: 500, maxAttempts: 4, jitter: 0 });
  for (let i = 0; i < 4; i += 1) {
    assert.equal(a.nextDelay(), b.nextDelay());
  }
});

test('backoff: jitter stays within [delay, min(delay*(1+jitter), maxMs)]', () => {
  // Force the RNG to its max so we can bound the result deterministically.
  const bp = new BackoffPolicy({
    baseMs: 100, factor: 1, maxMs: 10000, maxAttempts: 50, jitter: 0.5, random: () => 0.999999,
  });
  const d = bp.delayForAttempt(1);
  assert.ok(d >= 100 && d <= 150, `d=${d}`);
});

test('backoff: rejects invalid options', () => {
  assert.throws(() => new BackoffPolicy({ baseMs: 0 }), RangeError);
  assert.throws(() => new BackoffPolicy({ factor: 0.5 }), RangeError);
  assert.throws(() => new BackoffPolicy({ baseMs: 100, maxMs: 50 }), RangeError);
  assert.throws(() => new BackoffPolicy({ maxAttempts: 0 }), RangeError);
  assert.throws(() => new BackoffPolicy({ jitter: 2 }), RangeError);
  const bp = new BackoffPolicy();
  assert.throws(() => bp.delayForAttempt(0), RangeError);
  assert.throws(() => bp.delayForAttempt(1.5), RangeError);
});

test('backoff: defaults match the documented restart schedule', () => {
  const bp = new BackoffPolicy();
  assert.equal(bp.baseMs, 1000);
  assert.equal(bp.factor, 2);
  assert.equal(bp.maxMs, 30000);
  assert.equal(bp.maxAttempts, 8);
  // 1,2,4,8,16,30,30,30 seconds
  assert.deepEqual(
    [1, 2, 3, 4, 5, 6, 7, 8].map((n) => bp.delayForAttempt(n)),
    [1000, 2000, 4000, 8000, 16000, 30000, 30000, 30000],
  );
});

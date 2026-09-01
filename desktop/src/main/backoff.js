'use strict';

/**
 * backoff.js — deterministic exponential backoff policy for gateway restarts.
 *
 * Pure module (no timers, no Electron): the caller owns the clock. This keeps
 * the restart schedule fully unit-testable and makes the policy auditable:
 * for defaults (base 1s, factor 2, cap 30s) the schedule is
 *   1s, 2s, 4s, 8s, 16s, 30s, 30s, … up to maxAttempts, after which the
 * manager gives up and reports `failed` instead of restarting forever.
 */

const DEFAULTS = Object.freeze({
  baseMs: 1000,
  factor: 2,
  maxMs: 30000,
  maxAttempts: 8,
  jitter: 0, // fraction 0..1 of delay added as uniform random jitter (0 = deterministic)
});

class BackoffPolicy {
  /**
   * @param {object} [opts]
   * @param {number} [opts.baseMs=1000] first delay in milliseconds
   * @param {number} [opts.factor=2] multiplicative growth per attempt
   * @param {number} [opts.maxMs=30000] upper bound for any single delay
   * @param {number} [opts.maxAttempts=8] number of restarts allowed before giving up
   * @param {number} [opts.jitter=0] optional jitter fraction (0..1); 0 keeps it deterministic
   * @param {(n:number)=>number} [opts.random] RNG injectable for tests, returns [0,1)
   */
  constructor(opts = {}) {
    const o = { ...DEFAULTS, ...opts };
    if (!Number.isFinite(o.baseMs) || o.baseMs <= 0) throw new RangeError(`baseMs must be > 0, got ${o.baseMs}`);
    if (!Number.isFinite(o.factor) || o.factor < 1) throw new RangeError(`factor must be >= 1, got ${o.factor}`);
    if (!Number.isFinite(o.maxMs) || o.maxMs < o.baseMs) throw new RangeError(`maxMs must be >= baseMs, got ${o.maxMs}`);
    if (!Number.isInteger(o.maxAttempts) || o.maxAttempts < 1) throw new RangeError(`maxAttempts must be a positive integer, got ${o.maxAttempts}`);
    if (!Number.isFinite(o.jitter) || o.jitter < 0 || o.jitter > 1) throw new RangeError(`jitter must be within [0,1], got ${o.jitter}`);
    this.baseMs = o.baseMs;
    this.factor = o.factor;
    this.maxMs = o.maxMs;
    this.maxAttempts = o.maxAttempts;
    this.jitter = o.jitter;
    this._random = typeof o.random === 'function' ? o.random : Math.random;
    this._attempts = 0;
  }

  /** Number of restart attempts already consumed since the last reset(). */
  get attempts() {
    return this._attempts;
  }

  /** True when no further restart attempts remain. */
  get exhausted() {
    return this._attempts >= this.maxAttempts;
  }

  /** Number of attempts still available. */
  get remaining() {
    return Math.max(0, this.maxAttempts - this._attempts);
  }

  /**
   * Delay for a given attempt number WITHOUT consuming it.
   * @param {number} attempt 1-based attempt number
   * @returns {number} delay in milliseconds
   */
  delayForAttempt(attempt) {
    if (!Number.isInteger(attempt) || attempt < 1) {
      throw new RangeError(`attempt must be a positive integer, got ${attempt}`);
    }
    const raw = this.baseMs * Math.pow(this.factor, attempt - 1);
    const capped = Math.min(raw, this.maxMs);
    if (this.jitter <= 0) return Math.round(capped);
    const jitterAmount = capped * this.jitter * this._random();
    return Math.round(Math.min(capped + jitterAmount, this.maxMs));
  }

  /**
   * Consume one attempt and return the delay to wait before it.
   * Returns null when the policy is exhausted (caller must stop restarting).
   * @returns {number|null}
   */
  nextDelay() {
    if (this.exhausted) return null;
    this._attempts += 1;
    return this.delayForAttempt(this._attempts);
  }

  /** Reset the attempt counter (call after a successful start / stable run). */
  reset() {
    this._attempts = 0;
  }
}

module.exports = { BackoffPolicy, DEFAULTS };

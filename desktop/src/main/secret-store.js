'use strict';

/**
 * secret-store.js — encrypted storage for user API keys.
 *
 * Secrets are encrypted with Electron `safeStorage` (DPAPI per-user
 * encryption on Windows) before touching disk. Plaintext is NEVER persisted:
 * if encryption is unavailable the store refuses to write instead of
 * degrading to plaintext.
 *
 * The `safeStorage` implementation is constructor-injected so that:
 *   - production wires the real `require('electron').safeStorage`
 *   - unit tests can exercise the store's logic (naming rules, persistence,
 *     envelope format) against a stand-in for the OS crypto primitive.
 *
 * Keys stored here are injected into the gateway child process as environment
 * variables at spawn time (name = env var name, e.g. GROQ_API_KEY), which is
 * exactly how moa_gateway.config._resolve_api_keys consumes them.
 */

const fs = require('node:fs');
const path = require('node:path');

const FILE_VERSION = 1;
// Env-var style names only: these become environment variables for the child.
const NAME_RE = /^[A-Za-z_][A-Za-z0-9_]{0,127}$/;

class SecretStoreError extends Error {
  constructor(message, code) {
    super(message);
    this.name = 'SecretStoreError';
    this.code = code;
  }
}

class SecretStore {
  /**
   * @param {object} opts
   * @param {string} opts.filePath absolute path of the encrypted-store JSON file
   * @param {object} opts.safeStorage Electron safeStorage-compatible backend:
   *   { isEncryptionAvailable(): boolean,
   *     encryptString(s: string): Buffer,
   *     decryptString(b: Buffer): string }
   */
  constructor({ filePath, safeStorage }) {
    if (typeof filePath !== 'string' || filePath.trim() === '') {
      throw new TypeError('SecretStore requires a file path');
    }
    if (!safeStorage || typeof safeStorage.encryptString !== 'function' || typeof safeStorage.decryptString !== 'function') {
      throw new TypeError('SecretStore requires a safeStorage backend');
    }
    this.filePath = filePath;
    this.safeStorage = safeStorage;
  }

  /** Whether the platform can actually encrypt. False ⇒ writes are refused. */
  isAvailable() {
    try {
      return this.safeStorage.isEncryptionAvailable() === true;
    } catch {
      return false;
    }
  }

  _readFile() {
    let raw;
    try {
      raw = fs.readFileSync(this.filePath, 'utf8');
    } catch (err) {
      if (err.code === 'ENOENT') return { version: FILE_VERSION, entries: {} };
      throw new SecretStoreError(`failed to read secret store: ${err.message}`, 'read-failed');
    }
    let parsed;
    try {
      parsed = JSON.parse(raw);
    } catch {
      throw new SecretStoreError('secret store file is corrupt; refusing to overwrite', 'corrupt');
    }
    if (!parsed || typeof parsed !== 'object' || !parsed.entries || typeof parsed.entries !== 'object') {
      throw new SecretStoreError('secret store file has unexpected shape', 'corrupt');
    }
    return parsed;
  }

  _writeFile(doc) {
    const dir = path.dirname(this.filePath);
    fs.mkdirSync(dir, { recursive: true });
    const tmp = `${this.filePath}.tmp-${process.pid}-${Date.now()}`;
    fs.writeFileSync(tmp, JSON.stringify(doc, null, 2), { encoding: 'utf8', mode: 0o600 });
    fs.renameSync(tmp, this.filePath);
    try {
      fs.chmodSync(this.filePath, 0o600);
    } catch { /* best effort on platforms without chmod semantics */ }
  }

  _assertName(name) {
    if (typeof name !== 'string' || !NAME_RE.test(name)) {
      throw new SecretStoreError(
        `invalid secret name ${JSON.stringify(name)}: must match [A-Za-z_][A-Za-z0-9_]* (it becomes an env var for the gateway)`,
        'bad-name',
      );
    }
  }

  /**
   * Encrypt and store a secret (overwrites existing entry of same name).
   * @param {string} name env-var-style identifier
   * @param {string} value plaintext secret (kept in memory only)
   */
  set(name, value) {
    this._assertName(name);
    if (typeof value !== 'string' || value.length === 0) {
      throw new SecretStoreError('secret value must be a non-empty string', 'bad-value');
    }
    if (!this.isAvailable()) {
      throw new SecretStoreError(
        'OS secret encryption (DPAPI/safeStorage) is unavailable on this machine; refusing to store plaintext',
        'encryption-unavailable',
      );
    }
    const doc = this._readFile();
    const encrypted = this.safeStorage.encryptString(value);
    if (!Buffer.isBuffer(encrypted) || encrypted.length === 0) {
      throw new SecretStoreError('encryption backend returned no data', 'encryption-failed');
    }
    doc.entries[name] = { data: encrypted.toString('base64'), updatedAt: new Date().toISOString() };
    this._writeFile(doc);
  }

  /**
   * Decrypt and return a secret, or null when absent.
   * @param {string} name
   * @returns {string|null}
   */
  get(name) {
    this._assertName(name);
    const doc = this._readFile();
    const entry = doc.entries[name];
    if (!entry || typeof entry.data !== 'string') return null;
    if (!this.isAvailable()) {
      throw new SecretStoreError('OS secret encryption unavailable; cannot decrypt', 'encryption-unavailable');
    }
    return this.safeStorage.decryptString(Buffer.from(entry.data, 'base64'));
  }

  /** True when an entry exists (does not decrypt). */
  has(name) {
    this._assertName(name);
    const doc = this._readFile();
    return Boolean(doc.entries[name]);
  }

  /**
   * Metadata for all stored secrets — names + timestamps only.
   * Plaintext never leaves main(); the renderer only ever sees this list.
   * @returns {Array<{name: string, updatedAt: string}>}
   */
  list() {
    const doc = this._readFile();
    return Object.entries(doc.entries)
      .map(([name, entry]) => ({ name, updatedAt: entry.updatedAt || '' }))
      .sort((a, b) => a.name.localeCompare(b.name));
  }

  /**
   * Delete one secret.
   * @param {string} name
   * @returns {boolean} true when an entry was removed
   */
  delete(name) {
    this._assertName(name);
    const doc = this._readFile();
    if (!(name in doc.entries)) return false;
    delete doc.entries[name];
    this._writeFile(doc);
    return true;
  }

  /**
   * All secrets as a plain env-shaped object, for injection into the gateway
   * child process environment at spawn time. Memory-only; never persisted.
   * Entries that fail to decrypt are skipped and reported.
   * @returns {{env: Object<string,string>, skipped: string[]}}
   */
  allAsEnv() {
    const doc = this._readFile();
    const env = {};
    const skipped = [];
    for (const name of Object.keys(doc.entries)) {
      try {
        const value = this.get(name);
        if (value != null) env[name] = value;
        else skipped.push(name);
      } catch {
        skipped.push(name);
      }
    }
    return { env, skipped };
  }
}

module.exports = { SecretStore, SecretStoreError, NAME_RE, FILE_VERSION };

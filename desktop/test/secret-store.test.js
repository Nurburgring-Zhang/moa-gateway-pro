'use strict';

/**
 * secret-store.test.js
 *
 * The safeStorage backend is constructor-injected. In these tests it is
 * replaced by a deterministic stand-in for the OS crypto primitive (DPAPI),
 * so the store's own logic — naming rules, envelope format, persistence,
 * plaintext-never-on-disk guarantee — is verified in plain Node. The REAL
 * Electron safeStorage is exercised separately by `npm run smoke`
 * (electron . --smoke-test), which asserts ciphertext-on-disk with DPAPI.
 */

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const { SecretStore, SecretStoreError } = require('../src/main/secret-store');

// Deterministic reversible transform standing in for DPAPI (XOR + base64 is
// obviously NOT security — it only simulates "encryptString returns opaque
// bytes and decryptString reverses them").
function makeFakeBackend({ available = true } = {}) {
  const KEY = Buffer.from('moa-test-stand-in-key');
  return {
    isEncryptionAvailable: () => available,
    encryptString: (s) => {
      const buf = Buffer.from(s, 'utf8');
      const out = Buffer.alloc(buf.length);
      for (let i = 0; i < buf.length; i += 1) out[i] = buf[i] ^ KEY[i % KEY.length];
      return out;
    },
    decryptString: (buf) => {
      const out = Buffer.alloc(buf.length);
      for (let i = 0; i < buf.length; i += 1) out[i] = buf[i] ^ KEY[i % KEY.length];
      return out.toString('utf8');
    },
  };
}

function tmpFile() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'moa-secret-test-'));
  return path.join(dir, 'secrets.json');
}

test('secret-store: set/get round-trip through the crypto backend', () => {
  const store = new SecretStore({ filePath: tmpFile(), safeStorage: makeFakeBackend() });
  store.set('GROQ_API_KEY', 'gsk_secret_value_123');
  assert.equal(store.get('GROQ_API_KEY'), 'gsk_secret_value_123');
  assert.equal(store.get('NOT_THERE'), null);
});

test('secret-store: plaintext NEVER appears in the file', () => {
  const file = tmpFile();
  const store = new SecretStore({ filePath: file, safeStorage: makeFakeBackend() });
  store.set('DASHSCOPE_API_KEY', 'sk-plaintext-that-must-not-touch-disk');
  const raw = fs.readFileSync(file, 'utf8');
  assert.ok(!raw.includes('sk-plaintext-that-must-not-touch-disk'));
  // name + envelope are present
  assert.ok(raw.includes('DASHSCOPE_API_KEY'));
  const parsed = JSON.parse(raw);
  assert.equal(parsed.version, 1);
  assert.ok(parsed.entries.DASHSCOPE_API_KEY.data.length > 0);
  assert.ok(parsed.entries.DASHSCOPE_API_KEY.updatedAt);
});

test('secret-store: persists across instances (file-backed)', () => {
  const file = tmpFile();
  const backend = makeFakeBackend();
  new SecretStore({ filePath: file, safeStorage: backend }).set('KEY_A', 'value-a');
  const second = new SecretStore({ filePath: file, safeStorage: backend });
  assert.equal(second.get('KEY_A'), 'value-a');
  assert.deepEqual(second.list().map((e) => e.name), ['KEY_A']);
});

test('secret-store: refuses to store when encryption is unavailable', () => {
  const file = tmpFile();
  const store = new SecretStore({ filePath: file, safeStorage: makeFakeBackend({ available: false }) });
  assert.equal(store.isAvailable(), false);
  assert.throws(() => store.set('ANY_KEY', 'value'), (err) => {
    assert.ok(err instanceof SecretStoreError);
    assert.equal(err.code, 'encryption-unavailable');
    return true;
  });
  // nothing was written
  assert.equal(fs.existsSync(file), false);
});

test('secret-store: enforces env-var naming rules', () => {
  const store = new SecretStore({ filePath: tmpFile(), safeStorage: makeFakeBackend() });
  for (const bad of ['1ABC', 'HAS SPACE', 'HAS-DASH', 'ÄÖÜ', '', 'x'.repeat(200)]) {
    assert.throws(() => store.set(bad, 'v'), (err) => err.code === 'bad-name', `name=${bad}`);
  }
  for (const good of ['GROQ_API_KEY', '_private', 'A1']) {
    store.set(good, 'v');
  }
  assert.equal(store.list().length, 3);
});

test('secret-store: rejects empty values', () => {
  const store = new SecretStore({ filePath: tmpFile(), safeStorage: makeFakeBackend() });
  assert.throws(() => store.set('KEY', ''), (err) => err.code === 'bad-value');
  assert.throws(() => store.set('KEY', null), (err) => err.code === 'bad-value');
});

test('secret-store: overwrite updates value and timestamp', async () => {
  const store = new SecretStore({ filePath: tmpFile(), safeStorage: makeFakeBackend() });
  store.set('KEY', 'v1');
  const t1 = store.list()[0].updatedAt;
  await new Promise((r) => setTimeout(r, 5));
  store.set('KEY', 'v2');
  assert.equal(store.get('KEY'), 'v2');
  const t2 = store.list()[0].updatedAt;
  assert.ok(t2 >= t1);
});

test('secret-store: delete removes entries and reports it', () => {
  const store = new SecretStore({ filePath: tmpFile(), safeStorage: makeFakeBackend() });
  store.set('KEY_A', '1');
  store.set('KEY_B', '2');
  assert.equal(store.delete('KEY_A'), true);
  assert.equal(store.delete('KEY_A'), false);
  assert.deepEqual(store.list().map((e) => e.name), ['KEY_B']);
  assert.equal(store.has('KEY_A'), false);
});

test('secret-store: allAsEnv returns env-shaped map for child spawning', () => {
  const store = new SecretStore({ filePath: tmpFile(), safeStorage: makeFakeBackend() });
  store.set('GROQ_API_KEY', 'g1');
  store.set('OPENAI_API_KEY', 'o1');
  const { env, skipped } = store.allAsEnv();
  assert.deepEqual(env, { GROQ_API_KEY: 'g1', OPENAI_API_KEY: 'o1' });
  assert.deepEqual(skipped, []);
});

test('secret-store: corrupt file raises a clear error instead of clobbering', () => {
  const file = tmpFile();
  fs.writeFileSync(file, '{{{{', 'utf8');
  const store = new SecretStore({ filePath: file, safeStorage: makeFakeBackend() });
  assert.throws(() => store.list(), (err) => err.code === 'corrupt');
  assert.throws(() => store.set('K', 'v'), (err) => err.code === 'corrupt');
  // original bytes untouched
  assert.equal(fs.readFileSync(file, 'utf8'), '{{{{');
});

test('secret-store: constructor requires backend and path', () => {
  assert.throws(() => new SecretStore({ filePath: '', safeStorage: makeFakeBackend() }), TypeError);
  assert.throws(() => new SecretStore({ filePath: tmpFile(), safeStorage: null }), TypeError);
  assert.throws(() => new SecretStore({ filePath: tmpFile(), safeStorage: {} }), TypeError);
});

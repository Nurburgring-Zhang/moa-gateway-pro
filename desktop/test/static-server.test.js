'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const http = require('node:http');

const { StaticServer } = require('../src/main/static-server');

function get(url) {
  return new Promise((resolve, reject) => {
    const req = http.get(url, { timeout: 4000 }, (res) => {
      let raw = '';
      res.on('data', (d) => { raw += d; });
      res.on('end', () => resolve({ status: res.statusCode, raw, headers: res.headers }));
    });
    req.on('error', reject);
    req.on('timeout', () => req.destroy(new Error('timeout')));
  });
}

function makeRoot() {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'moa-static-test-'));
  fs.writeFileSync(path.join(root, 'index.html'), '<html><body>HOME</body></html>', 'utf8');
  fs.mkdirSync(path.join(root, 'sub'), { recursive: true });
  fs.writeFileSync(path.join(root, 'sub', 'page.html'), '<html><body>SUB</body></html>', 'utf8');
  fs.writeFileSync(path.join(root, 'app.js'), 'console.log(1);', 'utf8');
  fs.writeFileSync(path.join(root, 'styles.css'), 'body{}', 'utf8');
  fs.writeFileSync(path.join(root, 'secret.txt'), 'TOP-SECRET', 'utf8');
  return root;
}

test('static-server: serves index.html at /', async () => {
  const server = new StaticServer({ rootDir: makeRoot() });
  const { url } = await server.listen(0);
  const res = await get(url);
  assert.equal(res.status, 200);
  assert.ok(res.raw.includes('HOME'));
  assert.ok(res.headers['content-type'].startsWith('text/html'));
  await server.close();
});

test('static-server: serves nested files with correct content types', async () => {
  const server = new StaticServer({ rootDir: makeRoot() });
  const { url } = await server.listen(0);
  const js = await get(`${url}app.js`);
  const css = await get(`${url}styles.css`);
  const sub = await get(`${url}sub/page.html`);
  assert.equal(js.status, 200);
  assert.ok(js.headers['content-type'].includes('javascript'));
  assert.equal(css.status, 200);
  assert.ok(css.headers['content-type'].includes('text/css'));
  assert.equal(sub.status, 200);
  assert.ok(sub.raw.includes('SUB'));
  await server.close();
});

test('static-server: 404 for missing files', async () => {
  const server = new StaticServer({ rootDir: makeRoot() });
  const { url } = await server.listen(0);
  const res = await get(`${url}does-not-exist.js`);
  assert.equal(res.status, 404);
  await server.close();
});

test('static-server: path traversal cannot escape the root', async () => {
  const root = makeRoot();
  // a file OUTSIDE the root that a traversal attack would target
  fs.writeFileSync(path.join(path.dirname(root), 'outside.txt'), 'OUTSIDE', 'utf8');
  const server = new StaticServer({ rootDir: root });
  const { url } = await server.listen(0);
  const attempts = [
    `${url}../outside.txt`,
    `${url}..%2foutside.txt`,
    `${url}%2e%2e/outside.txt`,
    `${url}sub/../../outside.txt`,
  ];
  for (const target of attempts) {
    // eslint-disable-next-line no-await-in-loop
    const res = await get(target).catch((e) => ({ status: 0, raw: String(e) }));
    assert.ok(res.status === 404 || res.status === 400 || res.status === 0,
      `traversal ${target} returned ${res.status}`);
    assert.ok(!String(res.raw).includes('OUTSIDE'), `traversal ${target} leaked content`);
  }
  await server.close();
});

test('static-server: rejects non-GET methods', async () => {
  const server = new StaticServer({ rootDir: makeRoot() });
  const { url } = await server.listen(0);
  const res = await new Promise((resolve, reject) => {
    const req = http.request(`${url}index.html`, { method: 'POST', timeout: 4000 }, (r) => {
      let raw = '';
      r.on('data', (d) => { raw += d; });
      r.on('end', () => resolve({ status: r.statusCode, raw }));
    });
    req.on('error', reject);
    req.end();
  });
  assert.equal(res.status, 405);
  await server.close();
});

test('static-server: binds loopback only', async () => {
  const server = new StaticServer({ rootDir: makeRoot() });
  const { port } = await server.listen(0);
  assert.equal(server.host, '127.0.0.1');
  const res = await get(`http://127.0.0.1:${port}/`);
  assert.equal(res.status, 200);
  await server.close();
});

test('static-server: close() releases the port', async () => {
  const server = new StaticServer({ rootDir: makeRoot() });
  const { port } = await server.listen(0);
  await server.close();
  await assert.rejects(() => get(`http://127.0.0.1:${port}/`));
});

test('static-server: requires rootDir', () => {
  assert.throws(() => new StaticServer({}), TypeError);
});

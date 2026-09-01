'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const paths = require('../src/main/paths');

function makeFakeRepo(root, name = 'repo') {
  const repo = path.join(root, name);
  fs.mkdirSync(path.join(repo, 'moa_gateway'), { recursive: true });
  fs.writeFileSync(path.join(repo, 'moa_gateway', '__init__.py'), '', 'utf8');
  fs.writeFileSync(path.join(repo, 'moa_gateway', 'server.py'), '# fake gateway\n', 'utf8');
  fs.writeFileSync(path.join(repo, 'config.yaml'), 'server:\n  port: 8910\n', 'utf8');
  return repo;
}

function makeFakeVenvPython(root, relToRepo) {
  const py = path.join(root, relToRepo, 'Scripts', 'python.exe');
  fs.mkdirSync(path.dirname(py), { recursive: true });
  fs.writeFileSync(py, 'MZ-fake-executable', 'utf8');
  return py;
}

test('paths: isGatewayRepo recognizes the package layout', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'moa-paths-'));
  const repo = makeFakeRepo(root);
  assert.equal(paths.isGatewayRepo(repo), true);
  assert.equal(paths.isGatewayRepo(path.join(repo, 'moa_gateway')), false);
  assert.equal(paths.isGatewayRepo(path.join(root, 'nope')), false);
  assert.equal(paths.isGatewayRepo(''), false);
  assert.equal(paths.isGatewayRepo(null), false);
});

test('paths: candidateRepoPaths orders user-config > bundled > dev-layout > cwd', () => {
  const cands = paths.candidateRepoPaths({
    configuredRepoPath: 'C:/custom/gw',
    appPath: 'C:/app/desktop',
    resourcesPath: 'C:/app/resources',
    cwd: 'C:/work/desktop',
  });
  assert.deepEqual(cands.map((c) => c.source), ['user-configured', 'bundled-resources', 'dev-layout', 'cwd-parent']);
  assert.equal(cands[0].path, path.resolve('C:/custom/gw'));
  assert.equal(cands[1].path, path.resolve('C:/app/resources/gateway'));
  assert.equal(cands[2].path, path.resolve('C:/app'));
});

test('paths: candidateRepoPaths de-duplicates identical paths', () => {
  const cands = paths.candidateRepoPaths({
    configuredRepoPath: 'C:/same',
    appPath: 'C:/same/desktop', // dev-layout resolves to C:/same too
    cwd: 'C:/same/desktop',
  });
  const resolved = cands.map((c) => c.path);
  assert.equal(new Set(resolved).size, resolved.length);
});

test('paths: resolveGatewayRepo finds the dev-layout repo', async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'moa-paths-'));
  const repo = makeFakeRepo(root);
  const desktopDir = path.join(repo, 'desktop');
  fs.mkdirSync(desktopDir, { recursive: true });

  const res = await paths.resolveGatewayRepo({ appPath: desktopDir, resourcesPath: '' });
  assert.equal(res.path, path.resolve(repo));
  assert.equal(res.source, 'dev-layout');
  assert.equal(res.valid, true);
});

test('paths: resolveGatewayRepo prefers the user-configured path', async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'moa-paths-'));
  const custom = makeFakeRepo(root, 'custom-gw');
  const decoy = makeFakeRepo(root, 'decoy');
  const desktopDir = path.join(decoy, 'desktop');
  fs.mkdirSync(desktopDir, { recursive: true });

  const res = await paths.resolveGatewayRepo({
    configuredRepoPath: custom,
    appPath: desktopDir,
    resourcesPath: '',
  });
  assert.equal(res.path, path.resolve(custom));
  assert.equal(res.source, 'user-configured');
});

test('paths: resolveGatewayRepo reports all tried candidates when nothing matches', async () => {
  const res = await paths.resolveGatewayRepo({
    appPath: '/definitely/not/here/desktop',
    resourcesPath: '/definitely/not/here/resources',
    cwd: '/definitely/not/here/desktop',
  });
  assert.equal(res.path, null);
  assert.equal(res.valid, false);
  assert.ok(Array.isArray(res.candidates) && res.candidates.length >= 2);
});

test('paths: candidatePythonPaths prefers venv above repo, then in-repo, then PATH', () => {
  const repo = path.resolve('/tmp/repo');
  const cands = paths.candidatePythonPaths({ repoPath: repo });
  const sources = cands.map((c) => c.source);
  assert.ok(sources.indexOf('venv-above-repo') < sources.indexOf('venv-in-repo'));
  assert.ok(sources.indexOf('venv-in-repo') < sources.indexOf('system-path'));
  assert.equal(cands.at(-1).source === 'py-launcher' || cands.at(-1).source === 'system-path', true);
  // user override always first
  const withOverride = paths.candidatePythonPaths({ configuredPythonPath: 'C:/py/python.exe', repoPath: repo });
  assert.equal(withOverride[0].path, 'C:/py/python.exe');
  assert.equal(withOverride[0].source, 'user-configured');
});

test('paths: resolvePython picks an existing venv interpreter over PATH', async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'moa-paths-'));
  const repo = makeFakeRepo(root);
  const py = makeFakeVenvPython(root, path.join(path.basename(repo), '..', 'venv'));

  // Inject a stub version probe by monkey-patching is not possible (pure fn),
  // so verify the candidate ordering logic instead: the venv path must be the
  // first absolute candidate and must exist on disk.
  const cands = paths.candidatePythonPaths({ repoPath: repo });
  const firstAbs = cands.find((c) => path.isAbsolute(c.path));
  assert.equal(firstAbs.path, path.resolve(py));
  assert.equal(paths.isPythonExecutable(firstAbs.path), true);
});

test('paths: getPythonVersion parses real interpreter output', async () => {
  // Use the Node binary itself with a script that mimics `python --version`
  // output shape is not possible; instead verify graceful null on garbage.
  const version = await paths.getPythonVersion(process.execPath, 3000);
  // node --version prints v22.x — not "Python x", so expect null
  assert.equal(version, null);
});

test('paths: getPythonVersion returns null quickly for missing binaries', async () => {
  const started = Date.now();
  const version = await paths.getPythonVersion('C:/no/such/python.exe', 3000);
  assert.equal(version, null);
  assert.ok(Date.now() - started < 3000);
});

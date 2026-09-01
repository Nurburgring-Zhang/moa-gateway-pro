'use strict';

/**
 * scripts/syntax-check.js — runs `node --check` over every JS file that
 * ships in the desktop app (main process, preload, renderer, scripts, tests)
 * and fails with a non-zero exit code on the first syntax error.
 *
 * Usage: node scripts/syntax-check.js
 */

const fs = require('node:fs');
const path = require('node:path');
const { spawnSync } = require('node:child_process');

const ROOT = path.resolve(__dirname, '..');
const SCAN_DIRS = ['src', 'scripts', 'test'];
const ROOT_FILES = ['main.js', 'preload.js'];
const SKIP_DIRS = new Set(['node_modules', 'dist', '.git']);

function collectJs(dir, out) {
  let entries;
  try {
    entries = fs.readdirSync(dir, { withFileTypes: true });
  } catch {
    return out;
  }
  for (const entry of entries) {
    if (SKIP_DIRS.has(entry.name)) continue;
    const abs = path.join(dir, entry.name);
    if (entry.isDirectory()) collectJs(abs, out);
    else if (entry.isFile() && entry.name.endsWith('.js')) out.push(abs);
  }
  return out;
}

function main() {
  const files = [];
  for (const f of ROOT_FILES) {
    const abs = path.join(ROOT, f);
    if (fs.existsSync(abs)) files.push(abs);
  }
  for (const d of SCAN_DIRS) collectJs(path.join(ROOT, d), files);

  let failed = 0;
  for (const file of files) {
    const rel = path.relative(ROOT, file);
    const res = spawnSync(process.execPath, ['--check', file], { encoding: 'utf8' });
    if (res.status === 0) {
      console.log(`ok   ${rel}`);
    } else {
      failed += 1;
      console.error(`FAIL ${rel}`);
      if (res.stderr) console.error(res.stderr);
    }
  }
  console.log(`\n${files.length - failed}/${files.length} files passed node --check`);
  process.exit(failed === 0 ? 0 : 1);
}

main();

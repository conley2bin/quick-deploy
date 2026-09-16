import assert from "node:assert/strict";
import { execFileSync, spawnSync } from "node:child_process";
import { chmodSync, existsSync, mkdirSync, mkdtempSync, readFileSync, realpathSync, rmSync, symlinkSync, unlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

const source = resolve(dirname(fileURLToPath(import.meta.url)), '..');
function setup(git = true) {
  const root = mkdtempSync(join(tmpdir(), 'pi-copy-links-install-'));
  const home = join(root, 'agent'), bin = join(root, 'bin'); mkdirSync(home); mkdirSync(bin);
  // Exercise deployment semantics without reinstalling dependencies under a running test.
  writeFileSync(join(bin, 'npm'), '#!/bin/sh\nexit 0\n'); chmodSync(join(bin, 'npm'), 0o755);
  if (git) execFileSync('git', ['init', '-q', home]);
  writeFileSync(join(home, 'settings.json'), '{"custom":42}\n');
  const target = join(home, 'extensions/pi-copy-links');
  return { root, home, target, run: () => spawnSync('bash', [join(source, 'install.sh')], {
    encoding: 'utf8', env: { ...process.env, PI_CODING_AGENT_DIR: home, PATH: `${bin}:${process.env.PATH}` },
  }), close: () => rmSync(root, { recursive: true, force: true }) };
}

test('installer creates an idempotent symlink and a name-specific local exclude; no settings changes', () => {
  const f = setup();
  try {
    assert.equal(f.run().status, 0); assert.equal(f.run().status, 0);
    assert.equal(realpathSync(f.target), source);
    assert.equal(readFileSync(join(f.home, 'settings.json'), 'utf8'), '{"custom":42}\n');
    assert.equal(readFileSync(join(f.home, '.git/info/exclude'), 'utf8').split('\n').filter(x => x === '/extensions/pi-copy-links').length, 1);
  } finally { f.close(); }
});

test('installer refuses existing directories, foreign links, and worktree-absent indexed paths', () => {
  for (const kind of ['directory', 'foreign', 'indexed']) {
    const f = setup();
    try {
      mkdirSync(dirname(f.target));
      const before = readFileSync(join(f.home, '.git/info/exclude'), 'utf8');
      if (kind === 'directory') mkdirSync(f.target);
      if (kind === 'foreign') symlinkSync('/nonexistent/foreign-extension', f.target);
      if (kind === 'indexed') {
        writeFileSync(f.target, 'indexed'); execFileSync('git', ['-C', f.home, 'add', 'extensions/pi-copy-links']); unlinkSync(f.target);
      }
      const result = f.run(); assert.notEqual(result.status, 0); assert.match(result.stderr, /refusing/);
      assert.equal(readFileSync(join(f.home, '.git/info/exclude'), 'utf8'), before);
    } finally { f.close(); }
  }
});

test('fresh non-Git Pi homes are supported', () => {
  const f = setup(false);
  try { assert.equal(f.run().status, 0); assert.equal(realpathSync(f.target), source); assert.equal(existsSync(join(f.home, '.git')), false); }
  finally { f.close(); }
});

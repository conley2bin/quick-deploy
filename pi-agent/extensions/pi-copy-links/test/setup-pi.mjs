// Test against the installed Pi, not a second npm copy with different prototypes.
import { execFileSync } from 'node:child_process';
import { existsSync, lstatSync, mkdirSync, readFileSync, realpathSync, symlinkSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const extension = resolve(dirname(fileURLToPath(import.meta.url)), '..');
let root = process.env.PI_TEST_CORE;
if (!root) {
  const cli = execFileSync('sh', ['-c', 'command -v pi'], { encoding: 'utf8' }).trim();
  root = dirname(realpathSync(cli));
  while (root !== dirname(root)) {
    const pkg = join(root, 'package.json');
    if (existsSync(pkg) && JSON.parse(readFileSync(pkg, 'utf8')).name === '@earendil-works/pi-coding-agent') break;
    root = dirname(root);
  }
}
if (!existsSync(join(root, 'dist/index.js'))) throw new Error('Set PI_TEST_CORE to the installed Pi package root');
for (const [name, source] of [
  ['pi-coding-agent', root],
  ['pi-tui', join(root, 'node_modules/@earendil-works/pi-tui')],
]) {
  const target = join(extension, 'node_modules/@earendil-works', name);
  mkdirSync(dirname(target), { recursive: true });
  let present;
  try { present = lstatSync(target); } catch (error) { if (error.code !== 'ENOENT') throw error; }
  if (present) {
    if (!present.isSymbolicLink() || realpathSync(target) !== realpathSync(source)) {
      throw new Error(`Refusing a second or mismatched Pi test runtime: ${target}`);
    }
  } else symlinkSync(source, target, 'dir');
}

import assert from "node:assert/strict";
import test from "node:test";
import { enableTmuxMouse, MOUSE_KEYS, type TmuxRun } from "../src/tmux.ts";

function server() {
  const keys = new Map<string, string>(), options = new Map<string, string>();
  const calls: string[][] = [];
  const run: TmuxRun = async (all) => {
    calls.push(all);
    assert.deepEqual(all.slice(0, 2), ['-S', '/fixture/socket,with-comma']);
    const args = all.slice(2), command = args[0];
    if (command === 'list-keys') return keys.has(args[3]!) ?
      { code: 0, stdout: keys.get(args[3]!)! + '\n', stderr: '' } : { code: 1, stdout: '', stderr: 'unknown key' };
    if (command === 'show-options') return { code: 0, stdout: options.get(args.at(-1)!) ?? '', stderr: '' };
    if (command === 'bind-key') keys.set(args[3]!, JSON.stringify(args));
    else if (command === 'set-option') {
      if (args[1]!.includes('u')) options.delete(args.at(-1)!);
      else options.set(args.at(-2)!, args.at(-1)!);
    } else throw new Error('Unexpected tmux command: ' + args.join(' '));
    return { code: 0, stdout: '', stderr: '' };
  };
  return { run, calls, keys, options };
}
const env = { TMUX: '/fixture/socket,with-comma,100,2', TMUX_PANE: '%42' };

test('tmux forwards only extension-owned mouse panes; reload reuses exact managed keys', async () => {
  const f = server();
  const first = await enableTmuxMouse(env, f.run);
  assert.equal(first.enabled, true); assert.deepEqual(first.warnings, []);
  assert.equal(f.keys.size, MOUSE_KEYS.length);
  assert.ok([...f.keys.values()].every(value => value.includes('@pi-copy-links-input') && value.includes('mouse_any_flag')));
  assert.ok(f.options.has('@pi-copy-links-input'));
  await first.dispose(); assert.equal(f.options.has('@pi-copy-links-input'), false);
  const count = f.calls.filter(args => args[2] === 'bind-key').length;
  const second = await enableTmuxMouse(env, f.run); assert.deepEqual(second.warnings, []);
  assert.equal(f.calls.filter(args => args[2] === 'bind-key').length, count);
  await second.dispose();
  assert.equal(f.keys.size, MOUSE_KEYS.length, 'shared guarded keys are left inert, avoiding cross-session unbind races');
});

test('foreign key bindings are not overwritten, even if a managed receipt exists', async () => {
  const f = server(); const first = await enableTmuxMouse(env, f.run); await first.dispose();
  f.keys.set(MOUSE_KEYS[0]!, 'foreign custom binding');
  const second = await enableTmuxMouse(env, f.run);
  assert.deepEqual(second.warnings, [MOUSE_KEYS[0]]);
  assert.equal(f.keys.get(MOUSE_KEYS[0]!), 'foreign custom binding');
  await second.dispose();
});

test('pane marker cleanup restores prior ownership but never removes a later owner', async () => {
  const f = server(); f.options.set('@pi-copy-links-input', 'outer-owner');
  const first = await enableTmuxMouse(env, f.run); await first.dispose();
  assert.equal(f.options.get('@pi-copy-links-input'), 'outer-owner');
  const second = await enableTmuxMouse(env, f.run);
  f.options.set('@pi-copy-links-input', 'later-owner'); await second.dispose();
  assert.equal(f.options.get('@pi-copy-links-input'), 'later-owner');
});

test('non-tmux processes are untouched and command failures are not interpreted as missing keys', async () => {
  const f = server(); assert.equal((await enableTmuxMouse({}, f.run)).enabled, false); assert.equal(f.calls.length, 0);
  await assert.rejects(() => enableTmuxMouse(env, async () => ({ code: 2, stdout: '', stderr: 'server unavailable' })), /server unavailable/);
});

import assert from "node:assert/strict";
import { registerHooks } from "node:module";
import { afterEach, test } from "node:test";

// Actual extension, separate URL factories, inert host and helper only. No clipboard/Pi/GUI.
let hostUrl;
const childUrl = (origin) => 'data:text/javascript,' + encodeURIComponent(`
import { EventEmitter } from 'node:events';
export function spawn() {
  globalThis.__ownershipProbe(${JSON.stringify(origin)});
  const child = new EventEmitter();
  child.stdout = new EventEmitter(); child.stderr = new EventEmitter();
  child.stdout.destroy = child.stderr.destroy = () => {};
  child.kill = () => true;
  queueMicrotask(() => { child.emit('exit', 0); child.emit('close', 0); });
  return child;
}`);
registerHooks({ resolve(specifier, context, next) {
  if (context.parentURL?.includes('/pi-extension/index.ts?ownership-')) {
    if (specifier === '@earendil-works/pi-coding-agent' || specifier === '@earendil-works/pi-tui') return { url: hostUrl, shortCircuit: true };
    if (specifier === 'node:child_process') return { url: childUrl(context.parentURL.split('-').at(-1)), shortCircuit: true };
  }
  return next(specifier, context);
} });
const sinkKey = Symbol.for('pi-remote-clipboard:copy-sink');
const layerKey = Symbol.for('pi-remote-clipboard:owned-layer:v1');
const originalSink = Object.getOwnPropertyDescriptor(globalThis, sinkKey);
const envKeys = ['TMUX', 'TMUX_PANE', 'SSH_CONNECTION', 'SSH_CLIENT', 'MOSH_CONNECTION'];
const savedEnv = envKeys.map(k => process.env[k]);
let serial = 0;
let cleanups = [];
afterEach(async () => {
  for (const cleanup of cleanups.reverse()) await cleanup();
  cleanups = [];
  if (originalSink) Object.defineProperty(globalThis, sinkKey, originalSink); else delete globalThis[sinkKey];
  envKeys.forEach((k, i) => { if (savedEnv[i] === undefined) delete process.env[k]; else process.env[k] = savedEnv[i]; });
  delete globalThis.__ownershipProbe;
});
async function fixture(options = {}) {
  for (const k of envKeys) delete process.env[k];
  delete globalThis[sinkKey];
  hostUrl = 'data:text/javascript,' + encodeURIComponent(`export class InteractiveMode { async handleClipboardPaste() {} } export class TuiAltScreen { async copyTextToClipboard() {} } // ${++serial}`);
  const { InteractiveMode, TuiAltScreen } = await import(hostUrl);
  const targets = [[TuiAltScreen.prototype, 'copyTextToClipboard'], [InteractiveMode.prototype, 'handleClipboardPaste']];
  const calls = [];
  for (const [target, key] of targets) Object.defineProperty(target, key, {
    value: async function (...args) { calls.push({ key, receiver: this, args }); return true; },
    writable: true, configurable: options.configurable ?? true, enumerable: false,
  });
  if ('sink' in options) Object.defineProperty(globalThis, sinkKey, options.sink);
  const base = targets.map(([target, key]) => Object.getOwnPropertyDescriptor(target, key));
  const baseSink = Object.getOwnPropertyDescriptor(globalThis, sinkKey);
  const factories = await Promise.all(['a', 'b', 'c'].map(async name => (await import(`../pi-extension/index.ts?ownership-${serial}-${name}`)).default));
  const owners = factories.map(factory => {
    const events = new Map(); const notices = [];
    factory({ on: (key, fn) => events.set(key, fn), registerCommand() {}, registerShortcut() {} });
    const ctx = { mode: 'tui', ui: { notify: (...args) => notices.push(args) } };
    const owner = { notices, ctx, start: () => events.get('session_start')({}, ctx), shutdown: () => events.get('session_shutdown')({}, ctx) };
    cleanups.push(owner.shutdown); return owner;
  });
  return { targets, calls, base, baseSink, owners,
    snapshot: () => [...targets.map(([t,k]) => Object.getOwnPropertyDescriptor(t,k)), Object.getOwnPropertyDescriptor(globalThis,sinkKey)],
    values: () => [...targets.map(([t,k]) => Object.getOwnPropertyDescriptor(t,k)?.value), Object.getOwnPropertyDescriptor(globalThis,sinkKey)?.value],
  };
}
const orders = [[0,1,2],[0,2,1],[1,0,2],[1,2,0],[2,0,1],[2,1,0]];
for (const order of [[0,1], [1,0], ...orders]) test(`exact methods/sink restoration after shutdown ${order.join('→')}`, async () => {
  const f = await fixture();
  const snapshots = [];
  for (const i of order.map((_,i) => i)) { f.owners[i].start(); snapshots.push(f.snapshot()); }
  const live = new Set(order);
  for (const i of order) {
    await f.owners[i].shutdown(); live.delete(i);
    assert.deepEqual(f.snapshot(), live.size ? snapshots[Math.max(...live)] : [...f.base, f.baseSink]);
  }
  for (const owner of f.owners) await owner.shutdown();
  assert.deepEqual(f.snapshot(), [...f.base, f.baseSink]);
});
test('B fallback bypasses still-live A for both methods, preserving receiver and all arguments', async () => {
  const f = await fixture();
  let probe = 'A'; const probes = [];
  globalThis.__ownershipProbe = origin => probes.push([probe, origin]);
  process.env.TMUX = 'synthetic'; process.env.TMUX_PANE = '%synthetic';
  f.owners[0].start(); const capturedA = f.values(); f.owners[1].start();
  probe = 'B'; const receiver = {};
  for (const method of f.values().slice(0,2)) await method.call(receiver, 'text', 42);
  assert.deepEqual(probes, [['B', 'b'], ['B', 'b']], 'one B probe per method; zero A probes');
  assert.equal(f.calls.length, 2);
  for (const call of f.calls) { assert.equal(call.receiver, receiver); assert.deepEqual(call.args, ['text', 42]); }
  probe = 'captured A'; await capturedA[0].call(receiver, 'A');
  assert.deepEqual(probes.at(-1), ['captured A', 'a'], 'captured live A uses its origin, never B');
  assert.equal(f.calls.length, 3, 'captured live A still executes its own behavior');
});
test('retiring A at the old added-await boundary cannot cancel valid B native fallback', async () => {
  const f = await fixture(); f.owners[0].start(); f.owners[1].start();
  const pending = f.values()[1].call({});
  queueMicrotask(() => { void f.owners[0].shutdown(); });
  await pending;
  assert.equal(f.calls.length, 1);
});
test('retired captured functions stay inert, stale sink rejects AbortError before false', async () => {
  const f = await fixture(); f.owners[0].start(); const a = f.values(); f.owners[1].start();
  await f.owners[0].shutdown();
  await a[0].call({ flash() { assert.fail('stale feedback'); } }, 'x');
  await a[1].call({ showError() { assert.fail('stale feedback'); } });
  await assert.rejects(a[2]('x'), { name: 'AbortError' });
  assert.deepEqual(f.calls, []);
});
test('repeat start, TUI→nonTUI, repeat shutdown release every publication', async () => {
  const f = await fixture(); const a = f.owners[0];
  a.start(); const stale = f.values(); a.start();
  await assert.rejects(stale[2]('x'), { name: 'AbortError' });
  a.ctx.mode = 'print'; a.start();
  assert.deepEqual(f.snapshot(), [...f.base, f.baseSink]);
  await a.shutdown(); await a.shutdown();
  assert.deepEqual(f.snapshot(), [...f.base, f.baseSink]);
});
for (const value of [undefined, 7, () => 'foreign']) test(`prior sink data value ${typeof value} restores exact flags`, async () => {
  const f = await fixture({ sink: { value, writable: true, enumerable: false, configurable: true } });
  f.owners[0].start(); f.owners[1].start();
  await f.owners[0].shutdown(); await f.owners[1].shutdown();
  assert.deepEqual(f.snapshot(), [...f.base, f.baseSink]);
});
test('writable nonconfigurable methods preserve flags and restore', async () => {
  const f = await fixture({ configurable: false });
  f.owners[0].start(); f.owners[1].start();
  assert.equal(f.snapshot()[0].configurable, false); assert.equal(f.snapshot()[0].enumerable, false);
  await f.owners[0].shutdown(); await f.owners[1].shutdown();
  assert.deepEqual(f.snapshot(), [...f.base, f.baseSink]);
});
for (const kind of ['accessor','readonly']) test(`unsupported ${kind} installation never invokes accessors`, async () => {
  const f = await fixture(); let gets = 0; let sets = 0;
  const descriptor = kind === 'accessor' ? { get() { gets++; return () => {}; }, set() { sets++; }, configurable: true } : { value: () => {}, writable: false, configurable: true };
  for (const [t,k] of [...f.targets, [globalThis,sinkKey]]) Object.defineProperty(t,k,descriptor);
  const before = f.snapshot(); f.owners[0].start(); await f.owners[0].shutdown();
  assert.deepEqual(f.snapshot(), before); assert.equal(gets,0); assert.equal(sets,0);
  assert.ok(f.owners[0].notices.length > 0);
});
for (const kind of ['value','accessor','flags','deletion']) test(`foreign ${kind} at uninstall stays untouched without getter calls`, async () => {
  const f = await fixture(); f.owners[0].start(); let gets = 0;
  for (const [t,k] of [...f.targets, [globalThis,sinkKey]]) {
    if (kind === 'deletion') delete t[k];
    else Object.defineProperty(t,k, kind === 'value' ? { ...Object.getOwnPropertyDescriptor(t,k), value: () => 'foreign' } : kind === 'flags' ? { ...Object.getOwnPropertyDescriptor(t,k), enumerable: !Object.getOwnPropertyDescriptor(t,k).enumerable } : { get() { gets++; throw Error('foreign getter'); }, configurable: true });
  }
  const before = f.snapshot(); await f.owners[0].shutdown();
  assert.deepEqual(f.snapshot(), before); assert.equal(gets,0);
});
test('live B record links contain no retired A callable or Owner payload', async () => {
  const f = await fixture(); f.owners[0].start(); const a = f.values(); f.owners[1].start(); const b = f.values();
  const aRecords = a.map(fn => Object.getOwnPropertyDescriptor(fn, layerKey)?.value);
  assert.ok(aRecords.every(Boolean), 'each publication has shared URL-safe metadata');
  await f.owners[0].shutdown();
  const seen = new Set();
  function inspect(value) {
    if (value === null || (typeof value !== 'object' && typeof value !== 'function') || seen.has(value)) return;
    seen.add(value);
    assert.ok(!a.includes(value), 'no retired callable reachable from B metadata');
    assert.ok(!Object.hasOwn(value,'helpers') && !Object.hasOwn(value,'operations'), 'no Owner payload');
    const descriptors = Object.getOwnPropertyDescriptors(value);
    for (const key of Reflect.ownKeys(descriptors)) if ('value' in descriptors[key]) inspect(descriptors[key].value);
  }
  for (const fn of b) inspect(Object.getOwnPropertyDescriptor(fn,layerKey)?.value);
  for (const record of aRecords) assert.equal(record.callable, undefined);
});
test('metadata getters on opaque foreign functions are never evaluated', async () => {
  const f = await fixture(); let gets=0;
  for (const [t,k] of f.targets) Object.defineProperty(t[k],layerKey,{get(){gets++;throw Error('metadata getter');}});
  f.owners[0].start(); f.owners[1].start(); await f.values()[1].call({});
  await f.owners[0].shutdown(); await f.owners[1].shutdown();
  assert.equal(gets,0); assert.deepEqual(f.snapshot(), [...f.base,f.baseSink]);
});
for (const order of [[0,1],[1,0]]) test(`reverse URL activation also restores correctly ${order}`, async () => {
  const f = await fixture();
  f.owners[1].start(); const first = f.snapshot(); f.owners[0].start(); const second = f.snapshot();
  const live = new Set([0,1]);
  for (const id of order) {
    await f.owners[id].shutdown(); live.delete(id);
    assert.deepEqual(f.snapshot(), live.size ? (live.has(0) ? second : first) : [...f.base, f.baseSink]);
  }
});
for (const slot of ['copyTextToClipboard','handleClipboardPaste',sinkKey]) test(`attach-then-throw at ${String(slot)} rolls back all known publications`, async () => {
  const f = await fixture(); const define = Object.defineProperty;
  let failed = false;
  try {
    Object.defineProperty = function (target, key, descriptor) {
      const result = define(target,key,descriptor);
      if (!failed && key === slot) { failed = true; throw Error('publication failure'); }
      return result;
    };
    assert.throws(() => f.owners[0].start(), /publication failure/);
  } finally { Object.defineProperty = define; }
  assert.deepEqual(f.snapshot(), [...f.base,f.baseSink]);
  await f.owners[0].shutdown();
});
test('all failed uninstalls are attempted and the first error survives', async () => {
  const f = await fixture(); f.owners[0].start();
  const get = Object.getOwnPropertyDescriptor; const failures = [];
  let shutdown;
  try {
    Object.getOwnPropertyDescriptor = function (target,key) {
      if (f.targets.some(([t,k]) => t===target && k===key) || (target===globalThis && key===sinkKey)) {
        failures.push(key); throw Error(`failure ${String(key)}`);
      }
      return get(target,key);
    };
    shutdown = f.owners[0].shutdown();
  } finally { Object.getOwnPropertyDescriptor = get; }
  await assert.rejects(shutdown, /failure copyTextToClipboard/);
  assert.deepEqual(failures, ['copyTextToClipboard','handleClipboardPaste',sinkKey]);
  await f.owners[0].shutdown();
});

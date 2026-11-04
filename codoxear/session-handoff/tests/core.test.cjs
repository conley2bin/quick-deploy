const { test } = require('node:test');
const assert = require('node:assert/strict');
const Core = require('../web/core.js');
const rid = '12345678-1234-4123-8123-123456789012', oid = '87654321-1234-4123-8123-123456789012';
const ridB = '22345678-1234-4123-8123-123456789012', oidB = '97654321-1234-4123-8123-123456789012';
const op = (status = 'recovering', extra = {}) => ({ id: oid, request_id: rid, source_session_id: 'source', target_session_id: 'target', status, message: '合成状态', reviewed: false, can_continue_unreviewed: false, ...extra });
const sourceKey = source => 'key:source:' + encodeURIComponent(source);
function rig(post, raw, shared) {
  const values = shared || new Map(raw ? [['key', raw]] : []), calls = [], renders = [];
  const storage = { getItem: k => values.get(k), setItem: (k, v) => values.set(k, v), removeItem: k => values.delete(k), get length() { return values.size; }, key: i => [...values.keys()][i] };
  let seq = 0;
  const c = Core.createController({ storage, key: 'key', uuid: () => seq++ ? ridB : rid, changed: s => renders.push(s), post: async (route, body) => { calls.push({ route, body }); return post(route, body); } });
  c.select('source');
  return { c, values, calls, storage, renders };
}
test('regression: one source reference must not globally suppress another start', async () => {
  const calls = [], values = new Map();
  const c = Core.createController({ key: 'key', storage: { getItem: k => values.get(k), setItem: (k,v) => values.set(k,v) }, uuid: () => rid,
    post: async (route, body) => { calls.push(body); return { operation: op('recovering', { source_session_id: body.source_session_id }) }; } });
  await c.start('source'); await c.start('other'); assert.equal(calls.length, 2);
});
test('A pending start never blocks B; late A cannot render or replace B', async () => {
  let resolveA;
  const r = rig((route, body) => body.source_session_id === 'source' ? new Promise(resolve => resolveA = resolve) : { operation: op('recovering', { id: oidB, request_id: ridB, source_session_id: 'other' }) });
  const a = r.c.start('source', 'private A'); r.c.select('other');
  assert.equal(r.c.view().busy, false); await r.c.start('other', 'private B');
  assert.equal(r.calls.length, 2); assert.notEqual(r.calls[0].body.request_id, r.calls[1].body.request_id);
  assert.equal(r.c.view().op.id, oidB); const rendered = r.renders.length;
  resolveA({ operation: op() }); await a;
  assert.equal(r.c.view().op.id, oidB); assert.equal(r.renders.length, rendered);
  r.c.select('source'); assert.equal(r.c.view().op.id, oid);
  assert.equal(r.values.size, 2); assert(![...r.values.values()].join('').includes('private'));
});
test('late A success cannot clear B unknown-start error or recovery UUID', async () => {
  let resolveA;
  const r = rig((route, body) => body.source_session_id === 'source' ? new Promise(resolve => resolveA = resolve) : Promise.reject(Error('B offline')));
  const a = r.c.start('source'); r.c.select('other'); await r.c.start('other');
  const before = r.c.view(), persisted = r.values.get(sourceKey('other'));
  resolveA({ operation: op() }); await a;
  assert.equal(r.c.view().error, before.error); assert.match(r.c.view().error, /结果未知/);
  assert.equal(r.values.get(sourceKey('other')), persisted); await r.c.start('other'); assert.equal(r.calls.length, 2);
});
test('prefix is from own namespace script and rejects cross origin', () => {
  assert.equal(Core.namespace('https://x/nested/__session_handoff__/a.hash.js', 'https://x'), 'https://x/nested/__session_handoff__/');
  assert.throws(() => Core.namespace('https://evil/__session_handoff__/asset.js', 'https://x'));
  assert.throws(() => Core.namespace('https://x/__owner_groups__/asset.js', 'https://x'));
});
test('hash selection and navigation preserve opaque identifiers and other hash params', () => {
  assert.equal(Core.selected('#session=a%2Fb%20%26%E4%B8%AD'), 'a/b &中');
  for (const hash of ['', '#session=', '#session=a&session=b', '#session=%00', '#session=' + 'x'.repeat(513)]) assert.equal(Core.selected(hash), null);
  const u = new URL(Core.targetURL('https://x/p/?q=1#other=yes&session=old', 'new/&中'));
  assert.equal(Core.selected(u.hash), 'new/&中'); assert.equal(new URLSearchParams(u.hash.slice(1)).get('other'), 'yes');
});
test('multiple clicks produce one mutation and source-associated minimal reference persists', async () => {
  let resolve; const r = rig(() => new Promise(r => resolve = r));
  const first = r.c.start('source', 'private note'); await r.c.start('source');
  assert.equal(r.calls.length, 1); assert(!r.values.get(sourceKey('source')).includes('private'));
  resolve({ operation: op() }); await first;
  assert.deepEqual(JSON.parse(r.values.get(sourceKey('source'))), { operation_id: oid, source_session_id: 'source' });
});
test('uncertain start keeps same UUID on explicit recovery; no replay on reload; B allowed', async () => {
  const r = rig(() => { throw Error('offline'); }); await r.c.start('source', 'not retained');
  assert.match(r.c.view().error, /结果未知/);
  const next = rig((route, body) => ({ operation: body.source_session_id === 'other' ? op('recovering', { id: oidB, source_session_id: 'other' }) : op() }), null, r.values);
  assert.equal(next.calls.length, 0); await next.c.start('source'); assert.equal(next.calls.length, 0);
  next.c.select('other'); await next.c.start('other'); assert.equal(next.calls.length, 1);
  next.c.select('source'); await next.c.recover(); assert.deepEqual(next.calls[1], { route: 'start', body: { request_id: rid, source_session_id: 'source' } });
});
test('server coalesces another active request for the same source', async () => {
  const r = rig(() => ({ operation: op('recovering', { request_id: oid }) }));
  await r.c.start('source'); assert.equal(r.c.view().error, ''); assert.equal(r.c.view().op.id, oid);
});
test('status validates both source and operation identities', async () => {
  for (const status of Object.keys(Core.labels)) assert.equal(Core.operation(op(status)).status, status);
  assert.throws(() => Core.operation(op('invented')));
  for (const extra of [{ id: rid }, { source_session_id: 'other' }]) {
    const r = rig(() => ({ operation: op('ready', extra) }), null, new Map([[sourceKey('source'), JSON.stringify({ operation_id: oid, source_session_id: 'source' })]]));
    await r.c.poll(); assert.match(r.c.view().error, /身份不匹配/); assert.equal(r.c.view().op, null);
  }
});
test('explicit unreviewed continuation is one-shot across reload', async () => {
  let fail = false;
  const post = route => { if (route === 'continue' && fail) throw Error('offline'); return { operation: op('unreviewed', { can_continue_unreviewed: true }) }; };
  const r = rig(post); await r.c.start('source'); fail = true; await r.c.continueUnreviewed();
  assert(r.c.view().uncertainContinue); await r.c.continueUnreviewed(); assert.equal(r.calls.length, 2);
  const next = rig(post, null, r.values); await next.c.poll(); await next.c.continueUnreviewed();
  assert.equal(next.calls.length, 1); assert(next.c.view().uncertainContinue);
  for (const extra of [{ can_continue_unreviewed: false }, { target_session_id: null }]) {
    const denied = rig(() => ({ operation: op('unreviewed', extra) })); await denied.c.start('source'); await denied.c.continueUnreviewed(); assert.equal(denied.calls.length, 1);
  }
});
test('definitive rejection only releases its own source', async () => {
  const r = rig((route, body) => { if (body.source_session_id === 'other') throw Object.assign(Error('not Pi'), { definitive: true }); return { operation: op() }; });
  await r.c.start('source'); r.c.select('other'); await r.c.start('other'); assert.equal(r.c.view().reference, null);
  r.c.select('source'); assert.equal(r.c.view().op.id, oid); assert(r.values.has(sourceKey('source')));
});
test('storage refusal prevents mutation without changing another source', async () => {
  assert.equal(Core.retained('{bad'), null); assert.equal(Core.retained(JSON.stringify({ operation_id: 'x'.repeat(10000) })), null);
  const r = rig(() => ({ operation: op() })); await r.c.start('source'); r.c.select('other');
  r.storage.setItem = () => { throw Error('quota'); }; await r.c.start('other');
  assert.equal(r.calls.length, 1); assert.match(r.c.view().error, /未发送/); assert(r.values.has(sourceKey('source')));
});
test('cleanup ignores late replies and clears all feature identities on logout', async () => {
  let resolve; const r = rig(() => new Promise(r => resolve = r));
  r.values.set(sourceKey('unloaded'), JSON.stringify({ operation_id: oidB, source_session_id: 'unloaded' }));
  const task = r.c.start('source'); r.c.stop(true); resolve({ operation: op() }); await task;
  assert.equal(r.values.size, 0); assert.equal(r.c.view().op, null); await r.c.recover(); assert.equal(r.calls.length, 1);
});
test('unknown A holds same-source lease but allows B; forget only terminal selected record', async () => {
  const r = rig((route, body) => ({ operation: body.source_session_id === 'other' ? op('ready', { id: oidB, source_session_id: 'other' }) : op('creation_unknown') }));
  await r.c.start('source'); r.c.forget(); await r.c.start('source'); assert.equal(r.calls.length, 1);
  r.c.select('other'); await r.c.start('other'); r.c.forget(); assert(!r.values.has(sourceKey('other'))); assert(r.values.has(sourceKey('source')));
});
test('request UUID works without secure-context randomUUID and is version 4', () => {
  assert.equal(Core.requestUUID({ getRandomValues: a => a.fill(255) }), 'ffffffff-ffff-4fff-bfff-ffffffffffff');
});
test('in-flight status cannot erase a same-operation continuation persisted by another tab', async () => {
  let resolveStatus;
  const r = rig(route => route === 'status' ? new Promise(resolve => resolveStatus = resolve) : { operation: op('unreviewed', { can_continue_unreviewed: true }) });
  await r.c.start('source'); const polling = r.c.poll();
  r.values.set(sourceKey('source'), JSON.stringify({ operation_id: oid, source_session_id: 'source', pending_action: 'continue' }));
  resolveStatus({ operation: op('unreviewed', { can_continue_unreviewed: true }) }); await polling;
  await r.c.continueUnreviewed(); assert.equal(r.calls.filter(c=>c.route==='continue').length,0);
  assert.equal(JSON.parse(r.values.get(sourceKey('source'))).pending_action,'continue');
});
test('accepted continuation without state advancement cannot be sent twice', async () => {
  const r = rig(() => ({ operation: op('unreviewed', { can_continue_unreviewed: true }) }));
  await r.c.start('source'); await r.c.continueUnreviewed(); await r.c.continueUnreviewed();
  assert.equal(r.calls.filter(c => c.route === 'continue').length, 1);
  assert.equal(JSON.parse(r.values.get(sourceKey('source'))).pending_action, 'continue');
});
test('source/note bounds and invalid targets reject before action/navigation', async () => {
  const r = rig(() => ({ operation: op() }));
  await r.c.start('x'.repeat(513)); await r.c.start('source', 'x'.repeat(1001));
  assert.equal(r.calls.length, 0); assert.throws(() => Core.operation(op('ready', { target_session_id: 'source' })));
});
test('different-source tabs persist without map lost updates; refresh restores both', async () => {
  const shared = new Map(), a = rig(() => ({ operation: op() }), null, shared), b = rig(() => ({ operation: op('ready', { id: oidB, source_session_id: 'other' }) }), null, shared);
  await a.c.start('source'); b.c.select('other'); await b.c.start('other');
  const refresh = rig((route, body) => ({ operation: body.operation_id === oid ? op() : op('ready', { id: oidB, source_session_id: 'other' }) }), null, shared);
  await refresh.c.poll(); assert.equal(refresh.c.view().op.id, oid);
  refresh.c.select('other'); await refresh.c.poll(); assert.equal(refresh.c.view().op.id, oidB);
  refresh.c.forget(); assert(shared.has(sourceKey('source'))); assert(!shared.has(sourceKey('other')));
});
test('legacy pending source moves after successful write, never replays automatically', async () => {
  const raw = JSON.stringify({ request_id: rid, source_session_id: 'source' }), r = rig(() => ({ operation: op() }), raw);
  await r.c.migrateLegacy(); assert.equal(r.calls.length, 0); assert(!r.values.has('key')); assert(r.values.has(sourceKey('source')));
  const failed = rig(() => ({ operation: op() }), raw); failed.storage.setItem = () => { throw Error('quota'); };
  await failed.c.migrateLegacy(); assert.equal(failed.values.get('key'), raw);
  await failed.c.start('source'); assert.equal(failed.calls.length, 0); // Same A remains locked on failed migration.
  assert.equal(failed.values.get('key'), raw);
});
test('unsourced legacy operation asynchronously adopts returned A while B selected', async () => {
  let resolve; const raw = JSON.stringify({ operation_id: oid, pending_action: 'continue' });
  const r = rig(() => new Promise(r => resolve = r), raw); r.c.select('other'); const migration = r.c.migrateLegacy();
  assert.equal(r.c.view().reference, null); assert.equal(r.calls[0].route, 'status');
  resolve({ operation: op('unreviewed', { can_continue_unreviewed: true }) }); await migration;
  assert.equal(r.c.view().reference, null); assert.equal(r.values.has('key'), false);
  r.c.select('source'); assert.equal(r.c.view().op.source_session_id, 'source'); assert(r.c.view().uncertainContinue);
  await r.c.continueUnreviewed(); assert.equal(r.calls.length, 1);
});
test('failed legacy lookup/write keeps original and does not block unrelated B', async () => {
  const raw = JSON.stringify({ operation_id: oid });
  for (const writeFailure of [false, true]) {
    const r = rig(route => { if (route === 'status' && !writeFailure) throw Error('offline'); return { operation: op() }; }, raw);
    r.c.select('other'); if (writeFailure) r.storage.setItem = () => { throw Error('quota'); };
    await r.c.migrateLegacy(); assert.equal(r.values.get('key'), raw); assert.equal(r.c.view().reference, null);
    assert.match(r.c.view().legacyError, /旧接力/);
  }
});
test('migration never overwrites a different existing source reference', async () => {
  const raw = JSON.stringify({ operation_id: oid }), shared = new Map([['key', raw], [sourceKey('source'), JSON.stringify({ operation_id: oidB, source_session_id: 'source' })]]);
  const r = rig(() => ({ operation: op() }), null, shared); await r.c.migrateLegacy();
  assert.equal(shared.get('key'), raw); assert.equal(JSON.parse(shared.get(sourceKey('source'))).operation_id, oidB);
});

for (const stale of ['recovering', 'ready']) test('old ' + stale + ' observation cannot release another tab unknown Continue; fresh ready can', async () => {
  const shared = new Map([[sourceKey('source'), JSON.stringify({ operation_id: oid, source_session_id: 'source' })]]);
  let release;
  const old = rig(() => new Promise(resolve => release = resolve), null, shared);
  const held = old.c.poll();
  const post = route => { if (route === 'continue') throw Error('lost Continue response'); return { operation: op('unreviewed', { can_continue_unreviewed: true }) }; };
  const actor = rig(post, null, shared); await actor.c.poll(); await actor.c.continueUnreviewed();
  assert.equal(JSON.parse(shared.get(sourceKey('source'))).pending_action, 'continue');
  release({ operation: op(stale) }); await held;
  assert.equal(JSON.parse(shared.get(sourceKey('source'))).pending_action, 'continue');
  assert(old.c.view().uncertainContinue);
  const reload = rig(post, null, shared); await reload.c.poll(); await reload.c.continueUnreviewed();
  assert.equal([...actor.calls, ...reload.calls].filter(c => c.route === 'continue').length, 1);
  assert(reload.c.view().uncertainContinue);
  const confirmed = rig(() => ({ operation: op('ready') }), null, shared); await confirmed.c.poll();
  assert.equal(confirmed.c.view().uncertainContinue, false); confirmed.c.forget();
  assert(!shared.has(sourceKey('source')));
});

test('old responses and definitive errors cannot replace or resurrect current same-source ownership', async () => {
  for (const outcome of ['ready', 'definitive-status', 'definitive-start']) {
    for (const replace of [false, true]) {
      const initial = outcome === 'definitive-start' ? {} : { operation_id: oid, source_session_id: 'source' };
      const shared = new Map(Object.keys(initial).length ? [[sourceKey('source'), JSON.stringify(initial)]] : []);
      let release, reject;
      const old = rig(() => new Promise((yes, no) => { release = yes; reject = no; }), null, shared);
      const held = outcome === 'definitive-start' ? old.c.start('source') : old.c.poll();
      const actor = rig(route => ({ operation: route === 'start' ? op('recovering', { id: oidB, request_id: ridB }) : op('ready') }), null, shared);
      if (outcome === 'definitive-start') shared.delete(sourceKey('source')); // A later tab explicitly retired its failed start.
      else { await actor.c.poll(); actor.c.forget(); }
      if (replace) await actor.c.start('source');
      const expected = shared.get(sourceKey('source'));
      if (outcome === 'ready') release({ operation: op('ready') });
      else reject(Object.assign(Error('rejected old request'), { definitive: true }));
      await held;
      assert.equal(shared.get(sourceKey('source')), expected, outcome + ', replacement=' + replace);
      assert.equal(old.c.view().reference?.operation_id, replace ? oidB : undefined);
      assert.equal(old.c.view().op, null); assert.equal(old.c.view().busy, false);
      assert.equal(old.c.view().error, '');
    }
  }
});

test('a renewed Continue marker cannot be resolved by an observation of the previous marker', async () => {
  for (const kind of ['status', 'continue']) {
    const shared = new Map([[sourceKey('source'), JSON.stringify({ operation_id: oid, source_session_id: 'source',
      ...(kind === 'status' ? { pending_action: 'continue', pending_id: rid } : {}) })]]);
    let release, reject;
    const old = rig(route => route === 'status' && kind === 'continue'
      ? { operation: op('unreviewed', { can_continue_unreviewed: true }) }
      : new Promise((yes, no) => { release = yes; reject = no; }), null, shared);
    if (kind === 'continue') await old.c.poll();
    const held = kind === 'status' ? old.c.poll() : old.c.continueUnreviewed();
    // Another tab definitively retires the old intent and persists a new one.
    shared.set(sourceKey('source'), JSON.stringify({ operation_id: oid, source_session_id: 'source', pending_action: 'continue', pending_id: ridB }));
    if (kind === 'status') release({ operation: op('ready') });
    else reject(Object.assign(Error('old Continue rejected'), { definitive: true }));
    await held;
    const saved = JSON.parse(shared.get(sourceKey('source')));
    assert.equal(saved.pending_action, 'continue'); assert.equal(saved.pending_id, ridB);
  }
});
test('fresh unknown delivery status retains explicit Continue uncertainty', async () => {
  for (const status of ['unreviewed', 'creation_unknown', 'delivery_unknown', 'interrupted']) {
    const shared = new Map([[sourceKey('source'), JSON.stringify({ operation_id: oid, source_session_id: 'source', pending_action: 'continue' })]]);
    const r = rig(() => ({ operation: op(status) }), null, shared); await r.c.poll(); r.c.forget();
    assert(r.c.view().uncertainContinue); assert.equal(JSON.parse(shared.get(sourceKey('source'))).pending_action, 'continue');
  }
});

const chosenTarget = () => ({ model_provider: 'provider-b', model: 'shared', reasoning_effort: 'xhigh' });
test('explicit target is frozen and persisted before POST, then recovered unchanged after reload', async () => {
  const target = chosenTarget(); let release;
  const r = rig((route, body) => {
    assert.deepEqual(JSON.parse(r.values.get(sourceKey('source'))), { source_session_id: 'source', request_id: rid, target: chosenTarget() });
    return new Promise(resolve => release = resolve);
  });
  const pending = r.c.start('source', 'private', target);
  target.model = 'changed-after-click';
  assert.deepEqual(r.calls[0].body.target, chosenTarget());
  release(Promise.reject(Error('offline'))); await pending;
  const next = rig(() => ({ operation: op() }), null, r.values);
  assert.equal(next.calls.length, 0); await next.c.start('source', '', { ...chosenTarget(), model: 'another' });
  assert.equal(next.calls.length, 0); await next.c.recover();
  assert.deepEqual(next.calls[0].body, { source_session_id: 'source', request_id: rid, target: chosenTarget() });
});
test('malformed pending target keeps recovery lock and raw record without POST or inheritance', async () => {
  for (const target of [null, {}, { ...chosenTarget(), extra: true }, { ...chosenTarget(), reasoning_effort: 'HIGH' }, { ...chosenTarget(), model: ' leading' }]) {
    const raw = JSON.stringify({ source_session_id: 'source', request_id: rid, target });
    const r = rig(() => ({ operation: op() }), null, new Map([[sourceKey('source'), raw]]));
    await r.c.start('source'); await r.c.recover();
    assert.equal(r.calls.length, 0); assert.equal(r.values.get(sourceKey('source')), raw);
    assert.equal(r.c.view().invalidReference, true); assert.match(r.c.view().error, /恢复/);
  }
});
test('invalid explicit start values never persist or POST; legacy start has no target key', async () => {
  for (const target of [null, {}, { ...chosenTarget(), model: 'DEFAULT' }, { ...chosenTarget(), reasoning_effort: 'High' }, { ...chosenTarget(), model: 'x\u007f' }]) {
    const r = rig(() => ({ operation: op() })); await r.c.start('source', '', target);
    assert.equal(r.calls.length, 0); assert.equal(r.values.size, 0); assert.match(r.c.view().error, /模型/);
  }
  const legacy = rig(() => ({ operation: op() })); await legacy.c.start('source');
  assert.equal(Object.hasOwn(legacy.calls[0].body, 'target'), false);
});
test('target-only cross-tab replacement cannot accept old response under same request UUID', async () => {
  let release; const r = rig(() => new Promise(resolve => release = resolve));
  const pending = r.c.start('source', '', chosenTarget());
  const replacement = { source_session_id: 'source', request_id: rid, target: { ...chosenTarget(), model: 'replacement' } };
  r.values.set(sourceKey('source'), JSON.stringify(replacement)); release({ operation: op() }); await pending;
  assert.equal(r.c.view().op, null); assert.deepEqual(r.c.view().reference, replacement);
});
test('optional frozen settings and asymmetric target checks validate strictly without runtime claims', () => {
  const target_settings = { ...chosenTarget(), origin: 'explicit' };
  const target_check = { result: 'effort_differs', stage: 'launch', observed: { ...chosenTarget(), reasoning_effort: 'high' } };
  assert.deepEqual(Core.operation(op('ready', { target_settings, target_check })).target_check, target_check);
  assert.equal(Core.operation(op()).target_settings, undefined);
  for (const value of [null, { ...target_settings, origin: 'current' }, { ...target_settings, model: '' }, { ...target_settings, extra: 1 }])
    assert.throws(() => Core.operation(op('ready', { target_settings: value })), /格式/);
  for (const value of [null, { ...target_check, stage: 'status' }, { ...target_check, result: 'verified' }, { ...target_check, observed: {} }, { ...target_check, extra: 1 }])
    assert.throws(() => Core.operation(op('ready', { target_check: value })), /格式/);
});

const { test } = require('node:test');
const assert = require('node:assert/strict');
const { actualNative, catalog, fakeDOM } = require('./target-model-fixture.cjs');
const Target = require('../web/target-model.js');
async function rig(t) {
  const dom = fakeDOM(), writes = [];
  const native = await actualNative(t, { getItem: () => null, setItem: (...v) => writes.push(v), removeItem: (...v) => writes.push(v) });
  const menuHost = dom.doc.createElement('div'); dom.doc.body.append(menuHost);
  const d = { choice: null }, data = catalog(); let current = true, changes = 0, action = () => current;
  const field = Target.createTargetModelField({ ...dom, native, menuHost, actionCurrent: () => action(), onChange: () => changes++ });
  t.after(() => field.dispose()); field.bind({ draft: d, data, source: 'A', seq: 1, visible: true });
  const find = id => dom.doc.getElementById(id);
  const rows = () => find('handoffModelMenu').children.filter(n => n.role === 'option');
  const choose = (provider, model) => { find('handoffModelInput').click(); rows().find(n => n.title === `${model} · ${provider}`).click(); };
  return { ...dom, native, d, data, field, find, rows, choose, writes, stale: () => current = false, guard: fn => action = fn, changes: () => changes };
}
test('actual native exported controller owns grouping/search/display/parsing, not a cloned algorithm', async t => {
  const r = await rig(t), input = r.find('handoffModelInput'); input.click();
  const ctl = r.native.controllers[0];
  assert.deepEqual(r.rows().slice(1).map(n => n.title), ctl.filteredNewSessionModelOptions().map(n => n.displayText));
  assert(r.rows().length > 12); assert(!r.rows().some(n => n.title.includes('ghost')));
  for (const query of ['shared', 'provider-b/', 'model']) {
    input.value = query; input.fire('input');
    assert.deepEqual(r.rows().map(n => n.title), ctl.filteredNewSessionModelOptions().map(n => n.displayText));
    assert(r.rows().length <= 12);
  }
  input.value = ''; input.fire('input'); r.choose('provider-b', 'shared');
  assert.deepEqual(ctl.parseNewSessionProviderModelInput(), { model: 'shared', providerChoice: 'provider-b', providerError: '', providerAbsent: false });
  assert.deepEqual(r.field.target(), { model_provider: 'provider-b', model: 'shared', reasoning_effort: 'high' });
  assert.match(r.find('handoffModelSummary').textContent, /shared · provider-b · high/);
  assert.deepEqual(r.writes, []);
});
test('native effort resolution is visible, restricted choices are native, and no preference methods run', async t => {
  const r = await rig(t); r.choose('provider-a', 'shared');
  r.find('handoffReasoningBtn').click();
  r.find('handoffReasoningMenu').children.find(n => n.title === 'xhigh').click();
  r.choose('provider-b', 'restricted');
  assert.equal(r.d.choice.reasoning_effort, 'high'); assert.match(r.find('handoffModelSummary').textContent, /xhigh.*high/);
  r.find('handoffReasoningBtn').click();
  assert.deepEqual(r.find('handoffReasoningMenu').children.map(n => n.title), r.native.controllers[0].currentReasoningChoices());
  assert.deepEqual(r.writes, []);
});
test('dirty queries, stale pairs/effort and unavailable catalog never silently become inherit', async t => {
  const r = await rig(t); r.choose('provider-b', 'shared'); const frozen = r.field.target();
  r.find('handoffModelInput').value = 'not committed'; r.find('handoffModelInput').fire('input');
  assert.match(r.field.blockReason(), /列表/);
  r.find('handoffInheritModel').click(); assert.equal(r.field.target(), undefined); assert.equal(r.field.blockReason(), '');
  r.choose('provider-b', 'shared'); r.data.new_session_defaults.backends.pi.provider_models['provider-b'] = ['restricted'];
  r.field.bind({ draft: r.d, data: r.data, source: 'A', seq: 2, visible: true });
  assert.match(r.field.blockReason(), /可用/); assert.deepEqual(r.field.target(), frozen);
  r.field.bind({ draft: r.d, data: null, source: 'A', seq: 3, visible: true, error: 'offline' });
  assert.match(r.field.blockReason(), /读取/); assert.deepEqual(r.field.target(), frozen);
  r.find('handoffInheritModel').click(); assert.equal(r.field.blockReason(), '');
  assert.equal(r.find('handoffModelInput').disabled, true);
});
test('warnings.pi presence and synthetic default are unavailable; no global/catalog reconstruction', async t => {
  const r = await rig(t);
  for (const piWarning of ['', null, false, 'reader failed']) {
    const data = catalog(); data.new_session_defaults.warnings = { pi: piWarning };
    r.field.bind({ draft: r.d, data, source: 'A', seq: 2, visible: true });
    assert.equal(r.find('handoffModelInput').disabled, true); assert.equal(r.field.blockReason(), '');
  }
  const data = catalog(); data.new_session_defaults.backends.pi.provider_models = { 'provider-a': ['DEFAULT'], 'provider-b': [] };
  r.field.bind({ draft: r.d, data, source: 'A', seq: 3, visible: true });
  assert.equal(r.find('handoffModelInput').disabled, true);
});
test('A/B generations, menu callbacks and freeze cannot transfer a choice', async t => {
  const r = await rig(t), b = { choice: null }; r.choose('provider-b', 'shared'); const aTarget = r.field.target();
  r.find('handoffModelInput').click(); const oldModel = r.rows().find(n => n.title === 'restricted · provider-b');
  r.find('handoffReasoningBtn').click(); const oldEffort = r.find('handoffReasoningMenu').children[0];
  r.field.bind({ draft: b, data: r.data, source: 'B', seq: 2, visible: true }); oldModel.click(); oldEffort.click();
  assert.equal(r.field.target(), undefined); assert.equal(b.choice, null);
  r.field.bind({ draft: r.d, data: r.data, source: 'A', seq: 3, visible: true }); assert.deepEqual(r.field.target(), aTarget);
  r.field.bind({ draft: r.d, data: r.data, source: 'A', seq: 3, visible: false, locked: true }); oldModel.click();
  r.find('handoffInheritModel').click(); assert.deepEqual(r.d.choice, aTarget); assert(Object.isFrozen(aTarget));
  assert.deepEqual(r.writes, []);
});
test('keyboard browse restores committed display; first Escape closes menu, stale source guard refuses selection', async t => {
  const r = await rig(t), input = r.find('handoffModelInput'); r.choose('provider-b', 'shared');
  input.fire('keydown', { key: 'ArrowDown' }); assert.equal(input.value, '');
  let stopped = false; input.fire('keydown', { key: 'Escape', stopPropagation: () => stopped = true });
  assert.equal(stopped, true); assert.equal(input.value, 'shared · provider-b'); assert.equal(r.field.blockReason(), '');
  input.click(); const row = r.rows().find(n => n.title === 'restricted · provider-b'); r.stale(); row.click();
  assert.equal(r.field.target().model, 'shared');
});
test('loader resolves native same-origin prefixed modules and rejects incompatible exports', async () => {
  const seen = []; const importer = async url => { seen.push(String(url)); return { createNewSessionController() {}, iconSvg() {}, createDialogMenuController() {} }; };
  const native = await Target.loadNative('https://fixture/prefix/', { CODOXEAR_ASSET_VERSION: 'test version' }, importer);
  assert.equal(typeof native.createNewSessionController, 'function');
  assert.deepEqual(seen, ['https://fixture/prefix/static/app_new_session.js?v=test%20version', 'https://fixture/prefix/static/app_display.js', 'https://fixture/prefix/static/app_dialog_menu.js']);
  await assert.rejects(Target.loadNative('https://fixture/', {}, async () => ({})), /不兼容/);
});
for (const priorExplicit of [false, true]) test(`empty native effort choices block a new visible pick (${priorExplicit ? 'prior explicit' : 'initial inherit'}) until deliberate recovery`, async t => {
  const r = await rig(t), calls = [];
  if (priorExplicit) r.choose('provider-a', 'shared');
  r.data.new_session_defaults.backends.pi.reasoning_efforts_by_model['provider-b/restricted'] = [];
  r.choose('provider-b', 'restricted');
  assert.match(r.find('handoffModelInput').value, /restricted/);
  assert.notEqual(r.field.blockReason(), '', 'a failed visible selection must block confirmation');
  const Core = require('../web/core.js');
  const saved = new Map();
  const c = Core.createController({ key: 'test', storage: { getItem: k => saved.get(k), setItem: (k, v) => saved.set(k, v) },
    uuid: () => '12345678-1234-4123-8123-123456789012', post: async (route, body) => { calls.push(body); throw Error('synthetic loss'); } });
  const confirm = async () => { if (!r.field.blockReason()) await c.start('A', '', r.field.target()); };
  await confirm(); assert.equal(calls.length, 0);
  r.find('handoffModelInput').value = ''; r.find('handoffModelInput').fire('input');
  r.choose('provider-b', 'shared'); assert.equal(r.field.blockReason(), '');
  await confirm(); assert.deepEqual(calls[0].target, { model_provider: 'provider-b', model: 'shared', reasoning_effort: 'high' });
  r.choose('provider-b', 'restricted'); assert.notEqual(r.field.blockReason(), '');
  r.find('handoffInheritModel').click(); assert.equal(r.field.blockReason(), ''); assert.equal(r.field.target(), undefined);
  assert.deepEqual(r.writes, []);
});
test('a source guard repaint cannot erase the query before native filtering', async t => {
  const r = await rig(t), input = r.find('handoffModelInput');
  r.guard(() => {
    r.field.bind({ draft: r.d, data: r.data, source: 'A', seq: 1, visible: true });
    return true;
  });
  input.value = 'shared'; input.fire('input');
  assert.equal(input.value, 'shared');
  assert.deepEqual(r.rows().map(n => n.title), r.native.controllers[0].filteredNewSessionModelOptions().map(n => n.displayText));
  assert.equal(r.rows().length, 2);
  assert.deepEqual(r.writes, []);
});

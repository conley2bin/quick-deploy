'use strict';

const assert = require('node:assert/strict');
const { test } = require('node:test');
const { startRepair } = require('../extension');

class TabInputText {
  constructor(uri) { this.uri = uri; }
}
class TabInputCustom {
  constructor(uri, viewType) { this.uri = uri; this.viewType = viewType; }
}

function uri(path, scheme = 'file') {
  return { path, scheme, query: '', fragment: '', toString() { return `${scheme}://${path}`; } };
}
function tab(input, options = {}) {
  return { input, isActive: true, isDirty: false, ...options };
}
function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
async function settled() { await new Promise(resolve => setImmediate(resolve)); }

function harness(initialTab, options = {}) {
  const calls = [];
  const errors = [];
  const log = [];
  const group = { isActive: true, activeTab: initialTab };
  let event;
  let disposed = false;
  const media = options.media === undefined ? {
    activate: () => { calls.push('activate'); return options.activation || Promise.resolve(); }
  } : options.media;
  const workspace = { workspaceFolders: options.folders, workspaceFile: options.workspaceFile };
  const vscode = {
    TabInputText, TabInputCustom, workspace,
    window: {
      createOutputChannel() {
        return { info(message) { log.push(['info', message]); }, error(message) { log.push(['error', message]); }, dispose() {} };
      },
      tabGroups: {
        activeTabGroup: group,
        onDidChangeTabs(callback) { event = callback; return { dispose() { disposed = true; event = undefined; } }; }
      },
      showErrorMessage(message) { errors.push(message); return Promise.resolve(); }
    },
    extensions: { getExtension(id) { calls.push(['getExtension', id]); return media; } },
    commands: {
      async executeCommand(...args) {
        calls.push(args);
        if (options.commandError) throw options.commandError;
        if (options.convert !== false) {
          group.activeTab = tab(new TabInputCustom(args[1], args[2]));
        }
      }
    }
  };
  const context = { subscriptions: [] };
  return {
    vscode, workspace, group, calls, errors, log, context,
    emit() { event?.({ opened: [], closed: [], changed: [] }); },
    get disposed() { return disposed; },
    start() { startRepair(vscode, context); }
  };
}

const video = () => tab(new TabInputText(uri('/tmp/movie.mp4')));
const commands = h => h.calls.filter(c => Array.isArray(c) && c[0] === 'vscode.openWith');

test('startup text MP4 activates bundled provider and explicitly selects video once', async () => {
  const h = harness(video());
  h.start();
  await settled();
  assert.deepEqual(h.calls.slice(0, 2), [['getExtension', 'vscode.media-preview'], 'activate']);
  assert.deepEqual(commands(h), [['vscode.openWith', h.group.activeTab.input.uri, 'vscode.videoPreview']]);
  assert.equal(h.group.activeTab.input.viewType, 'vscode.videoPreview');
  assert.match(h.log[0][1], /First active tab is TabInputText: file:\/\/\/tmp\/movie\.mp4/);
  assert.match(h.log.at(-1)[1], /TabInputCustom\(vscode\.videoPreview, file:\/\/\/tmp\/movie\.mp4\)/);
  assert.equal(h.disposed, true);
  h.group.activeTab = video(); // a later deliberate Reopen as Text
  h.emit();
  assert.equal(commands(h).length, 1);
});

test('first tab event after empty startup can repair; later events cannot', async () => {
  const h = harness(undefined);
  h.start();
  assert.equal(h.disposed, false);
  h.emit();
  h.group.activeTab = video();
  h.emit();
  await settled();
  assert.equal(commands(h).length, 1);
  assert.equal(h.disposed, true);
});

test('already-custom preview is left untouched', async () => {
  const h = harness(tab(new TabInputCustom(uri('/tmp/movie.mp4'), 'vscode.videoPreview')));
  h.start();
  await settled();
  assert.deepEqual(h.calls, []);
  assert.equal(h.disposed, true);
});

test('ordinary file, non-file MP4, and a dirty MP4 are not opened with video', async () => {
  for (const first of [
    tab(new TabInputText(uri('/tmp/readme.txt'))),
    tab(new TabInputText(uri('/tmp/movie.mp4', 'vscode-remote'))),
    tab(new TabInputText(uri('/tmp/movie.mp4')), { isDirty: true })
  ]) {
    const h = harness(first);
    h.start();
    await settled();
    assert.deepEqual(h.calls, []);
    assert.equal(h.disposed, true);
  }
});

test('a later MP4 in a reused text window is not auto-opened', async () => {
  const h = harness(tab(new TabInputText(uri('/tmp/first.txt'))));
  h.start();
  h.group.activeTab = video();
  h.emit();
  await settled();
  assert.deepEqual(h.calls, []);
});

test('file with uppercase MP4 suffix is eligible', async () => {
  const h = harness(tab(new TabInputText(uri('/tmp/Movie.MP4'))));
  h.start();
  await settled();
  assert.equal(commands(h).length, 1);
});

test('project and workspace windows never attempt repair, even on later events', async () => {
  for (const options of [{ folders: [{ uri: uri('/project') }] }, { workspaceFile: uri('/project/ws.code-workspace') }]) {
    const h = harness(video(), options);
    h.start();
    h.emit();
    await settled();
    assert.deepEqual(h.calls, []);
    assert.equal(h.disposed, true);
  }
});

test('selection, group focus, dirty state and workspace changes during provider activation cancel repair', async () => {
  for (const change of [
    h => { h.group.activeTab = tab(new TabInputText(uri('/tmp/other.mp4'))); },
    h => { h.group.isActive = false; },
    h => { h.vscode.window.tabGroups.activeTabGroup = { isActive: true, activeTab: h.group.activeTab }; },
    h => { h.group.activeTab.isDirty = true; },
    h => { h.group.activeTab.input = new TabInputCustom(uri('/tmp/movie.mp4'), 'default'); },
    h => { h.workspace.workspaceFolders = [{ uri: uri('/project') }]; }
  ]) {
    const gate = deferred();
    const h = harness(video(), { activation: gate.promise });
    h.start();
    change(h);
    gate.resolve();
    await settled();
    assert.equal(commands(h).length, 0);
    assert.equal(h.errors.length, 0);
  }
});

test('missing/failed media provider and rejected openWith surface an error once', async () => {
  for (const options of [
    { media: null },
    { media: { activate: async () => { throw new Error('activation refused'); } } },
    { commandError: new Error('openWith refused') }
  ]) {
    const h = harness(video(), options);
    h.start();
    await settled();
    assert.equal(h.errors.length, 1);
    assert.match(h.errors[0], /MP4 video preview repair failed:/);
    assert.equal(h.disposed, true);
  }
});

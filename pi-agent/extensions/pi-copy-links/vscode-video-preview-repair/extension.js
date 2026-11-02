'use strict';

const VIDEO_PREVIEW = 'vscode.videoPreview';
const MEDIA_EXTENSION = 'vscode.media-preview';

// Every VS Code API this file calls, by dotted path. activeTabGroup is absent
// from the list on purpose: it may legitimately be undefined at startup.
const REQUIRED_FUNCTIONS = [
  'window.tabGroups.onDidChangeTabs', 'window.tabGroups.close',
  'window.createOutputChannel', 'window.showErrorMessage',
  'extensions.getExtension', 'commands.executeCommand',
  'TabInputText', 'TabInputCustom'
];

function missingApis(vscode) {
  const missing = REQUIRED_FUNCTIONS.filter(path =>
    typeof path.split('.').reduce((value, key) => value?.[key], vscode) !== 'function');
  if (typeof vscode.workspace !== 'object' || !vscode.workspace) missing.push('workspace');
  return missing;
}

// This runs in the extension host of the window that owns the tab. It never
// sends an editor choice through the Code CLI or to another Code window.
function startRepair(vscode, context) {
  const missing = missingApis(vscode);
  if (missing.length) {
    // No listener or command side effect. Only report through APIs that exist.
    const message = `MP4 video preview repair is inactive: this VS Code lacks ${missing.join(', ')}`;
    console.error(message);
    if (typeof vscode.window?.showErrorMessage === 'function') void vscode.window.showErrorMessage(message);
    return;
  }
  const { workspace, window } = vscode;
  let decided = false;
  let listener;
  let log;

  function standalone() {
    return !workspace.workspaceFile && !workspace.workspaceFolders?.length;
  }

  function active() {
    const group = window.tabGroups.activeTabGroup;
    return { group, tab: group?.activeTab };
  }

  function finish() {
    decided = true;
    listener?.dispose();
  }

  function output() {
    if (!log) {
      log = window.createOutputChannel('Pi MP4 Video Preview Repair');
      context.subscriptions.push(log);
    }
    return log;
  }

  function write(level, message) {
    output().appendLine(`[${level}] ${message}`);
  }

  function report(error) {
    const message = error instanceof Error ? error.message : String(error);
    write('error', message);
    void window.showErrorMessage(`MP4 video preview repair failed: ${message}`);
  }

  async function repair(group, tab, uri) {
    try {
      write('info', `First active tab is TabInputText: ${uri.toString()}`);
      const media = vscode.extensions.getExtension(MEDIA_EXTENSION);
      if (!media) throw new Error(`Bundled ${MEDIA_EXTENSION} is unavailable`);
      await media.activate(); // registerCustomEditorProvider runs during activation

      // Activation yields to the user and to editor events. Never redirect a
      // later selected tab, a dirty buffer, or a changed workspace.
      const now = active();
      if (!standalone() || now.group !== group || now.tab !== tab ||
          !group.isActive || !tab.isActive || tab.isDirty ||
          !(tab.input instanceof vscode.TabInputText) ||
          tab.input.uri.toString() !== uri.toString()) {
        write('info', 'Tab or workspace changed while activating media-preview; no editor change');
        return;
      }

      write('info', `Calling vscode.openWith for ${uri.toString()} as ${VIDEO_PREVIEW}`);
      await vscode.commands.executeCommand('vscode.openWith', uri, VIDEO_PREVIEW);
      const nowPlaying = active();
      const preview = nowPlaying.tab?.input;
      write('info', `openWith returned; active tab is ${preview instanceof vscode.TabInputCustom ? `TabInputCustom(${preview.viewType}, ${preview.uri.toString()})` : preview?.constructor.name || 'none'}`);

      // openWith creates another tab. Remove only the original, inactive text
      // tab after proving that the new active tab owns the same video URI.
      if (!standalone() || nowPlaying.group !== group || !group.isActive ||
          !nowPlaying.tab?.isActive || nowPlaying.tab === tab ||
          !(preview instanceof vscode.TabInputCustom) ||
          preview.viewType !== VIDEO_PREVIEW || preview.uri.toString() !== uri.toString() ||
          !group.tabs.includes(tab) || tab.isActive || tab.isDirty ||
          !(tab.input instanceof vscode.TabInputText) ||
          tab.input.uri.toString() !== uri.toString()) {
        write('info', 'Original text tab retained: cleanup guard did not match');
        return;
      }
      const closed = await window.tabGroups.close(tab, true);
      write('info', closed ? 'Closed original inactive text tab' : 'Original text tab retained: tabGroups.close returned false');
    } catch (error) {
      report(error);
    }
  }

  function consider() {
    if (decided) return;
    if (!standalone()) {
      finish();
      return;
    }
    const { group, tab } = active();
    if (!tab) return; // Startup can precede the first tab event.

    // Consume the first active tab only. Subsequent manual "Reopen as Text"
    // is an intentional choice and must not trigger another repair.
    finish();
    if (!group.isActive || !tab.isActive || tab.isDirty ||
        !(tab.input instanceof vscode.TabInputText)) return;
    const uri = tab.input.uri;
    if (uri.scheme !== 'file' || uri.query || uri.fragment ||
        !uri.path.toLowerCase().endsWith('.mp4')) return;
    void repair(group, tab, uri);
  }

  listener = window.tabGroups.onDidChangeTabs(consider);
  context.subscriptions.push(listener);
  consider();
}

function activate(context) {
  startRepair(require('vscode'), context);
}

module.exports = { activate, startRepair };

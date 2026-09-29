'use strict';

const VIDEO_PREVIEW = 'vscode.videoPreview';
const MEDIA_EXTENSION = 'vscode.media-preview';

// This runs in the extension host of the window that owns the tab. It never
// sends an editor choice through the Code CLI or to another Code window.
function startRepair(vscode, context) {
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
      log = window.createOutputChannel('Pi MP4 Video Preview Repair', { log: true });
      context.subscriptions.push(log);
    }
    return log;
  }

  function report(error) {
    const message = error instanceof Error ? error.message : String(error);
    output().error(message);
    void window.showErrorMessage(`MP4 video preview repair failed: ${message}`);
  }

  async function repair(group, tab, uri) {
    try {
      output().info(`First active tab is TabInputText: ${uri.toString()}`);
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
        output().info('Tab or workspace changed while activating media-preview; no editor change');
        return;
      }

      output().info(`Calling vscode.openWith for ${uri.toString()} as ${VIDEO_PREVIEW}`);
      await vscode.commands.executeCommand('vscode.openWith', uri, VIDEO_PREVIEW);
      const current = active().tab?.input;
      output().info(`openWith returned; active tab is ${current instanceof vscode.TabInputCustom ? `TabInputCustom(${current.viewType}, ${current.uri.toString()})` : current?.constructor.name || 'none'}`);
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

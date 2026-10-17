// Optional installed-Pi contract test, no provider calls or package edits.
// Usage: node verify_pi_undo.mjs /absolute/path/to/pi-coding-agent [keybindings.json]
// The ordinary regression suite does not require Pi to be installed.
// This covers native CustomEditor only, not extensions, fullscreen dispatch or replacement editors.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import { pathToFileURL } from 'node:url';

const root = process.argv[2];
if (!root) throw new Error('Pass the installed pi-coding-agent package directory');
const { KeybindingsManager } = await import(pathToFileURL(`${root}/dist/core/keybindings.js`));
const { CustomEditor } = await import(pathToFileURL(`${root}/dist/modes/interactive/components/custom-editor.js`));
const { setKeybindings, setKittyProtocolActive, matchesKey } = await import(
    pathToFileURL(`${root}/node_modules/@earendil-works/pi-tui/dist/index.js`));
const profiles = [['defaults', {}]];
if (process.argv[3]) {
    // Fail a missing/unreadable supplied path instead of silently checking defaults.
    fs.accessSync(process.argv[3], fs.constants.R_OK);
    // Match production profile loading, including legacy action-name migration.
    profiles.push(['profile', KeybindingsManager.loadFromFile(process.argv[3])]);
}
const results = [];
for (const [profile, config] of profiles) {
    for (const kitty of [false, true]) {
        setKittyProtocolActive(kitty);
        const kb = new KeybindingsManager(config);
        // Same shared keybinding instance as InteractiveMode and its base Editor.
        setKeybindings(kb);
        const editor = new CustomEditor({ requestRender() {} }, {
            borderColor: text => text,
            selectList: {
                selectedPrefix: text => text, selectedText: text => text,
                description: text => text, scrollInfo: text => text, noMatch: text => text,
            },
        }, kb);
        let suspend = 0;
        editor.onAction('app.suspend', () => suspend++);
        assert.equal(matchesKey('\x1f', 'ctrl+-'), true);
        assert.equal(kb.matches('\x1f', 'tui.editor.undo'), true,
            'This profile no longer retains the common undo key; do not deploy the alias blindly');
        // App handlers can win before Editor.undo. Reject all matching app actions
        // conservatively (including dialog-only ones), rather than freezing a copy
        // of InteractiveMode.setupKeyHandlers that can drift between Pi releases.
        const appConflicts = Object.keys(kb.getEffectiveConfig()).filter(
            action => action.startsWith('app.') && kb.matches('\x1f', action));
        assert.deepEqual(appConflicts, [], `App actions can precede undo: ${appConflicts.join(', ')}`);
        editor.handleInput('\x1f');
        assert.equal(editor.getText(), '');
        for (const ch of 'one two') editor.handleInput(ch);
        const states = [editor.getText()];
        for (let index = 0; index < 3; index++) {
            editor.handleInput('\x1f');
            states.push(editor.getText());
        }
        assert.deepEqual(states, ['one two', 'one', '', '']);
        assert.equal(suspend, 0);
        // A native deletion is undone as well as insertion; clipboard handling stays separate.
        for (const ch of 'again') editor.handleInput(ch);
        editor.handleInput('\x7f');
        assert.equal(editor.getText(), 'agai');
        editor.handleInput('\x1f');
        assert.equal(editor.getText(), 'again');
        results.push({ profile, kitty, undoKeys: kb.getKeys('tui.editor.undo'), appConflicts, states, suspend });
    }
}
console.log(JSON.stringify(results, null, 2));

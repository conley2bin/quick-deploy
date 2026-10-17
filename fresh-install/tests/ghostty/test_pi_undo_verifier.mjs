// Optional regression controls for the installed-Pi verifier.
// Usage: node test_pi_undo_verifier.mjs /absolute/path/to/pi-coding-agent
// Each candidate is loaded from its own private file through the real CLI helper.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const root = process.argv[2];
if (!root) throw new Error('Pass the installed pi-coding-agent package directory');
const helper = fileURLToPath(new URL('./verify_pi_undo.mjs', import.meta.url));
const work = fs.mkdtempSync(path.join(os.tmpdir(), 'ghostty-pi-undo-verifier-'));
let passed = 0;
try {
    function run(name, config, errorPattern, prefix = '') {
        const args = [helper, root];
        if (config !== undefined) {
            const profile = path.join(work, `${name}.json`);
            fs.writeFileSync(profile, prefix + JSON.stringify(config));
            args.push(profile);
        }
        const result = spawnSync(process.execPath, args, { encoding: 'utf8', timeout: 10000 });
        assert.ifError(result.error);
        assert.notEqual(result.status, null, `${name}: helper did not finish`);
        if (errorPattern) {
            assert.notEqual(result.status, 0, `${name}: verifier incorrectly approved the profile`);
            assert.match(result.stderr, errorPattern, `${name}: rejected for an unrelated reason`);
        } else {
            assert.equal(result.status, 0, `${name}: ${result.stderr}`);
            const results = JSON.parse(result.stdout);
            assert.equal(results.length, config === undefined ? 2 : 4);
            for (const result of results) {
                assert.deepEqual(result.states, ['one two', 'one', '', '']);
                assert.deepEqual(result.appConflicts, []);
                assert.equal(result.suspend, 0);
            }
        }
        console.log(`PASS: ${name}`);
        passed++;
    }
    run('defaults', undefined);
    run('modern-undo', { 'tui.editor.undo': ['ctrl+-', 'ctrl+z'], 'app.suspend': [] });
    run('legacy-compatible', { undo: ['ctrl+-', 'ctrl+z'], suspend: [] });
    run('production-bom-and-modern-precedence', { undo: [], 'tui.editor.undo': 'ctrl+-' }, undefined, '\uFEFF');
    for (const [name, config] of [
        ['legacy-disabled', { undo: [] }],
        ['legacy-remapped', { undo: 'ctrl+y' }],
        ['modern-disabled', { 'tui.editor.undo': [] }],
        ['modern-remapped', { 'tui.editor.undo': 'ctrl+y' }],
    ]) run(name, config, /no longer retains the common undo key/);
    for (const action of [
        'app.clear', 'app.interrupt', 'app.exit', 'app.suspend',
        'app.clipboard.pasteImage', 'app.model.cycleForward', 'app.session.new',
    ]) run(`collision-${action}`, { [action]: 'ctrl+-' }, /App actions can precede undo/);
    console.log(`PASS: ${passed} installed-Pi verifier controls; native class scope only`);
} finally {
    fs.rmSync(work, { recursive: true, force: true });
}

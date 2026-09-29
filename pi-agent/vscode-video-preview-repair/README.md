# VS Code MP4 video-preview repair

A small **VS Code** extension for the company-local Pi link workflow. Pi still opens a file with `code --new-window -- <absolute-path>`; Code 1.135 sometimes chooses a binary/text editor at cold first open even though its bundled Video Preview is available later. This companion runs in the *destination Code window*. At startup (or its first active tab event), it considers the first active tab once. Only a non-dirty, local `file:` `.mp4` **text** tab in a window without a folder/workspace is eligible. It activates `vscode.media-preview`, checks that the same tab is still active, and executes `vscode.openWith(uri, 'vscode.videoPreview')` in that window. It does not reopen an already-custom preview, touch project windows/other files, or override later deliberate “Reopen as Text” choices. The extension declares Restricted Mode compatibility, because a fresh standalone window may otherwise disable third-party extensions before they can repair the text tab. Provider/command failures appear in a VS Code error notification. `openWith` initially creates a second tab; after it returns, the extension closes the original text tab with focus preserved **only** if the same folderless window still has an active Video Preview of the same URI and the original tab is still present, inactive, non-dirty and unchanged. If any check fails or Code declines the close, it leaves the text tab alone and records the reason in the **Pi MP4 Video Preview Repair** output log.

## Test and install

```bash
cd pi-agent/vscode-video-preview-repair
npm test
# Install @vscode/vsce if needed; package outside the repository.
vsce package --no-dependencies -o /tmp/pi-video-preview-repair-0.1.0.vsix
code --install-extension /tmp/pi-video-preview-repair-0.1.0.vsix --force
code --list-extensions --show-versions | grep '^pi-local.pi-video-preview-repair@0.1.0$'
```

Restart **only after saving work in affected Code windows**, then Ctrl+click a Pi MP4 link **from the company desktop**. The separate Pi `pi-copy-links` extension and its local-vs-SSH launcher stay unchanged. This companion repairs only local `file:` MP4 tabs in company Code. An SSH-origin click opens a company file in **Y9000p Code Remote-SSH** as a `vscode-remote:` resource; the company installation and this extension's current URI guard do not repair that separate UI. Installing it into Pi's extension directory does nothing. To uninstall: `code --uninstall-extension pi-local.pi-video-preview-repair` and restart Code.

`vsce` is the official VS Code packaging tool (`npm install -g @vscode/vsce` if absent); the command above is an **operator step**, not part of Pi's installer. Isolated validation can use a private `--user-data-dir`/`--extensions-dir` profile and `Xephyr` without altering the real Code profile. `npm test` mocks VS Code APIs; a real video frame in the destination window is the visual acceptance criterion.

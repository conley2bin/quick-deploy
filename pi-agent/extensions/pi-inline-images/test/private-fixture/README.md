# Private X11 functional fixture

This provider-free fixture exercises the committed inline extension together with a disposable, replay-patched `pi-tmux-images@0.2.0` package. It never uses the caller's `DISPLAY`, tmux server, Pi profile, model, or session history.

It requires Ghostty, tmux, DBus, `xwd`, Pillow, the extension's npm dependencies, and the already extracted private Xvfb used by local image validation. Override paths explicitly when needed:

```bash
cd pi-agent/extensions/pi-inline-images
PI_IMAGE_FIXTURE_OUT=/tmp/pi-inline-images-c3
XVFB_BIN=/tmp/pi-inline-pixel-lane-20260910192606/root/usr/bin/Xvfb \
  test/private-fixture/run.sh "$PI_IMAGE_FIXTURE_OUT"
```

The script refuses an existing output directory. It creates a private X display, DBus session, XDG roots, tmux socket, Pi agent/session directories, and disposable old-package copy. Its persisted session contains complete assistant `read` toolCall → matching toolResult image → custom preview chains, so Pi's native tool rows and custom ownership arbitration are both exercised. Pi runs offline with `--no-extensions` followed by exactly one old-read extension and one `pi-inline-images` extension; no prompt or provider call is made.

Assertions performed during the run:

- hidden-first preparation produces zero uploads;
- the first compatible Ghostty client triggers 16 recent read uploads plus one 1920×1080 Markdown upload, while coordinated recent read rows expose only the custom bitmap owner;
- 3.5 seconds with the same attached client produces no repeat upload;
- switching that same attached client to another tmux window and back produces no repeat upload;
- detach plus a genuinely new Ghostty/tmux client identity triggers one complete resend and then remains stable;
- every upload is a complete Kitty `m=0` group with legal continuations;
- the >1 MiB 1920×1080 PNG is reconstructed twice with exact encoded SHA-256 and RGBA pixel/alpha SHA-256;
- native-Markdown screenshots require a large colorful rendered region, while the read-preview screenshot and PTY/pane captures remain available for review.

Review `artifacts/fixture-summary.json`, `artifacts/wire/wire-summary.json`, `first-client-native-markdown.png`, `first-client-read-previews.png`, `first-client-after-hide-show.png`, `second-client-resend.png`, `pane-*.txt`, and `pty-output.log`. Logs and temporary package/session state remain under the chosen output directory. The fixture makes no GTK crash claim.

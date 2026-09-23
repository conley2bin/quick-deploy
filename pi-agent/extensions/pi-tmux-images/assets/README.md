# Release demo capture

`demo-fixture.jpg` is the checked-in rickroll image used for the public demo.

Source URL:

```text
https://encrypted-tbn0.gstatic.com/images?q=tbn:ANd9GcSa1hkAAQ9mk3SSvcKmRwDr7rdt1VW3VRFEcc6u8N4Pdg&s=10
```

Expected SHA-256:

```text
98a3a36260668dfb991884a54640aacdc99a648886fcf33ffb102e1fa3ea8abe
```

`demo.png` is a real Ghostty + tmux render of that fixture. Re-capture it when rendering behavior or the fixture changes. Keep the capture static rather than making a GIF.

1. In Ghostty, start tmux with `set -g allow-passthrough on`.
2. From the checkout, run `pi -e .` inside tmux.
3. Run `/image /absolute/path/to/pi-tmux-images/assets/demo-fixture.jpg`.
4. Resize once and confirm the placement follows the pane.
5. Capture a clean static image to `assets/demo.png`. Crop out private prompts, paths, branch names, and unrelated terminal content.
6. Confirm `/image clear` removes the preview.
7. Verify the README image and `pi.image` package-gallery URL still point to `assets/demo.png`.

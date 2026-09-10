# Vendored protocol provenance

`kitty-placeholder.ts` is derived from `pi-tmux-images` 0.2.0, published from
Git commit `cab0433b173a3e09fae693240a63432d4d35c757`.

Upstream: <https://github.com/safurrier/pi-tmux-images>
License: MIT (see `LICENSE`).

The local copy intentionally appends SGR 0 to every placeholder row. Pi 0.85.1's
ANSI wrapper otherwise interprets the `2` parameter in Kitty underline-color
SGR (`58;2;…`) as dim state and carries it to later wrapped rows.

#!/usr/bin/env python3
from __future__ import annotations

import os
import re
import shlex
import sys
from pathlib import Path

BEGIN = "# >>> codoxear codex wrapper >>>"
END = "# <<< codoxear codex wrapper <<<"


def render(python: str, launcher: str) -> str:
    py = shlex.quote(python)
    launch = shlex.quote(launcher)
    return "\n".join(
        [
            BEGIN,
            "codox() {",
            f"  {py} {launch} codex \"$@\"",
            "}",
            "",
            "piox() {",
            f"  {py} {launch} pi \"$@\"",
            "}",
            END,
            "",
        ]
    )


def update(path: Path, block: str) -> bool:
    original = path.read_text(encoding="utf-8") if path.exists() else ""
    pattern = re.compile(rf"(?ms)^{re.escape(BEGIN)}\n.*?^{re.escape(END)}\n?")
    match = pattern.search(original)
    if match:
        updated = original[: match.start()] + block + original[match.end() :]
    else:
        updated = original.rstrip("\n")
        if updated:
            updated += "\n\n"
        updated += block
    if updated == original:
        return False

    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        path.write_text(updated, encoding="utf-8")
        return True
    tmp = path.with_name(f".{path.name}.codoxear-{os.getpid()}")
    tmp.write_text(updated, encoding="utf-8")
    if path.exists():
        os.chmod(tmp, path.stat().st_mode)
    else:
        os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    return True


def main() -> None:
    if len(sys.argv) != 4:
        raise SystemExit("usage: update_shell_rc.py RC_FILE PYTHON LAUNCHER")
    path = Path(sys.argv[1]).expanduser()
    changed = update(path, render(sys.argv[2], sys.argv[3]))
    print(f"wrapper rc: {'updated' if changed else 'unchanged'} ({path})")


if __name__ == "__main__":
    main()

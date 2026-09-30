#!/usr/bin/env bash
set -euo pipefail
if [[ $# -gt 1 ]]; then
  echo "usage: $0 [pi-code-reasoning-package-directory]" >&2
  exit 64
fi
target=${1:-${PI_CODING_AGENT_DIR:-$HOME/.pi/agent}/npm/node_modules/@feniix/pi-code-reasoning}
python3 - "$target/package.json" <<'PY'
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import tempfile

before = "cc47dba462775b26022fafc099f4c5f04b48fc39096f32bf4cfa6a5f8d7379fa"
after = "3bcf117a73f2a8c250e7228b96e24bd91a2c3e9b1030ec05e6b12a7d3d81b832"
path = Path(sys.argv[1])
if path.is_symlink():
    sys.exit("guard failed: package.json is a symlink; preserving it unchanged")
try:
    original = path.read_bytes()
except OSError as error:
    sys.exit(f"cannot read package.json: {error}")
digest = hashlib.sha256(original).hexdigest()
if digest == after:
    print("already applied: pi-code-reasoning 5.1.0 host typebox peer")
    sys.exit(0)
if digest != before:
    sys.exit("guard failed: expected pristine pi-code-reasoning 5.1.0; "
             f"found sha256 {digest}; preserving package.json unchanged")
manifest = json.loads(original)
if manifest["name"] != "@feniix/pi-code-reasoning" or manifest["version"] != "5.1.0":
    sys.exit("guard failed: unexpected package identity")
del manifest["dependencies"]["typebox"]
manifest["peerDependencies"]["typebox"] = "*"
patched = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode()
if hashlib.sha256(patched).hexdigest() != after:
    sys.exit("guard failed: unexpected patch output; preserving package.json unchanged")
# Replace one complete manifest, retaining its mode. Interrupted preparation
# leaves the installed manifest intact; no npm lock or dependency tree is changed.
temporary = None
try:
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".pi-code-reasoning-peer-", delete=False) as handle:
        temporary = Path(handle.name)
        os.fchmod(handle.fileno(), stat.S_IMODE(path.stat().st_mode))
        handle.write(patched)
        handle.flush()
        os.fsync(handle.fileno())
    if path.is_symlink() or path.read_bytes() != original:
        sys.exit("guard failed: package.json changed during preparation; preserving it")
    os.replace(temporary, path)
finally:
    if temporary is not None and temporary.exists():
        temporary.unlink()
print("applied: pi-code-reasoning 5.1.0 host typebox peer")
PY

#!/usr/bin/env python3
"""Test-only stdin bridge to exact helper source, with fake desktop endpoints."""

import json
import os
from pathlib import Path
import runpy
import socket
import sys

CLIENT_TESTS = Path(__file__).resolve().parents[1] / "client" / "tests"
sys.path.insert(0, str(CLIENT_TESTS))
from hostname_fixture import HELPER, HOST_ROLES  # noqa: E402
from test_helper import FAKE  # noqa: E402

root, hostname, remote_paths = Path(sys.argv[1]), sys.argv[2], set(json.loads(sys.argv[3]))
runtime, binary = root / "runtime", root / "bin"
runtime.mkdir(mode=0o700)
binary.mkdir()
fake = binary / "fake"
fake.write_text(FAKE.replace("#!PYTHON", "#!" + sys.executable))
fake.chmod(0o755)
for name in ("systemctl", "loginctl", "xset", "code", "ssh", "ss", "xdg-open",
             "xdg-settings", "google-chrome-stable"):
    (binary / name).symlink_to(fake)
(root / "state.json").write_text(json.dumps({"next_pid": 4001, "masters": {}, "listeners": []}))
os.environ.update({"HOME": str(root), "PATH": str(binary) + ":" + os.environ["PATH"],
                   "FAKE_STATE": str(root / "state.json"), "FAKE_LOG": str(root / "commands.jsonl"),
                   "FAKE_PEER": json.dumps(HOST_ROLES.get(hostname, {})),
                   "TEST_RUNTIME": str(runtime), "DISPLAY": ":forged",
                   "HOSTNAME": "untrusted-inherited-host", "SSH_CONNECTION": "untrusted"})
# Patch only in this fixture process. Production has no role override or
# permissive transport. A real local same-path file must never be inspected.
socket.gethostname = lambda: hostname
module = runpy.run_path(str(HELPER))


def prohibit_remote_path(function):
    def guarded(path, *args, **kwargs):
        if isinstance(path, (str, bytes, os.PathLike)) and os.fsdecode(path) in remote_paths:
            raise AssertionError("desktop inspected a Pi-server resource: " + os.fsdecode(path))
        return function(path, *args, **kwargs)
    return guarded


for name in ("stat", "lstat", "access"):
    setattr(os, name, prohibit_remote_path(getattr(os, name)))
sys.exit(module["main"]())

"""Opt-in installed-native + real private tmux probe; fake Pi, no credentials/model.

HANDOFF_NATIVE_PYTHON=/absolute/installed/venv/bin/python python -m unittest ...
The ordinary suite skips this test. Every process, window and state dir is owned
by this probe. Native mirror is unmodified; completion requires actual names.
"""
import asyncio
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile
import time
import unittest
from urllib.parse import quote
from uuid import uuid4

import aiohttp
from test_engine import Engine, HandoffError


FAKE_PI = '''#!/usr/bin/python3
import json, os, pathlib, signal, sys, uuid
if "--help" in sys.argv:
    print("--approve --session --provider --model --thinking")
    sys.exit(0)
p = pathlib.Path(sys.argv[sys.argv.index("--session") + 1])
p.parent.mkdir(parents=True, exist_ok=True)
rows = [
 {"type":"session", "version":3, "id":str(uuid.uuid4()), "cwd":os.getcwd(), "timestamp":"2026-11-02T01:00:00Z"},
 {"type":"model_change", "id":"model", "parentId":None, "provider":"test-provider", "modelId":"test-model"},
 {"type":"thinking_level_change", "id":"thinking", "parentId":"model", "thinkingLevel":"high"},
 {"type":"message", "id":"user", "parentId":"thinking", "message":{"role":"user", "content":[{"type":"text", "text":"Private cosmetic naming probe; no model execution."}]}},
 {"type":"message", "id":"assistant", "parentId":"user", "message":{"role":"assistant", "content":[{"type":"text", "text":"READY"}]}}
]
p.write_text("".join(json.dumps(row) + "\\n" for row in rows))
p.chmod(0o600)
print("READY", flush=True)
while True: signal.pause()
'''

SERVER = '''import json, os
from pathlib import Path
from codoxear import server
server._init_runtime()
server._require_password()
httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
mirror = server._tmux_order_sync()
Path("native-ready.json").write_text(json.dumps({"port":httpd.server_address[1],
    "mirror_started":mirror._thread is not None and mirror._thread.is_alive(),
    "tmux_available":server._tmux_available(), "app_dir":str(server.APP_DIR)}))
httpd.serve_forever()
'''


@unittest.skipUnless(os.environ.get("HANDOFF_NATIVE_PYTHON"), "opt-in private installed-native/tmux probe")
class NativeNamingTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_nonce_bound_window_name_and_limit(self):
        native_python = Path(os.environ["HANDOFF_NATIVE_PYTHON"]).absolute()
        tmux = shutil.which("tmux")
        self.assertTrue(native_python.is_file() and tmux)
        with tempfile.TemporaryDirectory(prefix="handoff-name-", dir="/tmp") as temp:
            root = Path(temp)
            root.chmod(0o700)
            home, bindir = root / "home", root / "bin"
            home.mkdir(); bindir.mkdir()
            project = root / "project"
            project.mkdir()
            socket = root / "tmux.sock"
            wrapper = bindir / "tmux"
            wrapper.write_text("#!/bin/sh\nexec " + shlex.quote(tmux) + " -S " + shlex.quote(str(socket)) + " \"$@\"\n")
            wrapper.chmod(0o700)
            fake = bindir / "pi"
            fake.write_text(FAKE_PI); fake.chmod(0o700)
            server_script = root / "server.py"
            server_script.write_text(SERVER)
            # Whitelist: no real auth/provider configuration or inherited TMUX.
            env = {"HOME":str(home), "PATH":str(bindir) + ":/usr/bin:/bin", "LANG":"C.UTF-8",
                   "PI_HOME":str(home / ".pi"), "PI_CODING_AGENT_DIR":str(home / ".pi/agent"),
                   "PI_BIN":str(fake), "CODEX_WEB_TMUX_SESSION":"name-probe",
                   "CODEX_WEB_PASSWORD":"private-probe-password", "SHELL":"/bin/sh"}
            native = engine = client = None
            stderr = (root / "native.log").open("wb")
            def windows():
                result = subprocess.run([tmux, "-S", str(socket), "list-windows", "-a", "-F",
                    "#{window_id}\t#{@codoxear_nonce}\t#{window_name}"], capture_output=True, text=True)
                return [line.split("\t", 2) for line in result.stdout.splitlines()] if result.returncode == 0 else []
            async def until(check, seconds=35):
                deadline = time.monotonic() + seconds
                while time.monotonic() < deadline:
                    value = await check()
                    if value:
                        return value
                    await asyncio.sleep(.25)
                raise AssertionError("private native probe deadline: " + (root / "native.log").read_text()[-4000:])
            try:
                native = subprocess.Popen([str(native_python), str(server_script)], cwd=root, env=env,
                                          stdout=stderr, stderr=stderr)
                async def ready():
                    path = root / "native-ready.json"
                    return json.loads(path.read_text()) if path.exists() else None
                info = await until(ready)
                self.assertTrue(info["mirror_started"])
                self.assertTrue(info["tmux_available"])
                self.assertTrue(Path(info["app_dir"]).is_relative_to(home))
                client = aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True))
                base = "http://127.0.0.1:" + str(info["port"])
                mutations = []
                async def request(method, path, *, body=None, cookie=None, **kwargs):
                    if method == "POST":
                        mutations.append((path, body))
                    async with client.request(method, base + path, json=body,
                                              headers={"Origin":base}) as response:
                        return response.status, await response.json()
                code, _ = await request("POST", "/api/login", body={"password":env["CODEX_WEB_PASSWORD"]})
                self.assertEqual(code, 200)
                code, created = await request("POST", "/api/sessions", body={
                    "cwd":str(project), "agent_backend":"pi", "model_provider":"test-provider",
                    "model":"test-model", "reasoning_effort":"high", "create_in_tmux":True,
                    "args":["Private fake-Pi naming source"]})
                self.assertEqual(code, 200, created)
                async def source_ready():
                    code, listing = await request("GET", "/api/sessions")
                    self.assertEqual(code, 200)
                    key = "launch_id" if created.get("launch_id") else "broker_pid"
                    matches = [r for r in listing["sessions"] if r.get(key) == created[key]]
                    return matches[0] if len(matches) == 1 and matches[0].get("log_path") and Path(matches[0]["log_path"]).is_file() else None
                source = await until(source_ready)
                name = "中文 空格 '引号' $;\"_09"
                code, _ = await request("POST", "/api/sessions/" + quote(source["session_id"], safe="") + "/rename", body={"name":name})
                self.assertEqual(code, 200)
                async def source_mirrored():
                    await request("GET", "/api/sessions")
                    return next((w for w in windows() if w[1] == source["spawn_nonce"] and w[2] == name), None)
                original_window = await until(source_mirrored)
                engine = Engine(request, state_root=root / "handoff", timeouts={
                    "poll":.1, "launch":30, "understanding":.2}).open()
                rows = await engine.sessions("")
                mutations.clear()
                public = engine.start(source["session_id"], str(uuid4()), "", rows=rows, cookie="")
                await asyncio.wait_for(engine.tasks[public["id"]], 35)
                op = engine.operations[public["id"]]
                self.assertTrue(op["intents"]["rename"]["verified"], op)
                self.assertEqual(op["error_code"], "understanding_timeout")  # intentionally no LLM report
                async def target_mirrored():
                    listing = await engine.sessions("")
                    target = engine._verify(op, listing, "target")
                    self.assertEqual(target["alias"], name[:-2] + "10")
                    self.assertEqual(target["display_name"], target["alias"])
                    return next((w for w in windows() if w[1] == target["spawn_nonce"] and w[2] == target["alias"]), None)
                successor_window = await until(target_mirrored)
                self.assertNotEqual(successor_window[0], original_window[0])
                self.assertIn(original_window, windows())
                fresh = await engine.sessions("")
                self.assertEqual(engine._row(fresh, source["session_id"])["alias"], name)
                self.assertEqual([p for p, _ in mutations], ["/api/sessions", "/api/sessions/" + quote(op["target_session_id"], safe="") + "/rename"])
                # Separate fresh Engine and freshly read native _99 alias: no
                # synthetic name override can stand in for this limit proof.
                code, _ = await request("POST", "/api/sessions/" + quote(source["session_id"], safe="") + "/rename",
                                        body={"name":"private_99"})
                self.assertEqual(code, 200)
                limit = Engine(request, state_root=root / "limit").open()
                try:
                    limit_rows = await limit.sessions("")
                    self.assertEqual(limit._row(limit_rows, source["session_id"])["display_name"], "private_99")
                    before = list(mutations)
                    with self.assertRaises(HandoffError) as raised:
                        limit.start(source["session_id"], str(uuid4()), "", rows=limit_rows, cookie="")
                    self.assertEqual(raised.exception.code, "sequence_limit")
                    self.assertEqual(mutations, before)
                    self.assertFalse(limit.operations)
                finally:
                    await limit.close()
                print(json.dumps({"mirror_started":info["mirror_started"], "tmux_available":info["tmux_available"],
                    "source_window":original_window, "target_window":successor_window,
                    "native_alias":op["target_display_name"], "limit_native_creates":0,
                    "socket":str(socket), "native_pid":native.pid,
                    "scope":"real native create/rename/listing/mirror; fake Pi; no model semantics"}, ensure_ascii=False))
            finally:
                if engine:
                    await engine.close()
                if client:
                    await client.close()
                if native:
                    native.terminate()
                    await asyncio.to_thread(native.wait, 10)
                # Socket was fresh under this private TemporaryDirectory. Kill
                # only enumerated windows on that socket, never any server.
                for window in windows():
                    subprocess.run([tmux, "-S", str(socket), "kill-window", "-t", window[0]], check=True, capture_output=True)
                self.assertEqual(windows(), [], "test-owned tmux windows must be gone")
                stderr.close()
                print(json.dumps({"cleanup":"complete", "socket":str(socket),
                                  "remaining_windows":0, "native_returncode":native.returncode if native else None}))


if __name__ == "__main__":
    unittest.main()

"""Opt-in native acceptance runner. Unit tests import helpers but never run Pi.

Run ONLY after a parent-approved isolation/auth plan, inside that plan's own
private tmux pane. Credentials and environment must already be provisioned.
This runner does not copy or refresh credentials and never chooses a model.
"""
from __future__ import annotations

import argparse
import asyncio
import ctypes
import errno
import importlib.util
import json
import os
from pathlib import Path
import struct
import shlex
import signal
import sys
import time
from types import SimpleNamespace
import uuid

import aiohttp
from aiohttp.test_utils import TestServer

from integration_fixture import (
    assert_intended_action, assert_startup_submitted, assert_unchanged,
    project_fingerprint, read_correlated_chain,
)


class FileEvents:
    """Linux inotify notifications, including directories created during a run."""
    def __init__(self, root):
        self.root = root
        self.lib = ctypes.CDLL(None, use_errno=True)
        self.fd = self.lib.inotify_init1(os.O_NONBLOCK | os.O_CLOEXEC)
        if self.fd < 0:
            raise OSError(ctypes.get_errno(), "inotify_init1")
        self.paths = {}
        self.event = asyncio.Event()
        self.observations = []
        self._add_tree(root)
        asyncio.get_running_loop().add_reader(self.fd, self._read)

    def _add_tree(self, path):
        for directory in [path, *(p for p in path.rglob("*") if p.is_dir())]:
            wd = self.lib.inotify_add_watch(self.fd, os.fsencode(directory), 0x0000010A | 0x00000080)
            if wd < 0:
                error = ctypes.get_errno()
                # Native/Pi lock directories can disappear between enumeration
                # and watch registration. Keep processing the event buffer.
                if error == errno.ENOENT:
                    continue
                raise OSError(error, "inotify_add_watch")
            self.paths[wd] = directory

    def _read(self):
        try:
            raw = os.read(self.fd, 65536)
        except BlockingIOError:
            return
        offset = 0
        while offset < len(raw):
            wd, mask, cookie, size = struct.unpack_from("iIII", raw, offset)
            name = os.fsdecode(raw[offset + 16:offset + 16 + size].split(b"\0", 1)[0])
            offset += 16 + size
            if mask & 0x4000:
                raise RuntimeError("inotify overflow: ordered mutation evidence lost")
            if wd not in self.paths:
                continue
            path = self.paths[wd] / name
            self.observations.append({"time_ns": time.monotonic_ns(), "path": str(path), "mask": mask})
            if mask & 0x40000000 and path.is_dir():
                self._add_tree(path)
        self.event.set()

    async def until(self, predicate, timeout):
        async with asyncio.timeout(timeout):
            while True:
                self.event.clear()
                result = await predicate()
                if result:
                    return result
                await self.event.wait()

    def close(self):
        asyncio.get_running_loop().remove_reader(self.fd)
        os.close(self.fd)


def private_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    path.chmod(0o600)


def load_host():
    path = Path(__file__).resolve().parents[2] / "web-host/host.py"
    spec = importlib.util.spec_from_file_location("codoxear_native_acceptance_host", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def check_isolation(root):
    root = root.resolve()
    if not root.name.startswith("codoxear-handoff-acceptance-") or root.parent != Path("/tmp"):
        raise ValueError("expected the approved fresh /tmp acceptance root")
    expected = {"HOME": str(root / "home"), "PI_HOME": str(root / "home/.pi"),
                "PI_CODING_AGENT_DIR": str(root / "home/.pi/agent")}
    if any(os.environ.get(key) != value for key, value in expected.items()):
        raise ValueError("private HOME/Pi isolation mismatch")
    auth = json.loads((root / "home/.pi/agent/auth.json").read_text())
    entry = auth.get("openai", {})
    if set(auth) != {"openai"} or entry.get("refresh") != "TEST_ONLY_REFRESH_DISABLED":
        raise ValueError("auth must contain only access-only openai credentials")
    if entry.get("type") != "oauth" or entry.get("expires", 0) / 1000 - time.time() < 1200:
        raise ValueError("access TTL must cover 900s experiment plus 300s margin")
    if not (root / "tmux.sock").is_socket():
        raise ValueError("approved private tmux server is missing")
    return root


async def run(args):
    root = check_isolation(Path(args.root))
    evidence = root / "evidence"
    evidence.mkdir(mode=0o700, exist_ok=True)
    project = root / "project"
    baseline = project_fingerprint(project)
    seed_paths = list((root / "home/.pi/agent/sessions/demo").glob("*.jsonl"))
    if len(seed_paths) != 1:
        raise ValueError("expected exactly one private synthetic seed")
    seed = seed_paths[0]
    watcher = FileEvents(root)
    host = load_host()
    owner, handoff = host.default_features()  # actual committed assets; no fake script
    native = front = client = None
    native_identity = None
    engine = None
    report = {"level": "isolated-native", "status": "running", "model": "openai/gpt-6-astra"}
    mutation_calls = []
    source = None
    stderr = (evidence / "native-stderr.txt").open("wb")
    try:
        ready_path = evidence / "native-ready.json"
        script = """import json, os
from pathlib import Path
from codoxear import server
server._init_runtime()
server._require_password()
httpd = server.ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
pid = os.getpid()
ready = {'port': httpd.server_address[1], 'pid': pid,
         'start_ticks': Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()[19]}
Path(%r).write_text(json.dumps(ready))
httpd.serve_forever()
""" % str(ready_path)
        server_path = evidence / "native-server.py"
        server_path.write_text(script)
        command = shlex.join([args.native_python, str(server_path)]) + " 2>" + shlex.quote(str(evidence / "native-stderr.txt"))
        native = await asyncio.create_subprocess_exec("tmux", "new-window", "-d", "-P", "-F", "#{pane_id}",
                    "-t", "acceptance", "-n", "native-web", command,
                    cwd=root, env=dict(os.environ), stdout=asyncio.subprocess.PIPE, stderr=stderr)
        stdout, _ = await native.communicate()
        if native.returncode:
            raise RuntimeError("private tmux native Web launch failed")
        report["native_pane"] = stdout.decode().strip()
        async def native_ready():
            try:
                return json.loads(ready_path.read_text())
            except (FileNotFoundError, json.JSONDecodeError):
                return False
        native_identity = await watcher.until(native_ready, 30)
        port = native_identity["port"]
        report["native_pid"] = native_identity["pid"]
        report["native_start_ticks"] = native_identity["start_ticks"]

        def install(app, ctx):
            nonlocal engine
            transport = ctx.upstream_json
            async def observe(method, path, **kwargs):
                if method == "POST":
                    body = kwargs.get("body")
                    mutation_calls.append({"path": path, "body": body, "time_ns": time.monotonic_ns()})
                    private_json(evidence / "native-mutations.json", mutation_calls)
                    if path == "/api/sessions":
                        assert body["model_provider"] == "openai" and body["model"] == "gpt-6-astra"
                        assert body["create_in_tmux"] is True and len(body["args"]) == 1
                        assert not body["args"][0].startswith(("-", "@"))
                        assert "resume_session_id" not in body
                    elif path.endswith("/send"):
                        assert_unchanged(project, baseline, "before native stage delivery")
                        op = next(iter(engine.operations.values()))
                        assert_startup_submitted(Path(op["target_header"]["log_path"]),
                            mutation_calls[0]["body"]["args"][0],
                            source_header_id=op["source_header"]["pi_session_id"])
                return await transport(method, path, **kwargs)
            ctx.upstream_json = observe
            engine = handoff.install(app, ctx, state_root=root / "run-state", timeouts={
                "launch": 120, "understanding": 240, "source_idle": 90,
                "review": 240, "resolution": 240})
        front = TestServer(host.create_app(f"http://127.0.0.1:{port}", features=(owner, SimpleNamespace(install=install))))
        await front.start_server()
        client = aiohttp.ClientSession(cookie_jar=aiohttp.DummyCookieJar())
        async def request(method, path, body=None, cookie=""):
            async with client.request(method, front.make_url(path), json=body,
                headers={"Cookie": cookie, "Origin": str(front.make_url("")).rstrip("/")}) as response:
                value = await response.json()
                if response.status != 200:
                    raise RuntimeError(f"native/feature {method} {path} returned {response.status}")
                return value, response.cookies
        _, cookies = await request("POST", "/api/login", {"password": os.environ["CODEX_WEB_PASSWORD"]})
        cookie = "; ".join(f"{key}={value.value}" for key, value in cookies.items())
        created, _ = await request("POST", "/api/sessions", {
            "cwd": str(project), "agent_backend": "pi", "model_provider": "openai",
            "model": "gpt-6-astra", "reasoning_effort": "medium", "create_in_tmux": True,
            "args": ["--session", str(seed), "This instruction applies only to this predecessor session. "
                "Acknowledge the fixture with exactly READY, without an answer-key summary or task edits, "
                "then wait to review a successor. It does not restrict the separate successor's authority "
                "to implement the latest user goal after the review/resolution protocol."]}, cookie)
        private_json(evidence / "source-create.json", created)
        async def source_ready():
            nonlocal source
            rows, _ = await request("GET", "/api/sessions", cookie=cookie)
            key = "launch_id" if created.get("launch_id") else "broker_pid"
            candidates = [row for row in rows["sessions"] if row.get(key) == created.get(key)]
            if len(candidates) != 1:
                return False
            row = candidates[0]
            if row.get("busy") is not False or not row.get("log_path"):
                return False
            log = Path(row["log_path"])
            if not log.exists():
                return False
            entries = [json.loads(line) for line in log.read_text().splitlines() if line.strip()]
            ready = any(entry.get("message", {}).get("role") == "assistant" and
                        any(block.get("text", "").strip() == "READY" for block in entry["message"].get("content", [])
                            if isinstance(block, dict)) for entry in entries)
            if ready:
                source = row
            return ready
        await watcher.until(source_ready, 180)
        assert_unchanged(project, baseline, "predecessor initialization")
        private_json(evidence / "source-binding.json", source)
        started, _ = await request("POST", "/__session_handoff__/start", {
            "source_session_id": source["session_id"], "request_id": str(uuid.uuid4())}, cookie)
        operation_id = started["operation"]["id"]
        report["operation_id"] = operation_id
        task = engine.tasks[operation_id]
        await asyncio.wait_for(asyncio.shield(task), 780)
        operation = engine.operations[operation_id]
        report["operation"] = engine.public(operation_id)
        if operation["status"] != "ready":
            raise AssertionError("handoff ended at " + operation["status"] + ":" + operation.get("error_code", ""))
        directory = root / "run-state" / operation_id
        manifest = json.loads((directory / "manifest.json").read_text())
        chain = read_correlated_chain({"manifest": manifest, "operation": directory})
        async def action_done():
            try:
                assert_intended_action(project, baseline)
                return True
            except AssertionError:
                return False
        await watcher.until(action_done, 120)
        assert len(mutation_calls) == 3, "expected create, predecessor review, successor continuation exactly once"
        # Publication is via link/rename; capture an event for the final filename,
        # not merely a temporary draft or an HTTP response.
        resolution_events = [x for x in watcher.observations if x["path"] == str(directory / "resolution.json")]
        main_events = [x for x in watcher.observations if x["path"] == str(project / "main.py")]
        if not resolution_events or not main_events or resolution_events[0]["time_ns"] > main_events[0]["time_ns"]:
            raise AssertionError("missing ordered resolution-before-task-write evidence")
        report.update(status="protocol-and-toy-action-passed", semantic_manual_review="required",
                      hashes=[artifact["_sha256"] for artifact in chain],
                      project_files=project_fingerprint(project))
    except BaseException as exc:
        report.update(status="failed", error_type=type(exc).__name__, error=str(exc)[:500])
        raise
    finally:
        private_json(evidence / "result.json", report)
        private_json(evidence / "file-events.json", watcher.observations)
        watcher.close()
        if client:
            await client.close()
        if front:
            await front.close()
        if native_identity:
            pid = native_identity["pid"]
            stat = Path(f"/proc/{pid}/stat")
            if stat.exists() and stat.read_text().rsplit(")", 1)[1].split()[19] == native_identity["start_ticks"]:
                os.kill(pid, signal.SIGTERM)
        stderr.close()
        # Pi/broker windows are retained for identity/tool-behavior inspection.
        # The approved operator must stop only those exact recorded windows.
        print(json.dumps({"status": report["status"], "evidence": str(evidence)}, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True)
    parser.add_argument("--native-python", required=True)
    args = parser.parse_args()
    async def bounded():
        async with asyncio.timeout(900):
            await run(args)
    asyncio.run(bounded())


if __name__ == "__main__":
    main()

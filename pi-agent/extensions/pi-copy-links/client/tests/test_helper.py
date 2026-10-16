#!/usr/bin/env python3
"""Black-box client helper tests: all SSH/desktop commands are isolated fakes."""

import fcntl
import http.client
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import runpy
import shutil
import signal
import socket
import subprocess
import sys
import threading
import tempfile
import time
import unittest
from urllib.parse import quote, urlsplit
from unittest.mock import patch

from hostname_fixture import COMPANY_HOST, HELPER, HOST_ROLES, LAPTOP_HOST, helper_command


class IPv6HTTPServer(HTTPServer):
    address_family = socket.AF_INET6


MODULE = Path(__file__).resolve().parents[1]
FAKE = r'''#!PYTHON
import json
import os
from pathlib import Path
import re
import socket
import sys
import time

name = Path(sys.argv[0]).name
args = sys.argv[1:]
state_path = Path(os.environ["FAKE_STATE"])
log_path = Path(os.environ["FAKE_LOG"])
state = json.loads(state_path.read_text())
peer = json.loads(os.environ["FAKE_PEER"])
with log_path.open("a") as log:
    log.write(json.dumps({"name": name, "args": args, "display": os.environ.get("DISPLAY"),
                          "authority": os.environ.get("XAUTHORITY")}) + "\n")

def save():
    state_path.write_text(json.dumps(state))

if name == "systemctl":
    if state.get("environment_padding"):
        print("PADDING=" + "x" * state["environment_padding"])
    if state.get("gui_missing"):
        print("XDG_RUNTIME_DIR=" + os.environ["TEST_RUNTIME"])
    else:
        print("XDG_RUNTIME_DIR=" + os.environ["TEST_RUNTIME"])
        print("DBUS_SESSION_BUS_ADDRESS=unix:path=" + os.environ["TEST_RUNTIME"] + "/bus")
        print("DISPLAY=:1")
        print("XAUTHORITY=" + os.environ["TEST_RUNTIME"] + "/Xauthority")
        print("XDG_SESSION_TYPE=x11")
elif name == "loginctl":
    if "show-user" in args:
        print("Display=2\nSessions=2 8 3" if state.get("ambiguous_gui") else "Display=2\nSessions=2 8")
    elif "show-session" in args:
        local = args[args.index("show-session") + 1] != "8"
        print("Active=yes\nRemote=" + ("no" if local else "yes") +
              "\nType=" + ("x11" if local else "tty") + "\nClass=user\nUser=" + str(os.getuid()))
elif name == "xset":
    if state.get("xset_fail"):
        print("X11 authentication failed", file=sys.stderr)
        sys.exit(1)
elif name == "xdg-settings":
    assert args == ["get", "default-web-browser"]
    if state.get("xdg_settings_fail"):
        print("cannot read default browser", file=sys.stderr)
        sys.exit(4)
    print(state.get("default_browser", "google-chrome.desktop"))
elif name == "ssh":
    if "-G" in args:
        assert args == ["-G", peer["alias"]], args
        if state.get("ssh_config_lines"):
            print(state["ssh_config_lines"])
        elif os.environ.get("FAKE_SSH_CONFIG"):
            import subprocess
            config = Path(os.environ["FAKE_SSH_CONFIG"])
            query = subprocess.run([os.environ["REAL_SSH"], "-G", "-F", str(config), peer["alias"]],
                                   text=True, capture_output=True, check=True)
            print(query.stdout, end="")
            if state.get("mutate_alias_after_snapshot"):
                config.write_text(state["mutate_alias_after_snapshot"])
        else:
            print("hostname {host}\nuser {user}\nport {port}".format(**peer))
    elif "-M" in args:
        assert "ClearAllForwardings=yes" in args and "BatchMode=yes" in args
        assert "ExitOnForwardFailure=yes" in args and "-fN" in args
        assert "StrictHostKeyChecking=yes" in args
        assert "-R" not in args and args[-1] == peer["alias"]
        assert f"HostName={peer['host']}" in args and f"User={peer['user']}" in args and f"Port={peer['port']}" in args
        if os.environ.get("FAKE_SSH_CONFIG"):
            import subprocess
            expanded = [os.environ["REAL_SSH"], "-G", "-F", os.environ["FAKE_SSH_CONFIG"]]
            for index, arg in enumerate(args[:-1]):
                if arg == "-o":
                    expanded.extend([arg, args[index + 1]])
            expanded.append(args[-1])
            query = subprocess.run(expanded, text=True, capture_output=True, check=True)
            state["master_effective_config"] = query.stdout
        if state.get("master_fail"):
            print("Permission denied (publickey)", file=sys.stderr)
            sys.exit(255)
        sock = args[args.index("-S") + 1]
        with socket.socket(socket.AF_UNIX) as s:
            s.bind(sock)
        pid = state["next_pid"]
        state["next_pid"] += 1
        state["masters"][sock] = {"pid": pid, "alive": True}
        save()
    elif "-O" in args:
        assert args[args.index("-F") + 1] == "/dev/null"
        assert "BatchMode=yes" in args
        assert "-R" not in args
        assert args[-5:] == ["-p", str(peer["port"]), "-l", peer["user"], peer["host"]]
        op = args[args.index("-O") + 1]
        sock = args[args.index("-S") + 1]
        master = state["masters"].get(sock)
        if not master or not master["alive"]:
            print("Control socket connect: Connection refused", file=sys.stderr)
            sys.exit(255)
        if op == "check":
            print("Master running (pid=%s)" % master["pid"])
        elif op == "forward":
            assert "ExitOnForwardFailure=yes" in args
            spec = args[args.index("-L") + 1]
            match = re.fullmatch(r"(\[[^]]+\]|[^:]+):(\d+):(\[[^]]+\]|[^:]+):(\d+)", spec)
            assert match, spec
            local, localport, remote, remoteport = match.groups()
            local, remote = local.strip("[]"), remote.strip("[]")
            assert local in ("127.0.0.1", "::1", "::ffff:7f00:1", "localhost")
            assert remote in ("127.0.0.1", "::1", "::ffff:7f00:1")
            assert (local == "localhost" or local == remote), spec
            port = int(localport)
            families = ("127.0.0.1", "::1") if local == "localhost" else (local,)
            if state.get("race_port") == port:
                state.pop("race_port")
                state["listeners"].append({"host": state.get("race_host", families[0]), "port": port, "pid": 999})
                save()
                print("mux_client_forward: Port forwarding failed", file=sys.stderr)
                sys.exit(255)
            if state.get("forward_fail"):
                print("mux_client_forward: permission denied", file=sys.stderr)
                sys.exit(255)
            if state.get("forward_partial_failure"):
                state["listeners"].append({"host": families[0], "port": port, "pid": master["pid"]})
                save()
                print("mux_client_forward: Port forwarding failed after one bind", file=sys.stderr)
                sys.exit(255)
            if any(item["port"] == port and item["host"] in
                   (*families, "*", "0.0.0.0", "::") for item in state["listeners"]):
                print("mux_client_forward: Port forwarding failed", file=sys.stderr)
                sys.exit(255)
            if state.get("forward_no_listener"):
                sys.exit(0)
            for family in families:
                if state.get("forward_omit_family") == family:
                    continue
                state["listeners"].append({"host": family, "port": port,
                                           "pid": 999 if state.get("forward_wrong_pid_family") == family else master["pid"],
                                           "remote_host": remote, "remote_port": int(remoteport)})
            save()
        elif op == "exit":
            master["alive"] = False
            state["listeners"] = [item for item in state["listeners"] if item.get("pid") != master["pid"]]
            Path(sock).unlink(missing_ok=True)
            save()
            print("Exit request sent.")
        else:
            sys.exit(9)
    else:
        sys.exit(10)
elif name == "ss":
    assert args == ["-H", "-ltnp"]
    for number in range(state.get("ss_padding", 0)):
        print('LISTEN 0 128 127.0.0.1:%d 0.0.0.0:* users:(("fake",pid=123,fd=5))' % (30000 + number))
    for item in state["listeners"]:
        pid = item.get("pid")
        process = 'users:(("ssh",pid=%s,fd=5))' % pid if pid else ""
        host = "[" + item["host"] + "]" if ":" in item["host"] else item["host"]
        print("LISTEN 0 128 %s:%d 0.0.0.0:* %s" % (host, item["port"], process))
elif name == "xdg-open":
    print("xdg-open must never be called", file=sys.stderr)
    sys.exit(99)
elif name in ("google-chrome-stable", "google-chrome-beta", "google-chrome-unstable",
              "chromium", "chromium-browser", "firefox", "microsoft-edge-stable",
              "brave-browser"):
    if state.get(name + "_fail"):
        sys.exit(5)
    if state.get("browser_sleep"):
        state["browser_pid"] = os.getpid()
        save()
        time.sleep(state["browser_sleep"])
elif name == "code":
    if state.get("code_fail"):
        print("code GUI failure", file=sys.stderr)
        sys.exit(5)
else:
    sys.exit(11)
'''


class HelperCase(unittest.TestCase):
    HOSTNAME = LAPTOP_HOST

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.hostname = self.HOSTNAME
        self.peer = HOST_ROLES[self.HOSTNAME]
        self.remote_code = "ssh-remote+" + self.peer["alias"]
        self.runtime = self.root / "runtime"
        self.runtime.mkdir(mode=0o700)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        fake = self.bin / "fake"
        fake.write_text(FAKE.replace("#!PYTHON", "#!" + sys.executable))
        fake.chmod(0o755)
        for name in ("ssh", "ss", "systemctl", "loginctl", "xset", "xdg-settings", "xdg-open",
                     "google-chrome-stable", "firefox", "code"):
            (self.bin / name).symlink_to(fake)
        (self.bin / "python3").symlink_to(sys.executable)
        self.state_path = self.root / "state.json"
        self.log_path = self.root / "log.jsonl"
        self.state = {"next_pid": 4001, "masters": {}, "listeners": []}
        self.save()
        self.env = os.environ.copy()
        self.env.update({"PATH": str(self.bin) + ":" + self.env["PATH"],
                         "FAKE_STATE": str(self.state_path), "FAKE_LOG": str(self.log_path),
                         "FAKE_PEER": json.dumps(self.peer),
                         "TEST_RUNTIME": str(self.runtime), "DISPLAY": ":99",
                         "XAUTHORITY": "/wrong/ssh/authority", "HOME": str(self.root)})

    def tearDown(self):
        self.assertFalse(self.calls("xdg-open"), "xdg-open tripwire was called")

    def save(self):
        self.state_path.write_text(json.dumps(self.state))

    def logs(self):
        return [json.loads(line) for line in self.log_path.read_text().splitlines()] if self.log_path.exists() else []

    def calls(self, name, operation=None):
        found = [item for item in self.logs() if item["name"] == name]
        if operation:
            found = [item for item in found if operation in item["args"]]
        return found

    def start(self, request, *, browser_grace=None):
        process = subprocess.Popen(helper_command(self.hostname, browser_grace=browser_grace),
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, env=self.env)
        data = {"phase": "request", "prepareDeadlineMs": 15000, **request, "version": 2}
        process.stdin.write(json.dumps(data).encode() + b"\n")
        process.stdin.flush()
        return process

    def finish(self, process, prepared):
        self.assertEqual(prepared["phase"], "prepared", prepared)
        self.assertEqual(prepared["version"], 2)
        process.stdin.write(json.dumps({"version": 2, "phase": "commit", "nonce": prepared["nonce"]}).encode() + b"\n")
        process.stdin.flush()
        result = json.loads(process.stdout.readline())
        process.wait(timeout=20)
        process.stdin.close()
        self.assertEqual(process.stderr.read(), b"")
        process.stderr.close()
        process.stdout.close()
        self.assertEqual((result["version"], result["phase"]), (2, "result"))
        return process.returncode, result

    def invoke(self, request):
        if isinstance(request, bytes):
            process = subprocess.run(helper_command(self.hostname), input=request, env=self.env,
                                     capture_output=True, timeout=20)
            self.assertEqual(process.stderr, b"")
            return process.returncode, json.loads(process.stdout)
        process = self.start(request)
        first = json.loads(process.stdout.readline())
        if first["phase"] == "result":
            process.wait(timeout=20)
            process.stdin.close()
            self.assertEqual(process.stderr.read(), b"")
            process.stderr.close()
            process.stdout.close()
            return process.returncode, first
        return self.finish(process, first)

    def web(self, url, *, backend_host=None):
        return self.invoke({"action": "open-web", "url": url,
                            **({"backendHost": backend_host} if backend_host is not None else {})})

    def test_os_hostname_selects_case_insensitive_peer_despite_inherited_environment(self):
        self.hostname = self.HOSTNAME.swapcase()
        other = COMPANY_HOST if self.HOSTNAME == LAPTOP_HOST else LAPTOP_HOST
        self.env.update({"HOSTNAME": other, "SSH_CONNECTION": "203.0.113.9 1 203.0.113.8 22",
                         "SSH_CLIENT": "203.0.113.9 1 22", "SSH_TTY": "/dev/pts/99"})
        path = "/opposite-server/主机 空格%#?.py"
        status, result = self.invoke({"action": "open-file", "kind": "file", "path": path, "line": 9})
        self.assertEqual(status, 0, result)
        self.assertEqual(self.calls("code")[-1]["args"],
                         ["--remote", self.remote_code, "--new-window", "--goto", "--file-uri",
                          "vscode-remote://" + self.remote_code + quote(path, safe="/") + ":9"])
        self.assertEqual(self.web("https://localhost:8745/os-host?x=1#f", backend_host="::1")[0], 0)
        self.assertEqual(self.calls("ssh", "-G")[-1]["args"], ["-G", self.peer["alias"]])
        self.assertEqual(self.calls("ssh", "-M")[-1]["args"][-1], self.peer["alias"])
        self.assertEqual(self.calls("xset")[-1]["display"], ":1")
        self.assertEqual(self.calls("code")[-1]["display"], ":1")

    def test_unknown_os_hostname_refuses_all_paired_work_before_gui_or_transport(self):
        self.env["HOSTNAME"] = self.HOSTNAME
        self.env["SSH_CONNECTION"] = "198.51.100.23 1234 203.0.113.64 22"
        for hostname in ("unpaired-desktop", self.HOSTNAME + ".example", ""):
            self.hostname = hostname
            for request in ({"action": "open-file", "kind": "file", "path": "/server/file.py"},
                            {"action": "open-file", "kind": "directory", "path": "/server/project.v2"},
                            {"action": "open-web", "url": "http://127.0.0.1:8743/path"},
                            {"action": "open-web", "url": "https://example.org/path"}):
                with self.subTest(hostname=hostname, request=request):
                    status, result = self.invoke(request)
                    self.assertEqual((status, result["phase"], result["error"]["code"]),
                                     (1, "result", "unsupported_host"), result)
                    self.assertIn(repr(hostname), result["error"]["message"])
                    self.assertNotIn("nonce", result)
        self.assertEqual(self.logs(), [])
        self.assertFalse((self.runtime / "pi-client-link-helper").exists())

    def test_request_cannot_select_server_alias_or_remote_authority(self):
        for key in ("serverAlias", "sshAlias", "hostname", "remoteAuthority"):
            for request in ({"action": "open-file", "kind": "file", "path": "/server/file.py"},
                            {"action": "open-web", "url": "http://127.0.0.1:8743/path"}):
                with self.subTest(key=key, request=request):
                    status, result = self.invoke({**request, key: "arbitrary-host"})
                    self.assertEqual((status, result["error"]["code"]), (1, "invalid_request"))
        self.assertEqual(self.logs(), [])

    def test_remote_resources_ignore_same_spelling_local_decoys_and_preserve_literal_unicode(self):
        local_folder = self.root / "Makefile"
        local_folder.mkdir()
        local_file = self.root / "project.v2"
        local_file.write_text("desktop decoy, not the server resource")
        local_link = self.root / "linked-resource"
        local_link.symlink_to(local_folder, target_is_directory=True)
        missing = self.root / "not-on-this-desktop" / "主机 空格 'quotes'%#?.py"
        requests = [{"kind": "file", "path": str(local_folder)},
                    {"kind": "directory", "path": str(local_file)},
                    {"kind": "file", "path": str(local_link)},
                    {"kind": "file", "path": str(missing), "line": 31, "column": 7}]
        for target in requests:
            with self.subTest(target=target):
                before = len(self.calls("code"))
                process = self.start({"action": "open-file", **target})
                prepared = json.loads(process.stdout.readline())
                self.assertEqual(set(prepared), {"version", "phase", "nonce", "ok", "action", "path", "kind"})
                self.assertEqual((prepared["path"], prepared["kind"]), (target["path"], target["kind"]))
                self.assertEqual(len(self.calls("code")), before)
                status, result = self.finish(process, prepared)
                self.assertEqual(status, 0, result)
                self.assertEqual(set(result), {"version", "phase", "ok", "action", "path", "kind"})
                expected = ["--remote", self.remote_code, "--new-window"]
                uri = "vscode-remote://" + self.remote_code + quote(target["path"], safe="/")
                if "line" in target:
                    expected.append("--goto")
                    uri += f":{target['line']}:{target['column']}"
                expected.extend(["--file-uri" if target["kind"] == "file" else "--folder-uri", uri])
                self.assertEqual(self.calls("code")[-1]["args"], expected)
        self.assertFalse(self.calls("ssh"))
        self.assertFalse(self.calls("google-chrome-stable"))
        self.assertTrue(local_folder.is_dir())
        self.assertEqual(local_file.read_text(), "desktop decoy, not the server resource")
        self.assertFalse(missing.exists())

    def test_file_target_and_command_never_stat_the_remote_path(self):
        helper = runpy.run_path(str(HELPER))
        with patch("socket.gethostname", return_value=self.hostname):
            alias = helper["paired_server"]()
        for kind in ("file", "directory"):
            target = {"kind": kind, "path": "/server-only/不可读取%#?.data"}
            with patch("os.stat", side_effect=AssertionError("remote path stat on desktop")), \
                    patch("os.lstat", side_effect=AssertionError("remote path lstat on desktop")), \
                    patch("os.access", side_effect=AssertionError("remote path access on desktop")):
                helper["file_target"](target)
                argv, result = helper["file_command"](target, alias)
            self.assertEqual(argv[2], self.remote_code)
            self.assertEqual(result["kind"], kind)

    def test_tunnel_reuse_requires_selected_opposite_literal_identity(self):
        url = "http://127.0.0.1:8743/paired?x=1#literal"
        status, result = self.web(url)
        self.assertEqual(status, 0, result)
        record = next((self.runtime / "pi-client-link-helper").glob("e-*.json"))
        original = json.loads(record.read_text())
        identity = {key: self.peer[key] for key in ("host", "user", "port")}
        self.assertEqual(original["identity"], identity)
        self.assertEqual(self.web(url)[1]["tunnel"]["reused"], True)
        for key, value in (("host", "192.0.2.99"), ("user", "other-user"), ("port", 2223)):
            with self.subTest(field=key):
                changed = {**original, "identity": {**identity, key: value}}
                record.write_text(json.dumps(changed))
                status, result = self.web(url)
                self.assertEqual((status, result["error"]["code"]), (1, "tunnel_unverified"))
                self.assertEqual(json.loads(record.read_text()), changed)
        record.write_text(json.dumps(original))
        # A role change in the same fixture runtime must not adopt the prior
        # opposite server's live master or close its listeners.
        other = COMPANY_HOST if self.HOSTNAME == LAPTOP_HOST else LAPTOP_HOST
        self.hostname = other
        self.env["FAKE_PEER"] = json.dumps(HOST_ROLES[other])
        status, result = self.web(url)
        self.assertEqual((status, result["error"]["code"]), (1, "tunnel_unverified"))
        self.assertEqual(self.calls("ssh", "-G")[-1]["args"], ["-G", HOST_ROLES[other]["alias"]])
        self.assertEqual(json.loads(record.read_text()), original)
        self.assertEqual(len(self.calls("ssh", "-M")), 1)
        self.assertEqual(len(self.calls("ssh", "forward")), 1)
        self.assertEqual(len(self.calls("google-chrome-stable")), 2)
        self.assertFalse(self.calls("ssh", "exit"))
        self.assertEqual(json.loads(self.state_path.read_text())["masters"][original["socket"]]["alive"], True)

    def test_port_conflict_diagnostic_names_desktop_without_assuming_laptop(self):
        self.state["listeners"] = [{"host": "::1", "port": 8743, "pid": 999}]
        self.save()
        status, result = self.web("http://127.0.0.1:8743/conflict")
        self.assertEqual((status, result["error"]["code"]), (1, "port_conflict"))
        self.assertIn("desktop loopback port 127.0.0.1:8743", result["error"]["message"])
        self.assertEqual(json.loads(self.state_path.read_text())["listeners"], self.state["listeners"])
        self.assertFalse(self.calls("ssh", "-M"))
        self.assertFalse(self.calls("google-chrome-stable"))

    def test_exact_origin_is_opened_and_reused_only_with_verified_record(self):
        original = "http://127.0.0.1:8743/a%2Fb?q=a%2Bb&x=1#frag%20raw"
        status, first = self.web(original)
        self.assertEqual(status, 0, (first, self.logs()))
        self.assertEqual(first["openedUrl"], original)
        self.assertEqual(first["tunnel"], {"remoteHost": "127.0.0.1", "remotePort": 8743,
                                            "localPort": 8743, "reused": False})
        self.assertEqual(self.calls("google-chrome-stable")[-1]["args"], ["--new-window", original])
        self.assertEqual(self.calls("xset")[-1]["display"], ":1")
        self.assertEqual(self.calls("xset")[-1]["authority"], str(self.runtime / "Xauthority"))
        status, again = self.web("http://127.0.0.1:8743/other?still=1#yes")
        self.assertEqual(status, 0)
        self.assertEqual(again["openedUrl"], "http://127.0.0.1:8743/other?still=1#yes")
        self.assertTrue(again["tunnel"]["reused"])
        self.assertEqual(again["tunnel"]["localPort"], 8743)
        self.assertEqual(len(self.calls("ssh", "-M")), 1)
        self.assertEqual(len(self.calls("ssh", "forward")), 1)
        record = next((self.runtime / "pi-client-link-helper").glob("e-*.json"))
        self.assertEqual(record.stat().st_mode & 0o777, 0o600)
        self.assertEqual(json.loads(record.read_text())["version"], 4)
        self.assertEqual(json.loads(record.read_text())["endpoint"],
                         {"host": "127.0.0.1", "port": 8743, "backendHost": "127.0.0.1"})
        state = json.loads(self.state_path.read_text())
        self.assertEqual({item["host"] for item in state["listeners"]}, {"127.0.0.1", "::1"})
        self.assertEqual({item["remote_host"] for item in state["listeners"]}, {"127.0.0.1"})

    def test_original_port_decoy_and_company_absolute_redirect_refuse_before_browser(self):
        class Decoy(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200); self.end_headers(); self.wfile.write(b"Y9000P-DECOY")
            def log_message(self, *args):
                pass

        with HTTPServer(("127.0.0.1", 0), Decoy) as decoy:
            decoy_thread = threading.Thread(target=decoy.serve_forever, daemon=True)
            decoy_thread.start()
            port = decoy.server_port
            class Company(BaseHTTPRequestHandler):
                def do_GET(self):
                    self.send_response(302)
                    self.send_header("Location", f"http://127.0.0.1:{port}/done")
                    self.end_headers()
                def log_message(self, *args):
                    pass
            # The same port is free on the other loopback address in this
            # fixture, representing the separate company endpoint.
            with HTTPServer(("127.0.0.2", port), Company) as company_server:
                company_thread = threading.Thread(target=company_server.serve_forever, daemon=True)
                company_thread.start()
                company = http.client.HTTPConnection("127.0.0.2", port, timeout=2)
                company.request("GET", "/start")
                redirect = company.getresponse()
                self.assertEqual((redirect.status, redirect.getheader("Location")),
                                 (302, f"http://127.0.0.1:{port}/done"))
                redirect.read(); company.close()
                decoy_client = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
                decoy_client.request("GET", "/done")
                self.assertEqual(decoy_client.getresponse().read(), b"Y9000P-DECOY")
                decoy_client.close()
                self.state["listeners"] = [{"host": "127.0.0.1", "port": port, "pid": 999}]
                self.save()
                status, result = self.web(f"http://127.0.0.1:{port}/start")
                self.assertEqual((status, result["error"]["code"]), (1, "port_conflict"))
                self.assertIn("remove the conflicting listener", result["error"]["message"])
                self.assertFalse(self.calls("google-chrome-stable"))
                self.assertFalse(self.calls("ssh", "-M"))
                self.assertFalse(self.calls("ssh", "forward"))
                company_server.shutdown()
                company_thread.join(timeout=2)
            decoy.shutdown()
            decoy_thread.join(timeout=2)

    def test_stale_control_socket_recreates_forward(self):
        status, first = self.web("http://127.0.0.1:8743/")
        self.assertEqual(status, 0)
        self.state = json.loads(self.state_path.read_text())
        old_sock = next(iter(self.state["masters"]))
        self.state["masters"][old_sock]["alive"] = False
        self.state["listeners"] = []
        self.save()
        status, next_result = self.web("http://127.0.0.1:8743/new")
        self.assertEqual(status, 0, (next_result, self.logs()))
        self.assertFalse(next_result["tunnel"]["reused"])
        self.assertFalse(Path(old_sock).exists())
        self.assertEqual(len(self.calls("ssh", "-M")), 2)
        self.assertEqual(len(self.calls("ssh", "forward")), 2)
        self.assertEqual(next_result["openedUrl"], "http://127.0.0.1:8743/new")

    def test_bind_race_refuses_without_alternate_or_browser(self):
        self.state["race_port"] = 8743
        self.save()
        status, result = self.web("http://127.0.0.1:8743/race")
        self.assertEqual((status, result["error"]["code"]), (1, "port_conflict"))
        self.assertEqual(len(self.calls("ssh", "forward")), 1)
        self.assertEqual(len(self.calls("ssh", "exit")), 1)
        self.assertFalse(self.calls("google-chrome-stable"))

    def test_simultaneous_requests_are_serialized(self):
        processes = [self.start({"action": "open-web", "url": "http://127.0.0.1:8743/same"}) for _ in range(2)]
        results = [self.finish(process, json.loads(process.stdout.readline())) for process in processes]
        self.assertTrue(all(status == 0 for status, _ in results), results)
        parsed = [result for _, result in results]
        self.assertEqual(sorted(result["tunnel"]["reused"] for result in parsed), [False, True])
        self.assertEqual(len(self.calls("ssh", "-M")), 1)
        self.assertEqual(len(self.calls("ssh", "forward")), 1)

    def test_live_master_mapping_mismatch_fails_closed(self):
        self.assertEqual(self.web("http://127.0.0.1:8743/")[0], 0)
        self.state = json.loads(self.state_path.read_text())
        self.state["listeners"][0]["pid"] = 999
        self.save()
        status, result = self.web("http://127.0.0.1:8743/again")
        self.assertEqual(status, 1)
        self.assertEqual(result["error"]["code"], "port_conflict")
        self.assertEqual(len(self.calls("google-chrome-stable")), 1)
        self.assertEqual(len(self.calls("ssh", "-M")), 1)

    def test_old_or_alternate_record_cannot_be_reused_as_original_origin(self):
        self.assertEqual(self.web("http://127.0.0.1:8743/")[0], 0)
        record = next((self.runtime / "pi-client-link-helper").glob("e-*.json"))
        original = json.loads(record.read_text())
        for mutation, code in [({"version": 1}, "tunnel_unverified"),
                               ({"version": 2}, "tunnel_unverified"),
                               ({"version": 3}, "tunnel_unverified"),
                               ({"local_port": 54465}, "port_conflict"),
                               ({"endpoint": {"host": "127.0.0.2", "port": 8743,
                                              "backendHost": "127.0.0.1"}}, "tunnel_unverified")]:
            with self.subTest(mutation=mutation):
                record.write_text(json.dumps({**original, **mutation}))
                status, result = self.web("http://127.0.0.1:8743/reuse")
                self.assertEqual((status, result["error"]["code"]), (1, code))
        self.assertEqual(len(self.calls("google-chrome-stable")), 1)
        self.assertEqual(len(self.calls("ssh", "-M")), 1)

    def test_master_pins_snapshot_when_alias_changes_before_creation(self):
        real_ssh = shutil.which("ssh")
        self.assertIsNotNone(real_ssh)
        config = self.root / "ssh-config"
        identity_file = self.root / "configured-id"
        config.write_text("Host {alias}\n HostName {host}\n User {user}\n Port {port}\n".format(**self.peer) +
                          f" IdentityFile {identity_file}\n")
        self.env.update({"FAKE_SSH_CONFIG": str(config), "REAL_SSH": real_ssh})
        self.state["mutate_alias_after_snapshot"] = (
            f"Host {self.peer['alias']}\n HostName 203.0.113.88\n User wrong\n Port 2200\n"
            f" IdentityFile {identity_file}\n")
        self.save()
        status, result = self.web("http://127.0.0.1:8743/pin")
        self.assertEqual(status, 0, result)
        effective = json.loads(self.state_path.read_text())["master_effective_config"]
        self.assertIn(f"\nhostname {self.peer['host']}\n", "\n" + effective)
        self.assertIn(f"\nuser {self.peer['user']}\n", "\n" + effective)
        self.assertIn(f"\nport {self.peer['port']}\n", "\n" + effective)
        self.assertIn("\nstricthostkeychecking true\n", "\n" + effective)
        self.assertIn(f"\nidentityfile {identity_file}\n", "\n" + effective)
        self.assertIn("HostName 203.0.113.88", config.read_text())
        self.assertEqual(self.calls("google-chrome-stable")[-1]["args"],
                         ["--new-window", "http://127.0.0.1:8743/pin"])

    def test_noncomparable_ssh_identity_fails_before_master_or_browser(self):
        for config in ("hostname company.example\nuser conley\nport 22",
                       "hostname 198.51.100.23\nhostname 203.0.113.88\nuser conley\nport 22",
                       "hostname 198.51.100.23\nuser conley\nport 22\nport 2222",
                       "hostname 198.51.100.23\nuser name with spaces\nport 22"):
            with self.subTest(config=config):
                self.state["ssh_config_lines"] = config
                self.save()
                status, result = self.web("http://127.0.0.1:8743/no-identity")
                self.assertEqual((status, result["error"]["code"]), (1, "ssh_config_failed"))
        self.assertFalse(self.calls("ssh", "-M"))
        self.assertFalse(self.calls("google-chrome-stable"))

    def test_ssh_failure_and_forward_failure_do_not_launch_browser(self):
        self.state["master_fail"] = True
        self.save()
        status, result = self.web("http://127.0.0.1:8743/")
        self.assertEqual((status, result["error"]["code"]), (1, "ssh_master_failed"))
        self.assertFalse(self.calls("google-chrome-stable"))
        self.state.pop("master_fail")
        self.state["forward_fail"] = True
        self.save()
        status, result = self.web("http://127.0.0.1:8743/")
        self.assertEqual((status, result["error"]["code"]), (1, "ssh_forward_failed"))
        self.assertFalse(self.calls("google-chrome-stable"))
        self.assertEqual(len(self.calls("ssh", "exit")), 1)

    def test_successful_mux_reply_without_listener_does_not_open_browser(self):
        self.state["forward_no_listener"] = True
        self.save()
        status, result = self.web("http://127.0.0.1:8743/")
        self.assertEqual((status, result["error"]["code"]), (1, "tunnel_unverified"))
        self.assertFalse(self.calls("google-chrome-stable"))
        self.assertEqual(len(self.calls("ssh", "exit")), 1)

    def test_no_launch_without_gui_and_explicit_code_error(self):
        self.state["gui_missing"] = True
        self.save()
        status, result = self.web("http://127.0.0.1:8743/")
        self.assertEqual((status, result["error"]["code"]), (1, "gui_unavailable"))
        self.assertFalse(self.calls("ssh"))
        self.assertFalse(self.calls("xdg-settings"))
        self.state.pop("gui_missing")
        self.state["code_fail"] = True
        self.save()
        status, result = self.invoke({"version": 1, "action": "open-file", "kind": "file", "path": "/srv/source.py"})
        self.assertEqual((status, result["error"]["code"]), (1, "code_failed"))

    def test_default_chrome_opens_a_new_window_only_after_commit(self):
        url = "https://example.org/chrome"
        process = self.start({"action": "open-web", "url": url})
        prepared = json.loads(process.stdout.readline())
        self.assertEqual(prepared["phase"], "prepared", prepared)
        self.assertEqual(self.calls("xdg-settings")[-1]["args"], ["get", "default-web-browser"])
        self.assertFalse(self.calls("google-chrome-stable"))
        status, result = self.finish(process, prepared)
        self.assertEqual(status, 0, result)
        self.assertEqual(self.calls("google-chrome-stable")[-1]["args"], ["--new-window", url])

    def test_firefox_default_opens_a_new_window(self):
        self.state["default_browser"] = "firefox.desktop"
        self.save()
        url = "https://example.org/firefox"
        status, result = self.web(url)
        self.assertEqual(status, 0, result)
        self.assertEqual(self.calls("firefox")[-1]["args"], ["--new-window", url])
        self.assertFalse(self.calls("google-chrome-stable"))

    def test_unknown_missing_and_invalid_default_browser_fail_before_prepared(self):
        cases = [
            ("unknown", "org.gnome.Epiphany.desktop", False),
            ("missing", "google-chrome.desktop", True),
            ("empty", "", False),
            ("multiple", "google-chrome.desktop\nfirefox.desktop", False),
            ("invalid", "../google-chrome.desktop", False),
        ]
        for name, desktop_id, missing_binary in cases:
            with self.subTest(name=name):
                self.state["default_browser"] = desktop_id
                self.save()
                if missing_binary:
                    (self.bin / "google-chrome-stable").unlink()
                    self.env["PATH"] = str(self.bin)
                try:
                    status, result = self.web("https://example.org/unsupported")
                    self.assertEqual((status, result["phase"], result["error"]["code"]),
                                     (1, "result", "browser_unsupported"), result)
                    self.assertIn(desktop_id[:20] if desktop_id else "desktop id",
                                  result["error"]["message"])
                    self.assertFalse(self.calls("google-chrome-stable"))
                    self.assertFalse(self.calls("firefox"))
                    self.assertFalse(self.calls("ssh"))
                finally:
                    if missing_binary:
                        (self.bin / "google-chrome-stable").symlink_to(self.bin / "fake")
                        self.env["PATH"] = str(self.bin) + ":" + os.environ["PATH"]

    def test_browser_nonzero_exit_is_reported_after_commit(self):
        self.state["google-chrome-stable_fail"] = True
        self.save()
        process = self.start({"action": "open-web", "url": "https://example.org/fail"})
        prepared = json.loads(process.stdout.readline())
        self.assertEqual(prepared["phase"], "prepared", prepared)
        status, result = self.finish(process, prepared)
        self.assertEqual((status, result["error"]["code"]), (1, "browser_failed"))
        self.assertEqual(len(self.calls("google-chrome-stable")), 1)

    def test_long_lived_browser_is_handed_off_without_being_killed(self):
        self.state["browser_sleep"] = 30
        self.save()
        process = self.start({"action": "open-web", "url": "https://example.org/primary"},
                             browser_grace=0.05)
        prepared = json.loads(process.stdout.readline())
        self.assertEqual(prepared["phase"], "prepared", prepared)
        status, result = self.finish(process, prepared)
        self.assertEqual(status, 0, result)
        browser_pid = json.loads(self.state_path.read_text())["browser_pid"]
        browser_stat = Path(f"/proc/{browser_pid}/stat")
        try:
            self.assertTrue(browser_stat.exists(), "browser was killed at the grace deadline")
            process_state = browser_stat.read_text().split(") ", 1)[1].split()[0]
            self.assertNotIn(process_state, ("Z", "X"), "browser was not alive after helper success")
        finally:
            try:
                os.killpg(browser_pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            until = time.monotonic() + 2
            while browser_stat.exists() and time.monotonic() < until:
                time.sleep(0.01)

    def test_multiple_active_graphical_sessions_fail_before_launch(self):
        self.state["ambiguous_gui"] = True
        self.save()
        status, result = self.web("http://127.0.0.1:8743/")
        self.assertEqual((status, result["error"]["code"]), (1, "gui_unavailable"))
        self.assertFalse(self.calls("ssh"))
        self.assertFalse(self.calls("google-chrome-stable"))

    def test_file_kind_uri_argv_preserves_remote_paths_and_location(self):
        prefix = "vscode-remote://" + self.remote_code
        path = "/company/not-on-laptop/-name with spaces 'quotes'.py"
        status, result = self.invoke({"version": 1, "action": "open-file", "kind": "file",
                                      "path": path, "line": 31, "column": 7})
        self.assertEqual(status, 0)
        self.assertEqual(result["path"], path)
        self.assertEqual(result["kind"], "file")
        self.assertEqual(self.calls("code")[-1]["args"],
                         ["--remote", self.remote_code, "--new-window", "--goto",
                          "--file-uri", prefix + quote(path, safe="/") + ":31:7"])
        self.assertFalse(self.calls("ssh"))
        self.assertFalse(self.calls("google-chrome-stable"))
        for name in ("Makefile", "Dockerfile", "README"):
            path = "/company/" + name
            status, _ = self.invoke({"version": 1, "action": "open-file", "kind": "file", "path": path})
            self.assertEqual(status, 0)
            self.assertEqual(self.calls("code")[-1]["args"],
                             ["--remote", self.remote_code, "--new-window", "--file-uri", prefix + path])
        status, _ = self.invoke({"version": 1, "action": "open-file", "kind": "directory",
                                  "path": "/company/project.v2"})
        self.assertEqual(status, 0)
        self.assertEqual(self.calls("code")[-1]["args"],
                         ["--remote", self.remote_code, "--new-window", "--folder-uri",
                          prefix + "/company/project.v2"])
        colon_path = "/company/report:2026#raw?.txt"
        status, _ = self.invoke({"version": 1, "action": "open-file", "kind": "file", "path": colon_path})
        self.assertEqual(status, 0)
        self.assertEqual(self.calls("code")[-1]["args"][-1], prefix + quote(colon_path, safe="/"))

    def test_real_installed_code_resolver_selects_exact_remote_resource(self):
        self.assertIsNotNone(shutil.which("node"), "installed Code resolver test requires Node")
        self.assertTrue(Path("/usr/share/code/resources/app/out/main.js").is_file())
        requests = [
            {"path": "/company/Makefile", "kind": "file"},
            {"path": "/company/Dockerfile", "kind": "file"},
            {"path": "/company/README", "kind": "file"},
            {"path": "/company/project.v2", "kind": "directory"},
            {"path": "/company/a b#c?.py", "kind": "file", "line": 31, "column": 7},
            {"path": "/company/report:2026#raw?.txt", "kind": "file"},
            {"path": "/server-only/主机 空格 'quotes'%#?.py", "kind": "file", "line": 9},
            {"path": "/server-only/项目 空格%#?.v2", "kind": "directory"},
        ]
        for target in requests:
            status, result = self.invoke({"version": 1, "action": "open-file", **target})
            self.assertEqual(status, 0, result)
        argvs = [call["args"] for call in self.calls("code")]
        probe = subprocess.run(["node", str(MODULE / "tests/code_resolver_probe.cjs")],
                               input=json.dumps(argvs), text=True, capture_output=True, timeout=10)
        self.assertEqual(probe.returncode, 0, probe.stderr)
        resolved = json.loads(probe.stdout)
        self.assertEqual([(resource["kind"], resource["path"]) for resource in resolved],
                         [(target["kind"], target["path"]) for target in requests])
        self.assertEqual(resolved[4]["line"], 31)
        self.assertEqual(resolved[4]["column"], 7)
        self.assertEqual(resolved[6]["line"], 9)
        self.assertEqual(resolved[6]["column"], 1)  # Code defaults a line-only goto to column 1.
        self.assertEqual({resource["remoteAuthority"] for resource in resolved},
                         {self.remote_code})

    def test_colon_path_with_goto_and_directory_location_fail_before_code(self):
        for path in ("/company/report:2026", "/company/report:suffix.py"):
            with self.subTest(path=path):
                status, result = self.invoke({"version": 1, "action": "open-file", "kind": "file",
                                              "path": path, "line": 7})
                self.assertEqual(status, 1)
                self.assertEqual(result["error"]["code"], "unsupported_target")
        status, result = self.invoke({"version": 1, "action": "open-file", "kind": "directory",
                                      "path": "/company/project", "line": 3})
        self.assertEqual((status, result["error"]["code"]), (1, "invalid_request"))
        self.assertFalse(self.calls("code"))
        self.assertFalse(self.calls("systemctl"))

    def test_default_http_port_preserves_url_or_refuses_low_port(self):
        can_bind = True
        for family, host in ((socket.AF_INET, "127.0.0.1"), (socket.AF_INET6, "::1")):
            with socket.socket(family) as probe:
                try:
                    probe.bind((host, 80))
                except OSError as error:
                    self.assertIn(error.errno, (13, 98))  # EACCES or EADDRINUSE
                    can_bind = False
        original = "http://127.0.0.1/no-explicit-port?q=1#f"
        status, result = self.web(original)
        if can_bind:
            self.assertEqual(status, 0, result)
            self.assertEqual(result["tunnel"]["remotePort"], 80)
            self.assertEqual(result["tunnel"]["localPort"], 80)
            self.assertEqual(result["openedUrl"], original)
            self.assertEqual(self.calls("google-chrome-stable")[-1]["args"], ["--new-window", original])
        else:
            self.assertEqual((status, result["error"]["code"]), (1, "port_conflict"))
            self.assertFalse(self.calls("google-chrome-stable"))

    def test_ipv6_loopback_binds_both_families_to_ipv6_backend(self):
        status, result = self.web("http://[::1]:8743/hello?x=1#f")
        self.assertEqual(status, 0, result)
        self.assertEqual(result["openedUrl"], "http://[::1]:8743/hello?x=1#f")
        self.assertEqual(result["tunnel"]["remoteHost"], "::1")
        self.assertIn("localhost:8743:[::1]:8743", self.calls("ssh", "forward")[-1]["args"])
        state = json.loads(self.state_path.read_text())
        self.assertEqual({item["host"] for item in state["listeners"]}, {"127.0.0.1", "::1"})
        self.assertEqual({item["remote_host"] for item in state["listeners"]}, {"::1"})

    def test_nonstandard_loopback_addresses_refuse_before_prepared_or_gui(self):
        for url in ("http://127.0.0.2:8743/path", "http://127.255.255.254:8743/path",
                    "http://[::ffff:7f00:1]:8743/mapped", "http://[::ffff:7f00:2]:8743/mapped"):
            with self.subTest(url=url):
                status, result = self.web(url)
                self.assertEqual((status, result["phase"], result["error"]["code"]),
                                 (1, "result", "unsupported_target"))
        self.assertEqual(self.logs(), [])
        status, result = self.web("http://example.org/a?q=2#x")
        self.assertEqual(status, 0)
        self.assertIsNone(result["tunnel"])
        self.assertEqual(result["openedUrl"], "http://example.org/a?q=2#x")
        self.assertFalse(self.calls("ssh", "-M"))

    def test_company_chosen_backend_binds_both_exact_client_families_and_preserves_url(self):
        for url, backend, port in [
            ("http://localhost:8744/a%2Fb?q=1#v4", "127.0.0.1", 8744),
            ("https://localhost:8745/a%2Fb?q=one%2Btwo#frag%20x", "::1", 8745),
        ]:
            with self.subTest(backend=backend):
                process = self.start({"action": "open-web", "url": url, "backendHost": backend})
                prepared = json.loads(process.stdout.readline())
                self.assertEqual(prepared["phase"], "prepared", prepared)
                self.assertEqual(prepared["openedUrl"], url)
                self.assertEqual(prepared["tunnel"], {"remoteHost": backend, "remotePort": port,
                                                       "localPort": port, "reused": False})
                record = self.runtime / "pi-client-link-helper"
                records = [json.loads(item.read_text()) for item in record.glob("e-*.json")]
                matching = [item for item in records if item["endpoint"]["port"] == port]
                self.assertEqual(len(matching), 1)
                self.assertEqual(matching[0]["version"], 3)
                self.assertEqual(matching[0]["endpoint"], {"host": "localhost", "port": port,
                                                            "backendHost": backend})
                state = json.loads(self.state_path.read_text())
                exact = [item for item in state["listeners"] if item["port"] == port]
                self.assertEqual({item["host"] for item in exact}, {"127.0.0.1", "::1"})
                self.assertEqual({item["pid"] for item in exact}, {matching[0]["pid"]})
                self.assertEqual({item["remote_host"] for item in exact}, {backend})
                self.assertFalse(any(call["args"] == ["--new-window", url] for call in self.calls("google-chrome-stable")))
                status, result = self.finish(process, prepared)
                self.assertEqual(status, 0, result)
                self.assertEqual(result["openedUrl"], url)
                self.assertEqual(result["tunnel"], prepared["tunnel"])
                self.assertEqual(self.calls("google-chrome-stable")[-1]["args"], ["--new-window", url])
                expected = f"localhost:{port}:{'[' + backend + ']' if ':' in backend else backend}:{port}"
                self.assertIn(expected, self.calls("ssh", "forward")[-1]["args"])
        self.assertEqual(len(self.calls("ssh", "-M")), 2)

    def test_localhost_backend_missing_or_other_targets_with_backend_fail_before_gui(self):
        for url, field in [
            ("https://localhost:8744/a?#", {}),
            ("http://localhost:8744/", {"backendHost": None}),
            ("http://localhost:8744/", {"backendHost": "localhost"}),
            ("http://localhost:8744/", {"backendHost": "127.0.0.2"}),
            ("http://localhost:8744/", {"backendHost": "[::1]"}),
            ("http://localhost:8744/", {"backendHost": 4}),
            ("http://127.0.0.1:8744/", {"backendHost": "127.0.0.1"}),
            ("http://[::1]:8744/", {"backendHost": "::1"}),
            ("https://example.org/", {"backendHost": "127.0.0.1"}),
        ]:
            with self.subTest(url=url, field=field):
                status, result = self.invoke({"action": "open-web", "url": url, **field})
                self.assertEqual((status, result["error"]["code"]), (1, "invalid_request"))
        self.assertEqual(self.logs(), [])

    def test_explicit_ip_other_family_decoy_blocks_same_port_redirect(self):
        class Decoy(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200); self.end_headers(); self.wfile.write(b"Y9000P-DECOY")
            def log_message(self, *args):
                pass

        # The company backend is a separate 127.0.0.2 fixture; the ::1 socket
        # stands for the other Y9000p family of an initial 127.0.0.1 URL.
        with IPv6HTTPServer(("::1", 0), Decoy) as decoy:
            port = decoy.server_port
            class Company(BaseHTTPRequestHandler):
                def do_GET(self):
                    self.send_response(302)
                    self.send_header("Location", f"http://localhost:{port}/done")
                    self.end_headers()
                def log_message(self, *args):
                    pass
            with HTTPServer(("127.0.0.2", port), Company) as company:
                thread = threading.Thread(target=company.serve_forever, daemon=True)
                thread.start()
                try:
                    client = http.client.HTTPConnection("127.0.0.2", port, timeout=2)
                    client.request("GET", "/start")
                    redirect = client.getresponse()
                    self.assertEqual((redirect.status, redirect.getheader("Location")),
                                     (302, f"http://localhost:{port}/done"))
                    redirect.read(); client.close()
                    self.state["listeners"] = [{"host": "::1", "port": port, "pid": 999}]
                    self.save()
                    status, result = self.web(f"http://127.0.0.1:{port}/start")
                    self.assertEqual((status, result["phase"], result["error"]["code"]),
                                     (1, "result", "port_conflict"))
                    self.assertIn("remove the conflicting listener", result["error"]["message"])
                    self.assertFalse(self.calls("ssh", "-M"))
                    self.assertFalse(self.calls("google-chrome-stable"))
                finally:
                    company.shutdown(); thread.join(timeout=2)

    def test_explicit_ipv6_other_family_decoy_blocks_same_port_redirect(self):
        self.state["listeners"] = [{"host": "127.0.0.1", "port": 8743, "pid": 999}]
        self.save()
        status, result = self.web("http://[::1]:8743/start")
        self.assertEqual((status, result["phase"], result["error"]["code"]),
                         (1, "result", "port_conflict"))
        self.assertFalse(self.calls("ssh", "-M"))
        self.assertFalse(self.calls("google-chrome-stable"))

    def test_explicit_ip_opposite_family_bind_race_and_precommit_takeover(self):
        for source, other, port in (("127.0.0.1", "::1", 8761),
                                    ("[::1]", "127.0.0.1", 8762)):
            with self.subTest(source=source):
                self.state = {"next_pid": 4001, "masters": {}, "listeners": [],
                              "race_port": port, "race_host": other}
                self.save()
                url = f"http://{source}:{port}/start"
                status, result = self.web(url)
                self.assertEqual((status, result["error"]["code"]), (1, "port_conflict"))
                self.assertIn("remove the conflicting listener", result["error"]["message"])
                self.assertFalse(self.calls("google-chrome-stable"))
                raced = json.loads(self.state_path.read_text())["listeners"]
                self.assertEqual([(item["host"], item["port"], item["pid"]) for item in raced],
                                 [(other, port, 999)])

                # A free pair must also remain jointly owned after PREPARED.
                self.state = {"next_pid": 4001, "masters": {}, "listeners": []}
                self.save()
                process = self.start({"action": "open-web", "url": url})
                prepared = json.loads(process.stdout.readline())
                self.assertEqual(prepared["phase"], "prepared", prepared)
                current = json.loads(self.state_path.read_text())
                self.assertEqual({item["host"] for item in current["listeners"] if item["port"] == port},
                                 {"127.0.0.1", "::1"})
                for listener in current["listeners"]:
                    if listener["host"] == other and listener["port"] == port:
                        listener["pid"] = 999
                self.state = current
                self.save()
                status, result = self.finish(process, prepared)
                self.assertEqual((status, result["error"]["code"]), (1, "port_conflict"))
                self.assertFalse(self.calls("google-chrome-stable"))

    def test_explicit_ip_redirect_reaches_company_on_opposite_family(self):
        # Fake SSH attests its requested -L and PID ownership; these local HTTP
        # relays model the data plane without touching a physical Y9000p install.
        # Force the redirect to the opposite localhost family a browser may use.
        for source, backend, redirected_to in (("127.0.0.1", "127.0.0.1", "::1"),
                                                ("[::1]", "::1", "127.0.0.1")):
            with self.subTest(source=source):
                requests = []
                class Company(BaseHTTPRequestHandler):
                    def do_GET(self):
                        requests.append((self.path, self.headers["Host"]))
                        if self.path == "/start":
                            self.send_response(302)
                            self.send_header("Location", f"http://localhost:{port}/done")
                            self.end_headers()
                        else:
                            self.send_response(200)
                            self.end_headers()
                            self.wfile.write(b"COMPANY-FINAL-BODY")
                    def log_message(self, *args):
                        pass
                class Relay(BaseHTTPRequestHandler):
                    def do_GET(self):
                        upstream = http.client.HTTPConnection("127.0.0.2", port, timeout=2)
                        upstream.request("GET", self.path, headers={"Host": self.headers["Host"]})
                        reply = upstream.getresponse()
                        body = reply.read()
                        self.send_response(reply.status)
                        if reply.getheader("Location"):
                            self.send_header("Location", reply.getheader("Location"))
                        self.end_headers()
                        self.wfile.write(body)
                        upstream.close()
                    def log_message(self, *args):
                        pass
                with HTTPServer(("127.0.0.1", 0), Relay) as ipv4:
                    port = ipv4.server_port
                    with IPv6HTTPServer(("::1", port), Relay) as ipv6:
                        with HTTPServer(("127.0.0.2", port), Company) as company:
                            servers = (ipv4, ipv6, company)
                            threads = [threading.Thread(target=s.serve_forever, daemon=True) for s in servers]
                            for thread in threads:
                                thread.start()
                            try:
                                url = f"http://{source}:{port}/start"
                                opened_before = len(self.calls("google-chrome-stable"))
                                process = self.start({"action": "open-web", "url": url})
                                prepared = json.loads(process.stdout.readline())
                                self.assertEqual(prepared["phase"], "prepared", prepared)
                                self.assertEqual(prepared["openedUrl"], url)
                                self.assertEqual(prepared["tunnel"]["remoteHost"], backend)
                                self.assertEqual(len(self.calls("google-chrome-stable")), opened_before)
                                listener_state = json.loads(self.state_path.read_text())["listeners"]
                                exact = [item for item in listener_state if item["port"] == port]
                                self.assertEqual(len(exact), 2)
                                self.assertEqual({item["host"] for item in exact}, {"127.0.0.1", "::1"})
                                self.assertEqual({item["pid"] for item in exact}, {exact[0]["pid"]})
                                self.assertEqual({item["remote_host"] for item in exact}, {backend})
                                forward = self.calls("ssh", "forward")[-1]["args"]
                                destination = f"[{backend}]" if ":" in backend else backend
                                self.assertIn(f"localhost:{port}:{destination}:{port}", forward)
                                status, result = self.finish(process, prepared)
                                self.assertEqual(status, 0, result)
                                self.assertEqual(self.calls("google-chrome-stable")[-1]["args"], ["--new-window", url])

                                first = http.client.HTTPConnection(backend, port, timeout=2)
                                first.request("GET", "/start", headers={"Host": f"{source}:{port}"})
                                redirect = first.getresponse()
                                location = redirect.getheader("Location")
                                self.assertEqual(redirect.status, 302)
                                redirect.read(); first.close()
                                self.assertEqual(location, f"http://localhost:{port}/done")
                                self.assertEqual(urlsplit(location).hostname, "localhost")
                                final = http.client.HTTPConnection(redirected_to, port, timeout=2)
                                final.request("GET", "/done", headers={"Host": f"localhost:{port}"})
                                reply = final.getresponse()
                                self.assertEqual(reply.status, 200)
                                self.assertEqual(reply.read(), b"COMPANY-FINAL-BODY")
                                final.close()
                                self.assertEqual([path for path, _ in requests], ["/start", "/done"])
                            finally:
                                for server in servers:
                                    server.shutdown()
                                for thread in threads:
                                    thread.join(timeout=2)

    def test_explicit_ip_reuse_requires_new_dual_family_source_backend_record(self):
        for source, backend, port in (("127.0.0.1", "127.0.0.1", 8743), ("[::1]", "::1", 8744)):
            with self.subTest(source=source):
                url = f"http://{source}:{port}/start"
                status, result = self.web(url)
                self.assertEqual(status, 0, result)
                record = [p for p in (self.runtime / "pi-client-link-helper").glob("e-*.json")
                          if json.loads(p.read_text())["endpoint"]["port"] == port][0]
                original = json.loads(record.read_text())
                self.assertEqual(original["version"], 4)
                self.assertEqual(original["endpoint"], {"host": backend, "port": port,
                                                         "backendHost": backend})
                self.assertEqual(self.web(f"http://{source}:{port}/again")[1]["tunnel"]["reused"], True)
                opened = len(self.calls("google-chrome-stable"))
                for mutation in ({"version": 2}, {"version": 3},
                                 {"endpoint": {"host": backend, "port": port}},
                                 {"endpoint": {**original["endpoint"],
                                               "backendHost": "::1" if backend == "127.0.0.1" else "127.0.0.1"}}):
                    record.write_text(json.dumps({**original, **mutation}))
                    failed, reply = self.web(url)
                    self.assertEqual((failed, reply["error"]["code"]), (1, "tunnel_unverified"))
                record.write_text(json.dumps(original))
                self.assertEqual(len(self.calls("google-chrome-stable")), opened)
                self.assertFalse(self.calls("ssh", "exit"))

    def test_explicit_ip_other_source_family_cannot_reuse_owned_pair(self):
        self.assertEqual(self.web("http://127.0.0.1:8743/first")[0], 0)
        status, result = self.web("http://[::1]:8743/other-backend")
        self.assertEqual((status, result["error"]["code"]), (1, "port_conflict"))
        self.assertEqual(len(self.calls("ssh", "-M")), 1)
        self.assertEqual(len(self.calls("ssh", "forward")), 1)
        self.assertEqual(len(self.calls("google-chrome-stable")), 1)

    def test_localhost_any_covered_family_collision_is_port_conflict(self):
        for host in ("127.0.0.1", "::1", "::ffff:7f00:1", "0.0.0.0", "::"):
            with self.subTest(host=host):
                self.state["listeners"] = [{"host": host, "port": 8795, "pid": 999}]
                self.save()
                status, result = self.web("http://localhost:8795/start", backend_host="::1")
                self.assertEqual((status, result["error"]["code"]), (1, "port_conflict"))
        self.assertFalse(self.calls("google-chrome-stable"))
        self.assertFalse(self.calls("ssh", "-M"))
        self.assertFalse(self.calls("code"))

    def test_localhost_partial_forward_wrong_pid_and_race_close_only_new_master(self):
        cases = [({"forward_omit_family": "::1"}, "tunnel_unverified"),
                 ({"forward_wrong_pid_family": "127.0.0.1"}, "port_conflict"),
                 ({"forward_partial_failure": True}, "tunnel_unverified"),
                 ({"forward_no_listener": True}, "tunnel_unverified"),
                 ({"race_port": 8744, "race_host": "::1"}, "port_conflict")]
        for mutation, code in cases:
            with self.subTest(mutation=mutation):
                self.state = {"next_pid": 4001, "masters": {}, "listeners": [], **mutation}
                self.save()
                before_exit = len(self.calls("ssh", "exit"))
                before_forward = len(self.calls("ssh", "forward"))
                status, result = self.web("https://localhost:8744/a", backend_host="127.0.0.1")
                self.assertEqual((status, result["error"]["code"]), (1, code), result)
                self.assertEqual(len(self.calls("ssh", "forward")), before_forward + 1)
                self.assertEqual(len(self.calls("ssh", "exit")), before_exit + 1)
                state = json.loads(self.state_path.read_text())
                self.assertTrue(all(not item["alive"] for item in state["masters"].values()))
                self.assertFalse(list((self.runtime / "pi-client-link-helper").glob("e-*.json")))
                self.assertFalse(self.calls("google-chrome-stable"))
                if "race_port" in mutation:
                    self.assertEqual(state["listeners"], [{"host": "::1", "port": 8744, "pid": 999}])
        self.assertFalse(self.calls("code"))

    def test_localhost_backend_or_stale_record_mismatch_never_reuses_or_closes_existing_master(self):
        url = "https://localhost:8745/first"
        status, first = self.web(url, backend_host="127.0.0.1")
        self.assertEqual(status, 0, first)
        record = next((self.runtime / "pi-client-link-helper").glob("e-*.json"))
        original = json.loads(record.read_text())
        for mutation in [None, {"version": 2}, {"endpoint": {**original["endpoint"], "backendHost": "::1"}},
                         {"endpoint": {"host": "localhost", "port": 8746, "backendHost": "127.0.0.1"}}]:
            with self.subTest(mutation=mutation):
                record.write_text(json.dumps({**original, **mutation}) if mutation else json.dumps(original))
                backend = "::1" if mutation is None else "127.0.0.1"
                status, result = self.web("https://localhost:8745/again", backend_host=backend)
                self.assertEqual((status, result["error"]["code"]), (1, "tunnel_unverified"))
        record.write_text(json.dumps(original))
        self.assertEqual(self.web("https://localhost:8745/reused", backend_host="127.0.0.1")[1]["tunnel"]["reused"], True)
        self.assertEqual(len(self.calls("ssh", "-M")), 1)
        self.assertEqual(len(self.calls("ssh", "forward")), 1)
        self.assertFalse(self.calls("ssh", "exit"))
        self.assertEqual(len(self.calls("google-chrome-stable")), 2)

    def test_localhost_precommit_cancel_and_listener_loss_never_launch(self):
        request = {"action": "open-web", "url": "https://localhost:8745/a?x=1#f", "backendHost": "::1"}
        process = self.start(request)
        prepared = json.loads(process.stdout.readline())
        self.assertEqual(prepared["phase"], "prepared", prepared)
        self.assertFalse(self.calls("google-chrome-stable"))
        process.stdin.close()
        aborted = json.loads(process.stdout.readline())
        self.assertEqual(aborted["error"]["code"], "request_aborted")
        self.assertNotEqual(process.wait(timeout=5), 0)
        process.stdout.close(); process.stderr.close()
        self.assertFalse(self.calls("google-chrome-stable"))
        process = self.start(request)
        prepared = json.loads(process.stdout.readline())
        self.assertTrue(prepared["tunnel"]["reused"])
        state = json.loads(self.state_path.read_text())
        state["listeners"] = [item for item in state["listeners"] if item["host"] != "::1"]
        self.state = state
        self.save()
        status, result = self.finish(process, prepared)
        self.assertEqual((status, result["error"]["code"]), (1, "tunnel_unverified"))
        self.assertFalse(self.calls("google-chrome-stable"))
        self.assertFalse(self.calls("ssh", "exit"))

    def test_browser_normalized_loopback_cannot_open_local_decoy(self):
        self.state["listeners"] = [{"host": "127.0.0.1", "port": 8795, "pid": 999}]
        self.save()
        for url in ("http://127.0.0.1.:8795/path", "http://127.1.:8795/path",
                    "http://0x7f000001.:8795/path", "http://１２７.０.０.１:8795/path",
                    "http://0.0.0.0:8795/path", "http://[::]:8795/path"):
            with self.subTest(url=url):
                status, result = self.web(url)
                self.assertEqual(status, 1)
                self.assertEqual(result["error"]["code"], "unsupported_target")
        self.assertFalse(self.calls("google-chrome-stable"))
        self.assertFalse(self.calls("ssh"))
        self.assertFalse(self.calls("systemctl"))

    def test_long_inventories_are_complete_or_explicit_overflow(self):
        self.state["ss_padding"] = 90
        self.state["environment_padding"] = 6000
        self.save()
        status, result = self.web("http://127.0.0.1:8743/owned")
        self.assertEqual(status, 0, result)
        self.assertEqual(result["tunnel"]["localPort"], 8743)
        self.state = json.loads(self.state_path.read_text())
        self.state["listeners"].append({"host": "127.0.0.1", "port": 8795, "pid": 999})
        self.save()
        opened = len(self.calls("google-chrome-stable"))
        status, collision = self.web("http://127.0.0.1:8795/collision")
        self.assertEqual((status, collision["error"]["code"]), (1, "port_conflict"))
        self.assertEqual(len(self.calls("google-chrome-stable")), opened)
        self.state = json.loads(self.state_path.read_text())
        self.state["environment_padding"] = 1_100_000
        self.save()
        opened = len(self.calls("google-chrome-stable"))
        status, overflow = self.web("https://example.org/")
        self.assertEqual((status, overflow["error"]["code"]), (1, "command_output_overflow"))
        self.assertEqual(len(self.calls("google-chrome-stable")), opened)

    def test_reject_malformed_targets_without_side_effect(self):
        requests = [
            b'{"version":2,"version":2,"phase":"request","prepareDeadlineMs":100,"action":"open-web","url":"http://x"}\n',
            b'{"version":1,"action":"open-web","url":"https://example.org/"}\n',
            b'{"version":2,"phase":"request","prepareDeadlineMs":45001,"action":"open-web","url":"https://example.org/"}\n',
            {"version": 1, "action": "open-web", "url": "http://user@127.0.0.1:8743/"},
            {"version": 1, "action": "open-web", "url": "http://foo.localhost:8743/"},
            {"version": 1, "action": "open-web", "url": "http://0x7f000001:8743/"},
            {"version": 1, "action": "open-web", "url": "file:///tmp/a"},
            {"version": 1, "action": "open-web", "url": "http://127.0.0.1:0/"},
            {"version": 1, "action": "open-web", "url": "http://127.0.0.1:/"},
            {"version": 1, "action": "open-file", "path": "/absolute"},
            {"version": 1, "action": "open-file", "path": "relative/file", "kind": "file"},
            {"version": 1, "action": "open-file", "path": "/absolute", "kind": "file", "line": True},
            {"version": 1, "action": "open-file", "path": "/absolute", "kind": "file", "column": 2},
        ]
        for value in requests:
            with self.subTest(value=value):
                status, result = self.invoke(value)
                self.assertEqual(status, 1)
                self.assertFalse(result["ok"])
        self.assertEqual(self.logs(), [])

    def test_timeout_kills_term_ignoring_descendant_after_leader_exits(self):
        helper = runpy.run_path(str(HELPER))
        child_pid_file = self.root / "child.pid"
        leader_pid_file = self.root / "leader.pid"
        child = ("import os,signal,sys,time;"
                 "signal.signal(signal.SIGTERM,signal.SIG_IGN);"
                 "open(sys.argv[1],'w').write(str(os.getpid()));"
                 "time.sleep(30)")
        leader = ("import os,subprocess,sys,time;"
                  "subprocess.Popen([sys.executable,'-c',sys.argv[2],sys.argv[1]],"
                  "stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL);"
                  "open(sys.argv[3],'w').write(str(os.getpid()));"
                  "\nwhile not os.path.exists(sys.argv[1]): time.sleep(.01)"
                  "\ntime.sleep(30)")
        try:
            with self.assertRaises(helper["LinkError"]) as caught:
                helper["run"]([sys.executable, "-c", leader, str(child_pid_file), child,
                               str(leader_pid_file)], timeout=.5)
            self.assertEqual(caught.exception.code, "command_timeout")
            self.assertTrue(child_pid_file.exists(), "child was not ready before timeout")
            child_pid = int(child_pid_file.read_text())
            child_stat = Path(f"/proc/{child_pid}/stat")
            if child_stat.exists():
                state = child_stat.read_text().split(") ", 1)[1].split()[0]
                self.assertIn(state, ("Z", "X"), "TERM-ignoring descendant survived timeout")
        finally:
            if leader_pid_file.exists():
                try:
                    os.killpg(int(leader_pid_file.read_text()), signal.SIGKILL)
                except ProcessLookupError:
                    pass

    def test_disconnect_while_waiting_for_owned_lock_never_launches_later(self):
        directory = self.runtime / "pi-client-link-helper"
        directory.mkdir(mode=0o700)
        with (directory / "lock").open("w") as lock:
            os.chmod(directory / "lock", 0o600)
            fcntl.flock(lock, fcntl.LOCK_EX)
            process = self.start({"action": "open-web", "url": "http://127.0.0.1:8743/late"})
            try:
                until = time.monotonic() + 5
                while not self.calls("xset") and time.monotonic() < until:
                    time.sleep(.02)
                self.assertTrue(self.calls("xset"), "helper did not reach the lock")
                # Stand in for a dead SSH channel, not just a local child kill.
                process.stdin.close()
                process.stdout.close()
                process.stderr.close()
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)
                process.wait(timeout=5)
        self.assertFalse(self.calls("google-chrome-stable"))
        self.assertFalse(self.calls("ssh", "forward"))

    def test_web_and_file_launch_exactly_once_only_after_matching_commit(self):
        for request, opener in [({"action": "open-web", "url": "http://127.0.0.1:8743/commit"}, "google-chrome-stable"),
                                ({"action": "open-file", "kind": "file", "path": "/company/README"}, "code")]:
            with self.subTest(opener=opener):
                before = len(self.calls(opener))
                process = self.start(request)
                prepared = json.loads(process.stdout.readline())
                self.assertEqual(prepared["phase"], "prepared")
                self.assertEqual(prepared["action"], request["action"])
                self.assertEqual(len(self.calls(opener)), before)
                status, result = self.finish(process, prepared)
                self.assertEqual(status, 0, result)
                self.assertEqual(result["action"], request["action"])
                self.assertEqual(len(self.calls(opener)), before + 1)

    def test_prepared_disconnect_and_bad_commit_never_launch_web_or_code(self):
        for request, opener in [({"action": "open-web", "url": "http://127.0.0.1:8743/x"}, "google-chrome-stable"),
                                ({"action": "open-file", "kind": "file", "path": "/company/file"}, "code")]:
            with self.subTest(opener=opener):
                before = len(self.calls(opener))
                process = self.start(request)
                prepared = json.loads(process.stdout.readline())
                self.assertEqual(prepared["phase"], "prepared")
                process.stdin.close()
                result = json.loads(process.stdout.readline())
                self.assertEqual(result["error"]["code"], "request_aborted")
                self.assertNotEqual(process.wait(timeout=5), 0)
                process.stdout.close(); process.stderr.close()
                self.assertEqual(len(self.calls(opener)), before)
                for commit in [
                    {"version": 2, "phase": "commit", "nonce": "wrong"},
                    {"version": 2, "nonce": prepared["nonce"]},
                    {"version": 1, "phase": "commit", "nonce": prepared["nonce"]},
                    {"version": 2, "phase": "commit", "nonce": prepared["nonce"], "extra": 1},
                ]:
                    process = self.start(request)
                    current = json.loads(process.stdout.readline())
                    process.stdin.write(json.dumps({**commit, "nonce": "wrong" if commit["nonce"] == "wrong" else current["nonce"]}).encode() + b"\n")
                    process.stdin.flush()
                    result = json.loads(process.stdout.readline())
                    self.assertEqual(result["error"]["code"], "invalid_commit")
                    self.assertNotEqual(process.wait(timeout=5), 0)
                    process.stdin.close(); process.stdout.close(); process.stderr.close()
                    self.assertEqual(len(self.calls(opener)), before)

    def test_duplicate_commit_and_deadlines_never_launch(self):
        request = {"action": "open-web", "url": "https://example.org/"}
        process = self.start(request)
        prepared = json.loads(process.stdout.readline())
        commit = json.dumps({"version": 2, "phase": "commit", "nonce": prepared["nonce"]}).encode() + b"\n"
        process.stdin.write(commit + commit); process.stdin.flush()
        result = json.loads(process.stdout.readline())
        self.assertEqual(result["error"]["code"], "invalid_commit")
        self.assertNotEqual(process.wait(timeout=5), 0)
        process.stdin.close(); process.stdout.close(); process.stderr.close()
        self.assertFalse(self.calls("google-chrome-stable"))
        # GUI preflight spawns several programs before PREPARED. Give it a
        # realistic budget, then let the helper's commit-wait deadline expire.
        process = self.start({**request, "prepareDeadlineMs": 5000})
        first = json.loads(process.stdout.readline())
        self.assertEqual(first["phase"], "prepared", first)
        result = json.loads(process.stdout.readline())
        self.assertEqual(result["error"]["code"], "request_timeout")
        self.assertNotEqual(process.wait(timeout=5), 0)
        process.stdin.close(); process.stdout.close(); process.stderr.close()
        self.assertFalse(self.calls("google-chrome-stable"))
        directory = self.runtime / "pi-client-link-helper"
        directory.mkdir(mode=0o700, exist_ok=True)
        with (directory / "lock").open("w") as lock:
            os.chmod(directory / "lock", 0o600)
            fcntl.flock(lock, fcntl.LOCK_EX)
            process = self.start({"action": "open-web", "url": "http://127.0.0.1:8743/late",
                                  "prepareDeadlineMs": 5000})
            result = json.loads(process.stdout.readline())
            self.assertEqual(result["error"]["code"], "request_timeout")
            self.assertNotEqual(process.wait(timeout=5), 0)
            process.stdin.close(); process.stdout.close(); process.stderr.close()
        self.assertFalse(self.calls("google-chrome-stable"))

    def test_installer_only_owns_a_symlink_and_refuses_foreign_target(self):
        installer = MODULE / "install.sh"
        first = subprocess.run([str(installer)], env=self.env, capture_output=True, text=True)
        self.assertEqual(first.returncode, 0, first.stderr)
        target = self.root / ".local/bin/pi-client-link-helper"
        self.assertTrue(target.is_symlink())
        self.assertEqual(target.resolve(), HELPER)
        self.assertEqual(subprocess.run([str(installer)], env=self.env, capture_output=True).returncode, 0)
        target.unlink()
        target.write_text("foreign")
        failed = subprocess.run([str(installer)], env=self.env, capture_output=True)
        self.assertNotEqual(failed.returncode, 0)
        self.assertEqual(target.read_text(), "foreign")


class CompanyDesktopCase(HelperCase):
    """Replay every helper contract on the company desktop targeting Y9000p."""
    HOSTNAME = COMPANY_HOST


if __name__ == "__main__":
    unittest.main()

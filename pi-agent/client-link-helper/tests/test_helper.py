#!/usr/bin/env python3
"""Black-box client helper tests: all SSH/desktop commands are isolated fakes."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


MODULE = Path(__file__).resolve().parents[1]
HELPER = MODULE / "pi-client-link-helper"
FAKE = r'''#!PYTHON
import json
import os
from pathlib import Path
import re
import socket
import sys

name = Path(sys.argv[0]).name
args = sys.argv[1:]
state_path = Path(os.environ["FAKE_STATE"])
log_path = Path(os.environ["FAKE_LOG"])
state = json.loads(state_path.read_text())
with log_path.open("a") as log:
    log.write(json.dumps({"name": name, "args": args, "display": os.environ.get("DISPLAY"),
                          "authority": os.environ.get("XAUTHORITY")}) + "\n")

def save():
    state_path.write_text(json.dumps(state))

if name == "systemctl":
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
elif name == "ssh":
    if "-G" in args:
        print("hostname 198.51.100.23\nuser conley\nport 22")
    elif "-M" in args:
        assert "ClearAllForwardings=yes" in args and "BatchMode=yes" in args
        assert "ExitOnForwardFailure=yes" in args and "-fN" in args
        assert "-R" not in args and args[-1] == "conley-company"
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
        assert args[-5:] == ["-p", "22", "-l", "conley", "198.51.100.23"]
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
            assert local.strip("[]") == remote.strip("[]")
            port = int(localport)
            if state.get("race_port") == port:
                state.pop("race_port")
                state["listeners"].append({"host": local.strip("[]"), "port": port, "pid": 999})
                save()
                print("mux_client_forward: Port forwarding failed", file=sys.stderr)
                sys.exit(255)
            if state.get("forward_fail"):
                print("mux_client_forward: permission denied", file=sys.stderr)
                sys.exit(255)
            if any(item["port"] == port for item in state["listeners"]):
                print("mux_client_forward: Port forwarding failed", file=sys.stderr)
                sys.exit(255)
            if state.get("forward_no_listener"):
                sys.exit(0)
            state["listeners"].append({"host": local.strip("[]"), "port": port,
                                       "pid": master["pid"], "remote_port": int(remoteport)})
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
    for item in state["listeners"]:
        pid = item.get("pid")
        process = 'users:(("ssh",pid=%s,fd=5))' % pid if pid else ""
        host = "[" + item["host"] + "]" if ":" in item["host"] else item["host"]
        print("LISTEN 0 128 %s:%d 0.0.0.0:* %s" % (host, item["port"], process))
elif name in ("xdg-open", "code"):
    if state.get(name + "_fail"):
        print(name + " GUI failure", file=sys.stderr)
        sys.exit(5)
else:
    sys.exit(11)
'''


class HelperCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.runtime = self.root / "runtime"
        self.runtime.mkdir(mode=0o700)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        fake = self.bin / "fake"
        fake.write_text(FAKE.replace("#!PYTHON", "#!" + sys.executable))
        fake.chmod(0o755)
        for name in ("ssh", "ss", "systemctl", "loginctl", "xset", "xdg-open", "code"):
            (self.bin / name).symlink_to(fake)
        self.state_path = self.root / "state.json"
        self.log_path = self.root / "log.jsonl"
        self.state = {"next_pid": 4001, "masters": {}, "listeners": []}
        self.save()
        self.env = os.environ.copy()
        self.env.update({"PATH": str(self.bin) + ":" + self.env["PATH"],
                         "FAKE_STATE": str(self.state_path), "FAKE_LOG": str(self.log_path),
                         "TEST_RUNTIME": str(self.runtime), "DISPLAY": ":99",
                         "XAUTHORITY": "/wrong/ssh/authority", "HOME": str(self.root)})

    def save(self):
        self.state_path.write_text(json.dumps(self.state))

    def logs(self):
        return [json.loads(line) for line in self.log_path.read_text().splitlines()] if self.log_path.exists() else []

    def calls(self, name, operation=None):
        found = [item for item in self.logs() if item["name"] == name]
        if operation:
            found = [item for item in found if operation in item["args"]]
        return found

    def invoke(self, request):
        raw = request if isinstance(request, bytes) else json.dumps(request).encode()
        process = subprocess.run([str(HELPER)], input=raw, env=self.env,
                                 capture_output=True, timeout=20)
        self.assertEqual(process.stderr, b"")
        return process.returncode, json.loads(process.stdout)

    def web(self, url):
        return self.invoke({"version": 1, "action": "open-web", "url": url})

    def test_collision_uses_owned_alternative_and_reuses_exact_mapping(self):
        self.state["listeners"] = [{"host": "127.0.0.1", "port": 8743, "pid": 999}]
        self.save()
        original = "http://127.0.0.1:8743/a%2Fb?q=a%2Bb&x=1#frag%20raw"
        status, first = self.web(original)
        self.assertEqual(status, 0, (first, self.logs()))
        alternative = first["tunnel"]["localPort"]
        self.assertNotEqual(alternative, 8743)
        self.assertEqual(first["openedUrl"], original.replace(":8743", f":{alternative}"))
        self.assertEqual(self.calls("xdg-open")[-1]["args"], [first["openedUrl"]])
        self.assertEqual(len(self.calls("ssh", "-M")), 1)
        self.assertEqual(len(self.calls("ssh", "forward")), 1)
        self.assertEqual(self.calls("xset")[-1]["display"], ":1")
        self.assertEqual(self.calls("xset")[-1]["authority"], str(self.runtime / "Xauthority"))
        status, again = self.web("http://127.0.0.1:8743/other?still=1#yes")
        self.assertEqual(status, 0)
        self.assertTrue(again["tunnel"]["reused"])
        self.assertEqual(again["tunnel"]["localPort"], alternative)
        self.assertEqual(len(self.calls("ssh", "-M")), 1)
        self.assertEqual(len(self.calls("ssh", "forward")), 1)
        record = next((self.runtime / "pi-client-link-helper").glob("e-*.json"))
        self.assertEqual(record.stat().st_mode & 0o777, 0o600)
        self.assertEqual(json.loads(record.read_text())["endpoint"], {"host": "127.0.0.1", "port": 8743})

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

    def test_bind_race_retries_only_after_observed_collision(self):
        self.state["race_port"] = 8743
        self.save()
        status, result = self.web("http://127.0.0.1:8743/race")
        self.assertEqual(status, 0, result)
        self.assertNotEqual(result["tunnel"]["localPort"], 8743)
        self.assertEqual(len(self.calls("ssh", "forward")), 2)
        self.assertEqual(self.calls("xdg-open")[-1]["args"], [result["openedUrl"]])

    def test_simultaneous_requests_are_serialized(self):
        payload = json.dumps({"version": 1, "action": "open-web", "url": "http://127.0.0.1:8743/same"}).encode()
        processes = [subprocess.Popen([str(HELPER)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                      stderr=subprocess.PIPE, env=self.env) for _ in range(2)]
        results = [process.communicate(payload, timeout=20) for process in processes]
        self.assertTrue(all(process.returncode == 0 for process in processes), results)
        parsed = [json.loads(output) for output, _ in results]
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
        self.assertEqual(result["error"]["code"], "tunnel_unverified")
        self.assertEqual(len(self.calls("xdg-open")), 1)
        self.assertEqual(len(self.calls("ssh", "-M")), 1)

    def test_ssh_failure_and_forward_failure_do_not_launch_browser(self):
        self.state["master_fail"] = True
        self.save()
        status, result = self.web("http://127.0.0.1:8743/")
        self.assertEqual((status, result["error"]["code"]), (1, "ssh_master_failed"))
        self.assertFalse(self.calls("xdg-open"))
        self.state.pop("master_fail")
        self.state["forward_fail"] = True
        self.save()
        status, result = self.web("http://127.0.0.1:8743/")
        self.assertEqual((status, result["error"]["code"]), (1, "ssh_forward_failed"))
        self.assertFalse(self.calls("xdg-open"))
        self.assertEqual(len(self.calls("ssh", "exit")), 1)

    def test_successful_mux_reply_without_listener_does_not_open_browser(self):
        self.state["forward_no_listener"] = True
        self.save()
        status, result = self.web("http://127.0.0.1:8743/")
        self.assertEqual((status, result["error"]["code"]), (1, "tunnel_unverified"))
        self.assertFalse(self.calls("xdg-open"))
        self.assertEqual(len(self.calls("ssh", "exit")), 1)

    def test_no_launch_without_gui_and_explicit_desktop_errors(self):
        self.state["gui_missing"] = True
        self.save()
        status, result = self.web("http://127.0.0.1:8743/")
        self.assertEqual((status, result["error"]["code"]), (1, "gui_unavailable"))
        self.assertFalse(self.calls("ssh"))
        self.state.pop("gui_missing")
        self.state["xdg-open_fail"] = True
        self.save()
        status, result = self.web("https://example.org/")
        self.assertEqual((status, result["error"]["code"]), (1, "browser_failed"))
        self.state.pop("xdg-open_fail")
        self.state["code_fail"] = True
        self.save()
        status, result = self.invoke({"version": 1, "action": "open-file", "path": "/srv/source.py"})
        self.assertEqual((status, result["error"]["code"]), (1, "code_failed"))

    def test_multiple_active_graphical_sessions_fail_before_launch(self):
        self.state["ambiguous_gui"] = True
        self.save()
        status, result = self.web("http://127.0.0.1:8743/")
        self.assertEqual((status, result["error"]["code"]), (1, "gui_unavailable"))
        self.assertFalse(self.calls("ssh"))
        self.assertFalse(self.calls("xdg-open"))

    def test_file_argv_is_remote_and_path_is_never_read_locally(self):
        path = "/company/not-on-laptop/-name with spaces 'quotes':suffix.py"
        status, result = self.invoke({"version": 1, "action": "open-file", "path": path,
                                      "line": 31, "column": 7})
        self.assertEqual(status, 0)
        self.assertEqual(result["path"], path)
        self.assertEqual(self.calls("code")[-1]["args"],
                         ["--remote", "ssh-remote+conley-company", "--new-window", "--goto", "--", path + ":31:7"])
        self.assertFalse(self.calls("ssh"))
        self.assertFalse(self.calls("xdg-open"))
        status, _ = self.invoke({"version": 1, "action": "open-file", "path": "/company/project"})
        self.assertEqual(status, 0)
        self.assertEqual(self.calls("code")[-1]["args"],
                         ["--remote", "ssh-remote+conley-company", "--new-window", "--", "/company/project"])

    def test_default_http_port_gets_an_owned_forward(self):
        status, result = self.web("http://127.0.0.1/no-explicit-port?q=1#f")
        self.assertEqual(status, 0, result)
        self.assertEqual(result["tunnel"]["remotePort"], 80)
        self.assertEqual(result["openedUrl"],
                         f'http://127.0.0.1:{result["tunnel"]["localPort"]}/no-explicit-port?q=1#f')
        self.assertEqual(self.calls("xdg-open")[-1]["args"], [result["openedUrl"]])

    def test_ipv6_loopback_binds_and_opens_exact_family(self):
        status, result = self.web("http://[::1]:8743/hello?x=1#f")
        self.assertEqual(status, 0, result)
        self.assertEqual(result["openedUrl"], "http://[::1]:8743/hello?x=1#f")
        self.assertEqual(result["tunnel"]["remoteHost"], "::1")
        self.assertIn("[::1]:8743:[::1]:8743", self.calls("ssh", "forward")[-1]["args"])

    def test_loopback_host_normalization_and_exact_suffix(self):
        status, mapped = self.web("http://[::ffff:127.0.0.1]:8743/mapped")
        self.assertEqual(status, 0, mapped)
        self.assertEqual(mapped["openedUrl"], "http://127.0.0.1:8743/mapped")
        status, result = self.web("https://localhost:8743/a?#")
        self.assertEqual(status, 0)
        self.assertEqual(result["openedUrl"], "https://127.0.0.1:8743/a?#")
        self.assertEqual(result["tunnel"]["remoteHost"], "127.0.0.1")
        status, result = self.web("http://example.org/a?q=2#x")
        self.assertEqual(status, 0)
        self.assertIsNone(result["tunnel"])
        self.assertEqual(result["openedUrl"], "http://example.org/a?q=2#x")
        self.assertEqual(len(self.calls("ssh", "-M")), 1)

    def test_reject_malformed_targets_without_side_effect(self):
        requests = [
            b'{"version":1,"version":1,"action":"open-web","url":"http://x"}',
            {"version": 1, "action": "open-web", "url": "http://user@127.0.0.1:8743/"},
            {"version": 1, "action": "open-web", "url": "http://foo.localhost:8743/"},
            {"version": 1, "action": "open-web", "url": "http://0x7f000001:8743/"},
            {"version": 1, "action": "open-web", "url": "file:///tmp/a"},
            {"version": 1, "action": "open-web", "url": "http://127.0.0.1:0/"},
            {"version": 1, "action": "open-web", "url": "http://127.0.0.1:/"},
            {"version": 1, "action": "open-file", "path": "relative/file"},
            {"version": 1, "action": "open-file", "path": "/absolute", "line": True},
            {"version": 1, "action": "open-file", "path": "/absolute", "column": 2},
        ]
        for value in requests:
            with self.subTest(value=value):
                status, result = self.invoke(value)
                self.assertEqual(status, 1)
                self.assertFalse(result["ok"])
        self.assertEqual(self.logs(), [])

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


if __name__ == "__main__":
    unittest.main()

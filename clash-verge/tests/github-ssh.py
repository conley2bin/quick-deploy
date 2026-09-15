#!/usr/bin/env python3
"""Offline GitHub SSH/routing regression checks; never uses the real HOME."""
from pathlib import Path
import json
import os
import re
import shutil
import subprocess
import tempfile

SOURCE = Path(__file__).resolve().parents[1] / "tun-fix.sh"


def check(condition, message):
    if not condition:
        raise AssertionError(message)


def main():
    for program in ("bash", "ssh", "node"):
        check(shutil.which(program), f"missing prerequisite: {program}")
    real_ssh = shutil.which("ssh")
    source = SOURCE.read_text()
    check(source.endswith("\nmain\n"), "review entrypoint extraction after source changes")

    # tempfile owns this exact directory. Do not use shell HOME as a cleanup target.
    with tempfile.TemporaryDirectory(prefix="clash-github-check-") as td:
        root = Path(td)
        home = root / "home"
        clash = home / ".local/share/io.github.clash-verge-rev.clash-verge-rev"
        profiles = clash / "profiles"
        profiles.mkdir(parents=True)
        (clash / "profiles.yaml").write_text(
            "items:\n- uid: Merge\n  type: merge\n  file: Merge.yaml\n"
            "- uid: Script\n  type: script\n  file: Script.js\n"
        )
        functions = root / "functions.sh"
        functions.write_text(source[:-len("main\n")])
        bindir = root / "bin"
        bindir.mkdir()
        ssh_config = root / "ssh.config"
        wrapper = bindir / "ssh"
        wrapper.write_text(
            "#!/bin/sh\n"
            '[ "$1" = "-G" ] || { echo "network SSH forbidden in this test" >&2; exit 2; }\n'
            f'exec {real_ssh} -F "$SSH_TEST_CONFIG" "$@"\n'
        )
        wrapper.chmod(0o700)
        env = dict(os.environ, HOME=str(home), SSH_TEST_CONFIG=str(ssh_config))
        env["PATH"] = str(bindir) + os.pathsep + os.environ["PATH"]

        def bash(body, expected=0):
            result = subprocess.run(
                ["bash", "-c", 'source "$1"; ' + body, "test", str(functions)],
                cwd=root, env=env, text=True, capture_output=True, check=False,
            )
            check(result.returncode == expected,
                  f"unexpected rc {result.returncode}, expected {expected}:\n"
                  f"{result.stdout}\n{result.stderr}")
            return result.stdout

        generated = bash("github_ssh_block")
        check("ProxyCommand" not in generated and "ProxyJump" not in generated,
              "generator must not introduce a jump host")
        ssh_config.write_text(generated)
        bash("verify_github_ssh_config")
        parsed = subprocess.run(
            [real_ssh, "-G", "-F", str(ssh_config), "github.com"],
            text=True, capture_output=True, check=True,
        ).stdout
        for expected in ("hostname ssh.github.com\n", "port 443\n", "ipqos none none\n"):
            check(expected in parsed, f"missing effective setting: {expected.strip()}")
        for wrong_setting in ("IPQoS throughput", "ProxyCommand false", "ProxyJump jump.invalid"):
            ssh_config.write_text("Host github.com\n    " + wrong_setting + "\n" + generated)
            bash("verify_github_ssh_config", expected=1)
        ssh_config.write_text(generated)

        bash("update_direct_rules")
        js_file = profiles / "Script.js"
        js = js_file.read_text()
        bash("update_direct_rules")
        check(js_file.read_text() == js, "rule generator is not repeatable")
        node_test = r'''
const fs = require('node:fs');
const vm = require('node:vm');
const sandbox = {};
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(process.argv[1], 'utf8'), sandbox);
const rule = 'DOMAIN,ssh.github.com,DIRECT';
const original = [
  'DOMAIN-SUFFIX,github.com,Proxy', rule,
  'DOMAIN-SUFFIX,services.googleapis.cn,Proxy',
  'DOMAIN-SUFFIX,cn,DIRECT', 'GEOIP,CN,DIRECT,no-resolve', 'MATCH,Proxy'
];
const once = sandbox.main({rules: original.slice()}).rules;
if (once[0] !== rule || once.filter(x => x === rule).length !== 1)
  throw Error('GitHub SSH exception must be first and unique');
const retained = once.filter(x => original.includes(x) && x !== rule);
if (JSON.stringify(retained) !== JSON.stringify(original.filter(x => x !== rule)))
  throw Error('existing unrelated rules changed order or disappeared');
const twice = sandbox.main({rules: once.slice()}).rules;
if (JSON.stringify(once) !== JSON.stringify(twice)) throw Error('merge is not idempotent');
console.log(JSON.stringify({first: once[0], idempotent: true}));
'''
        result = subprocess.run(
            ["node", "-e", node_test, str(js_file)],
            text=True, capture_output=True, check=True, env=env,
        )
        check(json.loads(result.stdout)["idempotent"], "rule-merge check did not complete")

        dns = bash("fake_ip_filter_block")
        filters = re.findall(r"^\s*- '([^']+)'", dns, re.M)
        check("*.github.com" not in filters and "ssh.github.com" not in filters,
              "filter must preserve GitHub SSH domain mapping")
        check("github.com" in filters, "unrelated exact web-domain filter changed")
        merge = profiles / "Merge.yaml"
        merge.write_text("dns:\n" + dns + "\n" + bash("tun_block") + "\n" + bash("sniffer_block"))
        bash('verify_merge_yaml "$CLASH_DIR/profiles/Merge.yaml"')
        original_merge = merge.read_text()
        for bad_filter in ("*.github.com", "ssh.github.com"):
            merge.write_text(original_merge.replace("  fake-ip-filter:\n",
                "  fake-ip-filter:\n    - '" + bad_filter + "'\n", 1))
            bash('verify_merge_yaml "$CLASH_DIR/profiles/Merge.yaml"', expected=1)

    print("PASS: GitHub SSH QoS/no-jump policy, verifier rejection, rule ordering/idempotence, DNS mapping guard")


if __name__ == "__main__":
    main()

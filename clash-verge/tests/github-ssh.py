#!/usr/bin/env python3
"""Offline route-source regressions; all writes use a dedicated temporary HOME."""
from pathlib import Path
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]
CLASH = ROOT / "clash-verge"
SOURCE = CLASH / "tun-fix.sh"
CORE = shutil.which("verge-mihomo")
BASELINE = "666d6f80755b991166354bdcedef57409c962265"


def check(condition, message):
    if not condition:
        raise AssertionError(message)


def run(command, *, env, cwd, expected=0, input=None):
    result = subprocess.run(command, cwd=cwd, env=env, text=True, input=input,
                            capture_output=True, check=False)
    check(result.returncode == expected,
          f"unexpected rc {result.returncode}, expected {expected}:\n"
          f"$ {' '.join(command)}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}")
    return result


def write_rules(directory, direct, proxy):
    directory.mkdir()
    (directory / "direct.yaml").write_text(direct)
    (directory / "proxy.yaml").write_text(proxy)


def optimizer_shell(source, merge_file, extra="", forbid_mutators=True):
    if forbid_mutators:
        stubs = r'''
forbidden_mutator(){ echo "$1" >> "$OPTIMIZER_MARKER"; return 97; }
clear_subscription_merge(){ forbidden_mutator clear_subscription_merge; }
remove_prepend_rules(){ forbidden_mutator remove_prepend_rules; }
update_fake_ip_filter(){ forbidden_mutator update_fake_ip_filter; }
update_tun_config(){ forbidden_mutator update_tun_config; }
update_sniffer_config(){ forbidden_mutator update_sniffer_config; }
'''
    else:
        stubs = r'''
clear_subscription_merge(){ :; }
remove_prepend_rules(){ :; }
update_fake_ip_filter(){ :; }
update_tun_config(){ :; }
update_sniffer_config(){ :; }
'''
    stubs += r'''
verify_merge_yaml(){ :; }
verify_route_rules(){ :; }
verify_sniffer_live(){ :; }
verify_tun_routes(){ :; }
'''
    return ["bash", "-c", 'source "$1"; ' + stubs + extra + '\noptimize_all "$2"',
            "test", str(source), str(merge_file)]


def registry_with_merge(registry):
    if "uid: Merge" in registry:
        return registry
    if "items: []" in registry:
        registry = registry.replace("items: []", "items:")
    return registry + "- uid: Merge\n  type: merge\n  file: Merge.yaml\n"


def assert_optimizer_preflight_failure(root, rules, label, registry, *, profile_dir=True, extra="", input=None,
                                       script_text="// foreign optimizer sentinel\n", expect_prepare_failure=False):
    home = root / f"optimizer-{label}-home"
    state = home / ".local/share/io.github.clash-verge-rev.clash-verge-rev"
    state.mkdir(parents=True)
    profiles = state / "profiles"
    if profile_dir:
        profiles.mkdir()
        script = profiles / "Script.js"
        script.write_text(script_text)
        subscription = profiles / "subscription-merge.yaml"
        subscription.write_text("subscription sentinel\n")
    else:
        script = profiles / "Script.js"
        subscription = state / "subscription-merge.yaml"
        subscription.write_text("subscription sentinel\n")
    (state / "profiles.yaml").write_text(registry_with_merge(registry))
    merge = profiles / "Merge.yaml"
    if profile_dir:
        merge.write_text("merge sentinel\n")
    before_inventory = {
        str(path.relative_to(state)): path.read_bytes()
        for path in state.rglob("*.backup.*") if path.is_file()
    }
    marker = state / "forbidden-mutator.marker"
    prepare_marker = state / "prepare.marker"
    probe_marker = state / "probe.marker"
    env = dict(os.environ, HOME=str(home), RULES_DIR=str(rules), OPTIMIZER_MARKER=str(marker),
               PREPARE_MARKER=str(prepare_marker), PROBE_MARKER=str(probe_marker))
    result = run(optimizer_shell(SOURCE, merge, extra), env=env, cwd=root, expected=1, input=input)
    check(not marker.exists(), f"optimizer {label} called a mutator before preparation failed")
    check(subscription.read_text() == "subscription sentinel\n",
          f"optimizer {label} mutated subscription before Script preparation")
    if profile_dir:
        check(merge.read_text() == "merge sentinel\n", f"optimizer {label} mutated Merge before preparation")
        check(script.read_text() == script_text,
              f"optimizer {label} mutated Script before preparation completed")
        check(not list(profiles.glob(".Script.js.candidate.*")), f"optimizer {label} left candidate")
        check(not list(profiles.glob(".Script.js.rules.*")), f"optimizer {label} left rule snapshot")
        after_inventory = {
            str(path.relative_to(state)): path.read_bytes()
            for path in state.rglob("*.backup.*") if path.is_file()
        }
        check(after_inventory == before_inventory, f"optimizer {label} changed backup inventory")
    else:
        check(not merge.exists() and not profiles.exists(),
              "missing target directory was created during failed optimizer preflight")
    if expect_prepare_failure:
        check(prepare_marker.read_text() == "actual --prepare invoked\n",
              "renderer-failure fixture did not reach the real --prepare invocation")
        check("forced --prepare failure" in result.stderr,
              "renderer-failure fixture did not surface the injected --prepare error")
        check(not probe_marker.exists(), "renderer-failure fixture called a post-write diagnostic probe")
    return result


def host_wrapped_js(script, config):
    """Model v2.5.2's JSON return path: throws would return original config."""
    program = r'''
const fs = require('fs'), vm = require('vm');
const sandbox = {}; vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(process.argv[1], 'utf8'), sandbox);
const original = JSON.parse(process.argv[2]);
let returned;
try {
  const serialized = JSON.stringify(sandbox.main(original) || '');
  const parsed = JSON.parse(serialized);
  if (!parsed || Array.isArray(parsed) || typeof parsed !== 'object') throw new Error('mapping required');
  returned = parsed;
} catch (_) {
  returned = original;
}
console.log(JSON.stringify(returned));
'''
    result = subprocess.run(["node", "-e", program, str(script), json.dumps(config)],
                            text=True, capture_output=True, check=True)
    return json.loads(result.stdout)


def core_validate(root, config, name, expected):
    config_file = root / f"{name}.yaml"
    subprocess.run(["python3", "-c", "import json,sys,yaml; yaml.safe_dump(json.load(sys.stdin), sys.stdout)",
                    ], input=json.dumps(config), text=True, stdout=config_file.open("w"), check=True)
    result = subprocess.run([CORE, "-t", "-f", str(config_file)], text=True,
                            capture_output=True, check=False)
    check(result.returncode == expected,
          f"core {name} rc={result.returncode}, expected {expected}:\n{result.stdout}\n{result.stderr}")
    return result.stdout + result.stderr


def main():
    for program in ("bash", "ssh", "node", "python3", "verge-mihomo"):
        check(shutil.which(program), f"missing prerequisite: {program}")
    run(["python3", "-c", "import yaml"], env=os.environ, cwd=ROOT)

    with tempfile.TemporaryDirectory(prefix="clash-rules-check-") as td:
        root = Path(td)
        home = root / "home"
        state = home / ".local/share/io.github.clash-verge-rev.clash-verge-rev"
        profiles = state / "profiles"
        profiles.mkdir(parents=True)
        rules = root / "rules"
        direct = '''version: 1
pre:
  - "DOMAIN,force.example,DIRECT"
  - "DOMAIN,ssh.github.com,DIRECT"
post:
  - "DOMAIN-SUFFIX,cn,DIRECT"
  - "IP-CIDR,2001:db8::/32,DIRECT,no-resolve"
  - "DOMAIN,deleted.example,DIRECT"
'''
        proxy = '''version: 1
pre:
  - "DOMAIN,proxy.example,Proxy"
post:
  - "DOMAIN,late.example,Proxy"
'''
        write_rules(rules, direct, proxy)
        # Quoted and reordered valid registry fields prove real-YAML lookup.
        (state / "profiles.yaml").write_text(
            "items:\n- file: 'Script.js'\n  type: script\n  uid: Script\n"
        )
        merge = profiles / "Merge.yaml"
        runtime = state / "clash-verge.yaml"
        ssh = home / ".ssh" / "config"
        merge.write_text("untouched merge\n")
        runtime.write_text("untouched runtime\n")
        ssh.parent.mkdir()
        ssh.write_text("Host untouched\n")
        env = dict(os.environ, HOME=str(home), RULES_DIR=str(rules))
        baseline_source = root / "baseline-tun-fix.sh"
        baseline_blob = run(["git", "show", f"{BASELINE}:clash-verge/tun-fix.sh"],
                            env=os.environ, cwd=ROOT).stdout
        baseline_source.write_text(baseline_blob)
        baseline_source.chmod(0o755)

        # Policy sources are data-only and must remain byte-identical through the refactor.
        check(hashlib.sha256((CLASH / "rules/direct.yaml").read_bytes()).hexdigest() ==
              "e1cb6b4d086b0d94cba3126c2d5a2953dcad150c87cf1e4148c331d9db39c3ba",
              "direct.yaml changed during implementation refactor")
        check(hashlib.sha256((CLASH / "rules/proxy.yaml").read_bytes()).hexdigest() ==
              "8d318ea2f077eb5fad02a8560761343e71dd38b3acbf4a096f9cb28f7fd729c4",
              "proxy.yaml changed during implementation refactor")

        # Representative Merge mutation is an exact before/after fixture. It
        # preserves unrelated keys/comments, replaces bounded blocks, and is idempotent.
        merge_fixture = root / "merge-fixture.yaml"
        merge_fixture.write_text(
            "# fixture comment\nmode: rule # preserve\ndns:\n"
            "  enable: true # preserve\n  fake-ip-filter:\n    - old\n    - rule-set:old\n"
            "  nameserver:\n    - 1.1.1.1 # preserve\n"
            "prepend-rules:\n  - DOMAIN,old.example,DIRECT\n"
            "sniffer:\n  enable: false\ntun:\n  enable: false\n"
            "experimental:\n  keep: yes # preserve\n"
        )
        mutate = ['source "$1"; remove_prepend_rules "$2"; update_fake_ip_filter "$2"; '
                  'update_tun_config "$2"; update_sniffer_config "$2"; verify_merge_yaml "$2"']
        run(["bash", "-c", mutate[0], "test", str(SOURCE), str(merge_fixture)], env=env, cwd=root)
        fake_block = run(["bash", "-c", 'source "$1"; fake_ip_filter_block', "test", str(SOURCE)],
                         env=env, cwd=root).stdout
        sniffer_block = run(["bash", "-c", 'source "$1"; sniffer_block', "test", str(SOURCE)],
                            env=env, cwd=root).stdout
        tun_block = run(["bash", "-c", 'source "$1"; tun_block', "test", str(SOURCE)],
                        env=env, cwd=root).stdout
        expected_merge = (
            "# fixture comment\nmode: rule # preserve\ndns:\n" + fake_block +
            "  enable: true # preserve\n  nameserver:\n    - 1.1.1.1 # preserve\n"
            "experimental:\n  keep: yes # preserve\n\n" + sniffer_block + "\n" + tun_block
        )
        check(merge_fixture.read_text() == expected_merge,
              "Merge before/after fixture changed unrelated content or block ordering")
        first_merge = merge_fixture.read_bytes()
        run(["bash", "-c", mutate[0], "test", str(SOURCE), str(merge_fixture)], env=env, cwd=root)
        check(merge_fixture.read_bytes() == first_merge, "Merge helpers are not byte-idempotent")

        # A failing nested producer must stop before its trailing echo and before replacement.
        false_marker = root / "false-helper.marker"
        no_write_before = merge_fixture.read_bytes()
        no_write = ('source "$1"; tun_block(){ false; echo leaked > "$FALSE_MARKER"; }; '
                    'update_tun_config "$2"')
        no_write_env = dict(env, FALSE_MARKER=str(false_marker))
        run(["bash", "-c", no_write, "test", str(SOURCE), str(merge_fixture)],
            env=no_write_env, cwd=root, expected=1)
        check(not false_marker.exists() and merge_fixture.read_bytes() == no_write_before,
              "false-then-echo helper was swallowed or mutated Merge")
        check(not list(root.glob("merge-fixture.yaml.tmp.*")) and
              not list(root.glob("merge-fixture.yaml.block.*")) and
              not list(root.glob("merge-fixture.yaml.strip.*")),
              "failed Merge helper leaked same-directory temporary files")

        # The shared zero-indent key predicate must retain baseline ^key:
        # semantics for comments, inline values, and trailing whitespace.
        managed_variants = {
            "commented": (
                "# commented fixture\ndns:\n  enable: true\n"
                "prepend-rules: # legacy rules\n  - DOMAIN,old.example,DIRECT\n"
                "sniffer: # prior setting\n  enable: false\n"
                "tun: # prior setting\n  enable: false\n"
                "tun-extra:\n  keep: true # unrelated prefix\n"
                "experimental:\n  keep: commented # unrelated comment\n"
            ),
            "inline": (
                "# inline fixture\ndns:\n  enable: true\n"
                "prepend-rules: []\n"
                "sniffer: {enable: false}\n"
                "tun:    \n  enable: false\n"
                "tun-extra: {keep: true} # unrelated prefix\n"
                "experimental: {keep: inline} # unrelated data\n"
            ),
        }
        for label, initial in managed_variants.items():
            old_merge = root / f"managed-{label}.baseline.yaml"
            new_merge = root / f"managed-{label}.current.yaml"
            old_merge.write_text(initial)
            new_merge.write_text(initial)
            run(["bash", "-c", mutate[0], "test", str(baseline_source), str(old_merge)],
                env=env, cwd=root)
            run(["bash", "-c", mutate[0], "test", str(SOURCE), str(new_merge)],
                env=env, cwd=root)
            check(new_merge.read_bytes() == old_merge.read_bytes(),
                  f"managed-key {label} output differs from baseline")
            managed = new_merge.read_text()
            check(not re.search(r"(?m)^prepend-rules:", managed) and
                  len(re.findall(r"(?m)^sniffer:", managed)) == 1 and
                  len(re.findall(r"(?m)^tun:", managed)) == 1 and
                  managed.count(sniffer_block) == 1 and managed.count(tun_block) == 1,
                  f"managed-key {label} retained or duplicated a managed block")
            check("tun-extra:" in managed and "unrelated" in managed,
                  f"managed-key {label} removed unrelated prefix/data")
            first_managed = new_merge.read_bytes()
            run(["bash", "-c", mutate[0], "test", str(SOURCE), str(new_merge)],
                env=env, cwd=root)
            check(new_merge.read_bytes() == first_managed,
                  f"managed-key {label} is not byte-idempotent")

        # Exercise the real full optimizer on a commented-key Merge. Only live
        # probes are mocked; structural verification and all mutators stay real.
        commented_home = root / "optimizer-commented-home"
        commented_state = commented_home / ".local/share/io.github.clash-verge-rev.clash-verge-rev"
        commented_profiles = commented_state / "profiles"
        commented_profiles.mkdir(parents=True)
        (commented_state / "profiles.yaml").write_text(
            "items:\n- uid: Merge\n  type: merge\n  file: Merge.yaml\n"
            "- uid: Script\n  type: script\n  file: Script.js\n"
        )
        commented_merge = commented_profiles / "Merge.yaml"
        commented_merge.write_text(managed_variants["commented"])
        commented_script = commented_profiles / "Script.js"
        commented_script.write_text("// Generated by tun-fix.sh sentinel\n")
        commented_env = dict(os.environ, HOME=str(commented_home), RULES_DIR=str(rules))
        full_optimizer = (
            'source "$1"; verify_route_rules(){ :; }; verify_sniffer_live(){ :; }; '
            'verify_tun_routes(){ :; }; optimize_all "$2"'
        )
        run(["bash", "-c", full_optimizer, "test", str(SOURCE), str(commented_merge)],
            env=commented_env, cwd=root)
        optimized = commented_merge.read_text()
        check(not re.search(r"(?m)^prepend-rules:", optimized) and
              len(re.findall(r"(?m)^sniffer:", optimized)) == 1 and
              len(re.findall(r"(?m)^tun:", optimized)) == 1 and
              "tun-extra:" in optimized and "unrelated comment" in optimized and
              commented_script.read_text().startswith("// Generated by tun-fix.sh") and
              not list(commented_profiles.glob(".Script.js.candidate.*")),
              "full optimizer duplicated/retained managed keys or partially failed")

        # Generic /rules diagnostics derive direct+proxy pre/post expectations
        # from edited sources. Proxy pre may precede direct post; post may already
        # exist earlier than source insertion order, so no old proxy barrier applies.
        diagnostic_rules = root / "diagnostic-rules"
        write_rules(diagnostic_rules, direct, proxy)
        rules_json = root / "rules.json"
        runtime_rules = [
            {"type": "Domain", "payload": "force.example", "proxy": "DIRECT"},
            {"type": "Domain", "payload": "ssh.github.com", "proxy": "DIRECT"},
            {"type": "Domain", "payload": "proxy.example", "proxy": "Proxy"},
            {"type": "Domain", "payload": "late.example", "proxy": "Proxy"},
            {"type": "DomainSuffix", "payload": "cn", "proxy": "DIRECT"},
            {"type": "IPCIDR", "payload": "2001:db8::/32", "proxy": "DIRECT"},
            {"type": "Domain", "payload": "deleted.example", "proxy": "DIRECT"},
            {"type": "Match", "payload": "", "proxy": "Proxy"},
        ]
        rules_json.write_text(json.dumps({"rules": runtime_rules}))
        diagnostic_env = dict(env, RULES_DIR=str(diagnostic_rules), RULES_JSON=str(rules_json))
        diagnostic_command = ('source "$1"; mihomo_api(){ cat "$RULES_JSON"; }; verify_route_rules')
        diagnosed = run(["bash", "-c", diagnostic_command, "test", str(SOURCE)],
                        env=diagnostic_env, cwd=root)
        check("pre[2] domain,proxy.example -> Proxy" in diagnosed.stdout and
              "post domain,late.example -> Proxy; first index 3" in diagnosed.stdout and
              "not proven by this endpoint" in diagnosed.stdout,
              "generic diagnostic did not report source-derived proxy/pre/post semantics or /rules limit")
        (diagnostic_rules / "direct.yaml").write_text(
            direct.replace('  - "DOMAIN,force.example,DIRECT"\n', ""))
        runtime_rules.pop(0)
        rules_json.write_text(json.dumps({"rules": runtime_rules}))
        edited = run(["bash", "-c", diagnostic_command, "test", str(SOURCE)],
                     env=diagnostic_env, cwd=root)
        check("force.example" not in edited.stdout and "pre[1] domain,proxy.example -> Proxy" in edited.stdout,
              "generic diagnostic retained deleted hardcoded expectations")
        runtime_rules[1]["proxy"] = "WrongGroup"
        rules_json.write_text(json.dumps({"rules": runtime_rules}))
        wrong_target = run(["bash", "-c", diagnostic_command, "test", str(SOURCE)],
                           env=diagnostic_env, cwd=root, expected=1)
        check("runtime has domain,proxy.example -> WrongGroup" in wrong_target.stdout,
              "generic diagnostic missed a local proxy target mismatch")
        missing_api = run(["bash", "-c", 'source "$1"; mihomo_api(){ return 1; }; verify_route_rules',
                           "test", str(SOURCE)], env=diagnostic_env, cwd=root, expected=1)
        check("查不到活跃数据不是通过" in missing_api.stdout, "missing controller data passed silently")
        probe_marker = root / "unexpected-specialized-probe"
        probe_env = dict(diagnostic_env, PROBE_MARKER=str(probe_marker))
        skipped_probe = run(["bash", "-c",
                             'source "$1"; mihomo_api(){ echo called > "$PROBE_MARKER"; return 1; }; '
                             'verify_sniffer_live', "test", str(SOURCE)],
                            env=probe_env, cwd=root)
        check("该专用探针不适用" in skipped_probe.stdout and not probe_marker.exists(),
              "LiteLLM specialized probe ran without its assumed source policy")

        # Every Bash registry helper is backed by the same strict YAML reader.
        query_home = root / "query-home"
        query_state = query_home / ".local/share/io.github.clash-verge-rev.clash-verge-rev"
        query_state.mkdir(parents=True)
        (query_state / "profiles.yaml").write_text(
            "current: 'remote-a'\nitems:\n"
            "- file: 'Merge.yaml'\n  uid: Merge\n  type: merge\n"
            "- type: script\n  file: Script.js\n  uid: Script\n"
            "- uid: local-merge\n  file: LocalMerge.yaml\n  type: merge\n"
            "- uid: local-rules\n  type: rules\n  file: LocalRules.yaml\n"
            "- option:\n    rules: local-rules\n    merge: local-merge\n"
            "  name: 'Quoted Profile'\n  file: remote.yaml\n  type: remote\n  uid: remote-a\n"
        )
        query_env = dict(os.environ, HOME=str(query_home), RULES_DIR=str(rules))
        queried = run(["bash", "-c",
                       'source "$1"; get_merge_config; get_script_config; get_profile_name; '
                       'get_current_profile_path; get_current_profile_option_path merge; '
                       'get_current_profile_option_path rules; registry_query remote-merge-targets',
                       "test", str(SOURCE)], env=query_env, cwd=root).stdout.splitlines()
        check(queried == [str(query_state / "profiles/Merge.yaml"),
                          str(query_state / "profiles/Script.js"), "Quoted Profile",
                          str(query_state / "profiles/remote.yaml"),
                          str(query_state / "profiles/LocalMerge.yaml"),
                          str(query_state / "profiles/LocalRules.yaml"), "LocalMerge.yaml"],
              f"registry helpers disagreed with quoted/reordered YAML: {queried}")

        # Missing current is a presentation state, not a malformed binding: the
        # main menu and path display must remain byte-equivalent to the baseline.
        no_current_home = root / "no-current-home"
        no_current_state = no_current_home / ".local/share/io.github.clash-verge-rev.clash-verge-rev"
        (no_current_state / "profiles").mkdir(parents=True)
        no_current_registry = (
            "items:\n- uid: Merge\n  type: merge\n  file: Merge.yaml\n"
            "- uid: Script\n  type: script\n  file: Script.js\n"
        )
        (no_current_state / "profiles.yaml").write_text(no_current_registry)
        no_current_env = dict(os.environ, HOME=str(no_current_home), RULES_DIR=str(rules))
        baseline_menu = run(["bash", str(baseline_source)], env=no_current_env, cwd=root,
                            input="0\n")
        current_menu = run(["bash", str(SOURCE)], env=no_current_env, cwd=root, input="0\n")
        check(current_menu.stdout == baseline_menu.stdout and
              "当前订阅: (profiles.yaml 中没有 current)" in current_menu.stdout and
              "Clash Verge 优化工具 - 主菜单" in current_menu.stdout,
              "no-current dispatcher no longer matches baseline menu behavior")
        baseline_paths = run(["bash", str(baseline_source)], env=no_current_env, cwd=root,
                             input="3\n0\n")
        current_paths = run(["bash", str(SOURCE)], env=no_current_env, cwd=root,
                            input="3\n0\n")
        check(current_paths.stdout == baseline_paths.stdout and
              "当前订阅: (路径缺失)" in current_paths.stdout and
              "订阅级 Merge: (未绑定)" in current_paths.stdout,
              "no-current path display no longer matches baseline behavior")

        # Explicit malformed current values/references remain errors.
        (no_current_state / "profiles.yaml").write_text("current: null\n" + no_current_registry)
        malformed_current = run(["bash", str(SOURCE)], env=no_current_env, cwd=root,
                                expected=1, input="0\n")
        check("explicit current must name" in malformed_current.stderr,
              "explicit null current was masked by the no-current fallback")
        (no_current_state / "profiles.yaml").write_text("current: missing\n" + no_current_registry)
        missing_current = run(["bash", str(SOURCE)], env=no_current_env, cwd=root,
                              expected=1, input="0\n")
        check("current references missing uid" in missing_current.stderr,
              "explicit missing current binding was masked")

        # Read-only paths use arbitrary cwd and no profile registry.
        no_profile = dict(env, HOME=str(root / "no-profile"))
        check("route sources valid: 7 rule(s)" in run(["bash", str(SOURCE), "rules", "check"], env=no_profile, cwd=root).stdout,
              "check did not read isolated sources")
        rendered = run(["bash", str(SOURCE), "rules", "render"], env=no_profile, cwd=root).stdout
        check(rendered.startswith("// Generated by tun-fix.sh"), "render missing ownership marker")
        check(not (root / "no-profile").exists(), "check/render wrote profile state")

        # Apply replaces only the registered Script and leaves unrelated state alone.
        run(["bash", str(SOURCE), "rules", "apply"], env=env, cwd=root)
        js = profiles / "Script.js"
        first = js.read_text()
        run(["bash", str(SOURCE), "rules", "apply"], env=env, cwd=root)
        check(js.read_text() == first, "repeat generated Script differs")
        check(merge.read_text() == "untouched merge\n" and runtime.read_text() == "untouched runtime\n" and ssh.read_text() == "Host untouched\n",
              "rules apply changed unrelated files")

        base = {"proxies": [], "proxy-groups": [{"name": "Proxy", "type": "select", "proxies": ["DIRECT"]}],
                "rules": ["DOMAIN-SUFFIX,example,DIRECT", "DOMAIN,force.example,DIRECT",
                          "DOMAIN,proxy.example,Proxy", "DOMAIN,subscription.cn,Proxy",
                          "DOMAIN-SUFFIX,cn,DIRECT", "MATCH,Proxy"]}
        ordered = host_wrapped_js(js, base)["rules"]
        check(ordered == [
            "DOMAIN,force.example,DIRECT", "DOMAIN,ssh.github.com,DIRECT", "DOMAIN,proxy.example,Proxy",
            "DOMAIN-SUFFIX,example,DIRECT", "DOMAIN,subscription.cn,Proxy", "DOMAIN-SUFFIX,cn,DIRECT",
            "IP-CIDR,2001:db8::/32,DIRECT,no-resolve", "DOMAIN,deleted.example,DIRECT",
            "DOMAIN,late.example,Proxy", "MATCH,Proxy"], f"unexpected ordering: {ordered}")
        check(core_validate(root, {**base, "rules": ordered}, "valid", 0), "valid core output unexpectedly empty")

        # Source-faithful host wrapper receives invalid JSON objects, not throws.
        missing_group = host_wrapped_js(js, {"proxies": [], "proxy-groups": [], "rules": ["MATCH,DIRECT"]})
        missing_group_output = core_validate(root, missing_group, "missing-group", 1)
        check("LOCAL-RULES-ERROR-MISSING-PROXY-GROUP" in missing_group_output,
              "missing group guard was not rejected by real core")
        missing_match = host_wrapped_js(js, {"proxies": [], "proxy-groups": [{"name": "Proxy", "type": "select", "proxies": ["DIRECT"]}], "rules": []})
        missing_match_output = core_validate(root, missing_match, "missing-match", 1)
        check("LOCAL-RULES-ERROR-MISSING-MATCH" in missing_match_output,
              "missing MATCH guard was not rejected by real core")

        # Strict grammar failures occur before candidate/backup/replacement.
        stable = js.read_text()
        for bad_rule, diagnostic in (
            ("DOMAIN-SUFIX,example.com,DIRECT", "unsupported matcher"),
            ("IP-CIDR,not-a-cidr,DIRECT,no-resolve", "valid IPv4 or IPv6 CIDR"),
            ("DST-PORT,70000,DIRECT", "DST-PORT payload"),
            ("DOMAIN,example.com,DIRECT,no-resolve", "no-resolve is not supported"),
            ("IP-CIDR,192.0.2.0/24,no-resolve,DIRECT", "only once as the final option"),
            ("IP-CIDR,192.0.2.0/24,DIRECT,no-resolve,no-resolve", "only once as the final option"),
        ):
            (rules / "direct.yaml").write_text(f'version: 1\npre:\n  - "{bad_rule}"\npost: []\n')
            checked_bad = run(["bash", str(SOURCE), "rules", "check"], env=env, cwd=root, expected=1)
            failed = run(["bash", str(SOURCE), "rules", "apply"], env=env, cwd=root, expected=1)
            check(diagnostic in checked_bad.stderr and diagnostic in failed.stderr and js.read_text() == stable,
                  f"invalid source {bad_rule} mutated Script or lacked diagnostic")

        # A bound legacy Rules extension blocks migration even after source removal.
        (rules / "direct.yaml").write_text(direct)
        (rules / "proxy.yaml").write_text('version: 1\npre:\n  - "DOMAIN,example.com,REJECT"\npost: []\n')
        reserved_target = run(["bash", str(SOURCE), "rules", "check"], env=env, cwd=root, expected=1)
        check("must name a subscription proxy group" in reserved_target.stderr, "proxy built-in target was accepted")
        (rules / "proxy.yaml").write_text(proxy)
        (state / "profiles.yaml").write_text(
            "items:\n- uid: Script\n  type: script\n  file: Script.js\n"
            "- uid: legacy-rules\n  type: rules\n  file: legacy.yaml\n"
            "- uid: remote-a\n  type: remote\n  option:\n    rules: legacy-rules\n"
        )
        legacy = profiles / "legacy.yaml"
        legacy.write_text('prepend:\n  - "DOMAIN,ssh.github.com,DIRECT"\nappend: []\n')
        refused = run(["bash", str(SOURCE), "rules", "apply"], env=env, cwd=root, expected=1)
        check(f"{legacy}:2: DOMAIN,ssh.github.com,DIRECT" in refused.stderr and js.read_text() == stable,
              "legacy GitHub duplicate was not reported before replacement")
        # Operator cleans only the named local extension; source removal then stops injection on a fresh base.
        legacy.write_text("prepend: []\nappend: []\n")
        (rules / "direct.yaml").write_text(direct.replace('  - "DOMAIN,ssh.github.com,DIRECT"\n', ""))
        run(["bash", str(SOURCE), "rules", "apply"], env=env, cwd=root)
        rebuilt = host_wrapped_js(js, {"proxies": [], "proxy-groups": [{"name": "Proxy", "type": "select", "proxies": ["DIRECT"]}],
                                       "rules": ["DOMAIN-SUFFIX,github.com,Proxy", "MATCH,Proxy"]})["rules"]
        check("DOMAIN,ssh.github.com,DIRECT" not in rebuilt and rebuilt[-1] == "MATCH,Proxy",
              "source removal still injected GitHub rule after migration cleanup")

        # Registry null/mixed duplicate Script entries reject without an orphan output.
        js.unlink()
        (state / "profiles.yaml").write_text("items:\n- uid: Script\n  type: script\n  file: null\n")
        run(["bash", str(SOURCE), "rules", "apply"], env=env, cwd=root, expected=1)
        check(not (profiles / "null").exists() and not js.exists(), "null registry file produced orphan")
        (state / "profiles.yaml").write_text("items: []\n")
        run(["bash", str(SOURCE), "rules", "apply"], env=env, cwd=root, expected=1)
        check(not js.exists(), "unregistered registry wrote output")
        (state / "profiles.yaml").write_text("items:\n- uid: Script\n  type: script\n  file: Script.js\n- uid: Script\n  type: merge\n  file: Other.yaml\n")
        run(["bash", str(SOURCE), "rules", "apply"], env=env, cwd=root, expected=1)
        check(not js.exists(), "duplicate mixed Script registry wrote output")

        # Confirmed foreign script has collision-free backups even for rapid applies.
        (state / "profiles.yaml").write_text("items:\n- uid: Script\n  type: script\n  file: Script.js\n")
        for prior_backup in profiles.glob("Script.js.backup.*"):
            prior_backup.unlink()
        js.write_text("// foreign script\n")
        run(["bash", str(SOURCE), "rules", "apply"], env=env, cwd=root, input="y\n")
        run(["bash", str(SOURCE), "rules", "apply"], env=env, cwd=root)
        backups = sorted(profiles.glob("Script.js.backup.*"))
        check(len(backups) >= 2 and any(backup.read_text() == "// foreign script\n" for backup in backups),
              "rapid applies overwrote the foreign-script backup")
        check(not list(profiles.glob(".Script.js.candidate.*")), "failed/finished apply left candidate files")
        check(all(re.fullmatch(r"Script\.js\.backup\.\d{8}_\d{6}\.[A-Za-z0-9]+", backup.name) for backup in backups),
              "Script backup no longer has the backup-menu timestamp prefix")
        listed_backups = run(["bash", "-c", 'source "$1"; get_backup_files; list_backups', "test", str(SOURCE)],
                             env=env, cwd=root).stdout
        check(all(backup.name in listed_backups for backup in backups) and "备份时间" in listed_backups,
              "backup menu cannot discover/display collision-free Script backups")

        restore_home = root / "restore-home"
        restore_profiles = restore_home / ".local/share/io.github.clash-verge-rev.clash-verge-rev/profiles"
        restore_profiles.mkdir(parents=True)
        restore_target = restore_profiles / "Restore.yaml"
        restore_target.write_text("original\n")
        restore_env = dict(os.environ, HOME=str(restore_home), RULES_DIR=str(rules))
        restored = run(["bash", "-c",
                        'source "$1"; unique_backup "$2"; printf "changed\\n" > "$2"; restore_backup',
                        "test", str(SOURCE), str(restore_target)],
                       env=restore_env, cwd=root, input="1\n")
        restore_backups = list(restore_profiles.glob("Restore.yaml.backup.*"))
        check(restore_target.read_text() == "original\n" and len(restore_backups) == 1 and
              re.fullmatch(r"Restore\.yaml\.backup\.\d{8}_\d{6}\.[A-Za-z0-9]+", restore_backups[0].name) and
              "已恢复: Restore.yaml" in restored.stdout and
              not list(restore_profiles.glob(".Restore.yaml.restore.*")),
              "backup restore did not atomically recover the selected unique backup")

        # Full optimizer must prepare every Script-specific condition before it
        # reaches its Merge backup/clear/rewrite calls. Mutating helpers are stubbed.
        assert_optimizer_preflight_failure(root, rules, "null",
            "items:\n- uid: Script\n  type: script\n  file: null\n")
        assert_optimizer_preflight_failure(root, rules, "absent", "items: []\n")
        assert_optimizer_preflight_failure(root, rules, "wrong-type",
            "items:\n- uid: Script\n  type: merge\n  file: Script.js\n")
        assert_optimizer_preflight_failure(root, rules, "unsafe",
            "items:\n- uid: Script\n  type: script\n  file: ../Script.js\n")
        assert_optimizer_preflight_failure(root, rules, "missing-directory",
            "items:\n- uid: Script\n  type: script\n  file: Script.js\n", profile_dir=False)
        assert_optimizer_preflight_failure(root, rules, "renderer-failure",
            "items:\n- uid: Script\n  type: script\n  file: Script.js\n",
            script_text="// Generated by tun-fix.sh sentinel\n", expect_prepare_failure=True,
            extra=r'''python3(){
for argument in "$@"; do
    if [ "$argument" = "--prepare" ]; then
        printf 'actual --prepare invoked\n' > "$PREPARE_MARKER"
        echo 'forced --prepare failure' >&2
        return 91
    fi
done
command python3 "$@"
}
verify_route_rules(){ echo probe > "$PROBE_MARKER"; return 99; }
''')
        assert_optimizer_preflight_failure(root, rules, "foreign-declined",
            "items:\n- uid: Script\n  type: script\n  file: Script.js\n", input="n\n")
        assert_optimizer_preflight_failure(root, rules, "merge-null",
            "items:\n- uid: Script\n  type: script\n  file: Script.js\n"
            "- uid: Merge\n  type: merge\n  file: null\n")
        assert_optimizer_preflight_failure(root, rules, "merge-duplicate",
            "items:\n- uid: Script\n  type: script\n  file: Script.js\n"
            "- uid: Merge\n  type: merge\n  file: Merge.yaml\n"
            "- uid: Merge\n  type: merge\n  file: Other.yaml\n")
        assert_optimizer_preflight_failure(root, rules, "remote-merge-missing",
            "items:\n- uid: Script\n  type: script\n  file: Script.js\n"
            "- uid: remote-a\n  type: remote\n  file: remote.yaml\n"
            "  option:\n    merge: missing-merge\n")

        success_home = root / "optimizer-success-home"
        success_state = success_home / ".local/share/io.github.clash-verge-rev.clash-verge-rev"
        success_profiles = success_state / "profiles"
        success_profiles.mkdir(parents=True)
        (success_state / "profiles.yaml").write_text(registry_with_merge(
            "items:\n- uid: Script\n  type: script\n  file: Script.js\n"))
        success_merge = success_profiles / "Merge.yaml"
        success_merge.write_text("merge sentinel\n")
        success_env = dict(os.environ, HOME=str(success_home), RULES_DIR=str(rules))
        run(optimizer_shell(SOURCE, success_merge, forbid_mutators=False), env=success_env, cwd=root)
        check((success_profiles / "Script.js").read_text().startswith("// Generated by tun-fix.sh") and
              not list(success_profiles.glob(".Script.js.candidate.*")),
              "stubbed optimizer success did not consume its preflight candidate")

        # A legacy mutator failure remains governed by set -e: false stops the
        # body before its following echo, Script consumption, or candidate leak.
        errexit_home = root / "optimizer-errexit-home"
        errexit_state = errexit_home / ".local/share/io.github.clash-verge-rev.clash-verge-rev"
        errexit_profiles = errexit_state / "profiles"
        errexit_profiles.mkdir(parents=True)
        (errexit_state / "profiles.yaml").write_text(registry_with_merge(
            "items:\n- uid: Script\n  type: script\n  file: Script.js\n"))
        errexit_script = errexit_profiles / "Script.js"
        errexit_script.write_text("// Generated by tun-fix.sh sentinel\n")
        errexit_merge = errexit_profiles / "Merge.yaml"
        errexit_merge.write_text("merge sentinel\n")
        errexit_marker = errexit_state / "should-not-run.marker"
        errexit_env = dict(os.environ, HOME=str(errexit_home), RULES_DIR=str(rules),
                           OPTIMIZER_MARKER=str(errexit_marker))
        run(optimizer_shell(SOURCE, errexit_merge,
                            extra='update_fake_ip_filter(){ false; echo should-not-run > "$OPTIMIZER_MARKER"; }',
                            forbid_mutators=False), env=errexit_env, cwd=root, expected=1)
        check(not errexit_marker.exists() and errexit_script.read_text() == "// Generated by tun-fix.sh sentinel\n" and
              not list(errexit_profiles.glob(".Script.js.candidate.*")),
              "legacy mutator failure was swallowed or prepared candidate was committed/leaked")

        # The pre-existing GitHub SSH generator remains callable from the new CLI source.
        ssh_block = run(["bash", "-c", 'source "$1"; github_ssh_block', "test", str(SOURCE)], env=env, cwd=root).stdout
        check("Host github.com ssh.github.com" in ssh_block and "IPQoS none" in ssh_block and
              "ProxyCommand" not in ssh_block and "ProxyJump" not in ssh_block,
              "GitHub SSH no-jump/QoS policy regressed")

    print("PASS: source-derived runtime diagnostics, exact Merge differentials/idempotence, strict YAML registry/migration, host-faithful guards, real Mihomo validation, preflight no-write failures, atomic unique backup/restore, and scoped probes")


if __name__ == "__main__":
    main()

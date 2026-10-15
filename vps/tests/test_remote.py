"""Execute remote transaction shell in a rewritten, unprivileged fake VPS root.

No service, nft, SSH, install or Tailscale commands can reach the real host.
These tests cover shell control flow and file restoration, not kernel behavior.
"""
import json
import os
import re
from pathlib import Path
import subprocess
import tempfile
import unittest

MODULE = Path(__file__).resolve().parents[1]


class RemoteTransaction(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="qd-vps-remote-test-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.state = self.root / "var/lib/quick-deploy-vps"
        self.lib = self.root / "usr/local/lib/quick-deploy-vps"
        self.etc = self.root / "etc"
        for directory in (self.state / "incoming", self.lib, self.etc / "sing-box", self.etc / "ssh/sshd_config.d",
                          self.etc / "systemd/system/sing-box.service.d", self.root / "run"):
            directory.mkdir(parents=True, exist_ok=True)
        (self.etc / "os-release").write_text('ID=ubuntu\nVERSION_ID=24.04\n')
        source = (MODULE / "lib/remote.sh").read_text()
        source = source.replace('[[ $EUID == 0 ]]', '[[ 1 == 1 ]]')
        # Longest roots first; only this fixture copy is rewritten.
        for absolute in ("/usr/local/lib/quick-deploy-vps", "/var/lib/quick-deploy-vps", "/etc", "/run/qd-vps-nft"):
            source = source.replace(absolute, str(self.root) + absolute)
        self.script = self.lib / "remote.sh"
        self.script.write_text(source)
        self.script.chmod(0o700)
        self.log = self.root / "commands.log"
        self.env = dict(os.environ, PATH=f"{self.bin}:{os.environ['PATH']}", QD_LOG=str(self.log), SSH_CONNECTION="198.51.100.5 12345 203.0.113.1 22")
        shell_env = self.root / 'bash-env.sh'
        shell_env.write_text('command() {\n'
            ' if [[ "$1" == -v && "${2:-}" == tailscale && "${QD_TS_ABSENT:-yes}" == yes && ! -f "' + str(self.state / 'install-tailscale.sh') + '" ]]; then return 1; fi\n'
            ' builtin command "$@"\n}\n')
        self.env['BASH_ENV'] = str(shell_env)
        (self.state / "incoming/params.json").write_text(json.dumps({"role": "proxy", "ssh_user": "root", "without_tailscale": False}))
        self.stub("systemctl", '''printf 'systemctl %s\\n' "$*" >>"$QD_LOG"
case "$*" in 'show -p User --value sing-box') echo qd-vps-proxy;; 'show -p MainPID --value sing-box') echo 4242;; esac
if [ "${QD_FAIL_RESTART:-}" = yes ] && [ "$*" = 'restart sing-box' ]; then exit 73; fi
exit 0
''')
        self.stub("nft", '''printf 'nft %s\\n' "$*" >>"$QD_LOG"
if [[ " $* " == *' list '* ]]; then printf 'table inet qd_vps { meta skuid 998 jump proxy_output; }\\n'; fi
exit 0
''')
        self.stub("sing-box", 'printf "sing-box %s\\n" "$*" >>"$QD_LOG"\nexit 0\n')
        self.stub("id", 'if [ "${1:-}" = -u ] && [ "${2:-}" = qd-vps-proxy ]; then echo 998; else exec /usr/bin/id "$@"; fi\n')
        self.stub("ps", 'echo "${QD_PROCESS_UID:-998}"\n')
        self.stub("readlink", 'echo /usr/bin/sing-box\n')
        self.stub("ss", 'echo listening\n')
        self.stub("sleep", 'exit 0\n')
        self.stub("tailscale", 'if [[ "${1:-}" == debug ]]; then echo \'{"RelayServerPort":null,"RelayServerStaticEndpoints":[]}\'; else echo \'{"BackendState":"Running","Self":{"Tags":["tag:egress"]}}\'; fi\n')
        self.stub("sshd", '''printf 'sshd %s\\n' "$*" >>"$QD_LOG"
if [ "${1:-}" = -T ]; then
printf 'passwordauthentication no\\npermitrootlogin without-password\\n'
# Force more than one pipe buffer after the matching lines: old grep -q
# pipelines cause this producer to SIGPIPE under pipefail.
python3 -c 'print("padding " + "x" * 32768)'
fi
exit 0
''')
        self.stub("systemd-run", 'printf "systemd-run %s\\n" "$*" >>"$QD_LOG"\nexit 0\n')
        self.stub("install", '''args=()
while [ "$#" -gt 0 ]; do
case "$1" in -o|-g) shift 2;; *) args+=("$1"); shift;; esac
done
exec /usr/bin/install "${args[@]}"
''')
        for command in ("curl", "apt-get", "useradd", "ssh", "rsync"):
            self.stub(command, f'echo "UNEXPECTED {command}" >&2\nexit 90\n')
        checkpoint = self.state / "checkpoint"
        checkpoint.mkdir()
        (checkpoint / "config.json").write_text('old configuration\n')
        (checkpoint / "service.conf").write_text('[Service]\nUser=qd-vps-proxy\n')
        (checkpoint / "table.nft").write_text('table inet qd_vps { # old table\n}\n')
        (checkpoint / "persistent.nft").write_text('old persistent table\n')
        for flag in ("had-table", "was-active", "fw-enabled"):
            (checkpoint / flag).touch()
        (self.state / "prepared").write_text(str(checkpoint) + "\n")
        (self.state / "candidate.nft").write_text('table inet qd_vps { # new\n}\n')
        (self.state / "candidate-transaction.nft").write_text('delete table inet qd_vps\ntable inet qd_vps {}\n')
        (self.state / "incoming/config.json").write_text('new configuration\n')
        (self.etc / "sing-box/config.json").write_text('old configuration\n')
        (self.etc / "systemd/system/sing-box.service.d/90-quick-deploy-vps.conf").write_text('[Service]\nUser=qd-vps-proxy\n')

    def stub(self, name, body):
        path = self.bin / name
        path.write_text('#!/usr/bin/env bash\nset -euo pipefail\n' + body)
        path.chmod(0o700)

    def action(self, action, **env):
        return subprocess.run(["bash", str(self.script), action, '', getattr(self, 'role', 'proxy')], env=dict(self.env, **env), capture_output=True, text=True)

    def test_apply_requires_confirmation_then_commit_persists(self):
        apply = self.action("apply")
        self.assertEqual(apply.returncode, 0, apply.stdout + apply.stderr)
        self.assertTrue((self.state / "pending").exists())
        self.assertEqual((self.etc / "sing-box/config.json").read_text(), 'new configuration\n')
        self.assertLess(self.log.read_text().index('systemd-run'), self.log.read_text().index('restart sing-box'))
        self.assertEqual(self.action("harden").returncode, 0)
        self.assertEqual(self.action("commit").returncode, 0)
        self.assertFalse((self.state / "pending").exists())
        self.assertTrue((self.state / "previous").exists())
        self.assertIn('stop quick-deploy-vps-revert.timer', self.log.read_text())

    def test_restart_failure_trap_restores_exact_files_and_clears_pending(self):
        failed = self.action("apply", QD_FAIL_RESTART="yes")
        self.assertNotEqual(failed.returncode, 0)
        self.assertEqual((self.etc / "sing-box/config.json").read_text(), 'old configuration\n')
        self.assertEqual((self.etc / "quick-deploy-vps.nft").read_text(), 'old persistent table\n')
        self.assertFalse((self.state / "pending").exists())
        self.assertIn('Restored pre-deployment', failed.stdout)
        self.assertEqual((self.etc / "systemd/system/sing-box.service.d/90-quick-deploy-vps.conf").read_text(), '[Service]\nUser=qd-vps-proxy\n')

    def test_timer_abort_restores_without_confirmation_and_repeat_is_noop(self):
        self.assertEqual(self.action("apply").returncode, 0)
        self.assertEqual(self.action("harden").returncode, 0)
        self.assertEqual(self.action("abort").returncode, 0)
        self.assertFalse((self.etc / "ssh/sshd_config.d/00-quick-deploy-vps.conf").exists())
        self.assertEqual((self.etc / "sing-box/config.json").read_text(), 'old configuration\n')
        repeated = self.action("abort")
        self.assertNotEqual(repeated.returncode, 0)
        self.assertIn('Refusing abort: no pending', repeated.stderr)
        self.assertNotEqual(self.action("commit").returncode, 0)

    def test_rollback_is_repeatable_and_does_not_toggle(self):
        self.assertEqual(self.action("apply").returncode, 0)
        self.assertEqual(self.action("commit").returncode, 0)
        for _ in range(2):
            result = self.action("rollback")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((self.etc / "sing-box/config.json").read_text(), 'old configuration\n')
            self.assertTrue((self.state / "pending").exists())
            self.assertEqual(self.action("commit").returncode, 0)

    def test_wrong_os_refused_before_commands(self):
        (self.etc / "os-release").write_text('ID=debian\nVERSION_ID=12\n')
        result = self.action("prepare")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Only Ubuntu 24.04', result.stderr)
        self.assertFalse(self.log.exists())

    def test_pristine_rollback_then_reboot_cannot_start_public_example(self):
        checkpoint = self.state / "checkpoint"
        for path in checkpoint.iterdir():
            path.unlink()
        # Simulate persistent unit state and a reboot independently of process
        # activity. Old restore left this enabled flag behind after commit.
        self.stub("systemctl", '''printf 'systemctl %s\\n' "$*" >>"$QD_LOG"
root=$(dirname "$QD_LOG")
case "$*" in
'show -p User --value sing-box') echo qd-vps-proxy;;
'show -p MainPID --value sing-box') echo 4242;;
'enable sing-box') touch "$root/enabled";;
'disable --now sing-box') rm -f "$root/enabled" "$root/running";;
'reboot') if [ -f "$root/enabled" ]; then touch "$root/running"; fi;;
'restart sing-box'|'start sing-box') touch "$root/running";;
'is-active --quiet sing-box') test -f "$root/running"; exit $?;;
'is-enabled --quiet sing-box') test -f "$root/enabled"; exit $?;;
esac
exit 0
''')
        self.assertEqual(self.action("apply").returncode, 0)
        self.assertEqual(self.action("commit").returncode, 0)
        self.assertTrue((self.root / "enabled").exists())
        result = self.action("rollback")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.action("transaction-health").returncode, 0)
        self.assertEqual(self.action("commit").returncode, 0)
        subprocess.run([str(self.bin / "systemctl"), "reboot"], env=self.env, check=True)
        self.assertFalse((self.root / "running").exists())
        self.assertFalse((self.root / "enabled").exists())
        self.assertFalse((self.etc / "sing-box/config.json").exists())
        self.assertFalse((self.etc / "systemd/system/sing-box.service.d/90-quick-deploy-vps.conf").exists())

    def test_enabled_state_and_fresh_package_cleanup_order(self):
        source = self.script.read_text()
        self.assertIn('systemctl is-enabled --quiet sing-box', source)
        fresh = source.split('apt-get -o DPkg::Lock::Timeout=300 install -y "sing-box=$version"', 1)[1].split('rm -f "$ROOT/mask-owned"', 1)[0]
        self.assertLess(fresh.index('rm -f "$CONF"'), fresh.index('systemctl unmask'))
        restore = source.split('restore() {', 1)[1].split('health() {', 1)[0]
        self.assertLess(restore.index('disable --now sing-box'), restore.index('nft -f'))
        commit = source.split('commit)\n', 1)[1].split('abort)\n', 1)[0]
        self.assertGreater(commit.index('systemctl enable sing-box'), commit.index('rm -f'))

    def test_timer_absence_is_tolerated_and_elapsed_timer_does_not_linger(self):
        self.stub("systemctl", '''printf 'systemctl %s\\n' "$*" >>"$QD_LOG"
if [ "$*" = 'stop quick-deploy-vps-revert.timer' ]; then exit 5; fi
case "$*" in 'show -p User --value sing-box') echo qd-vps-proxy;; 'show -p MainPID --value sing-box') echo 4242;; esac
exit 0
''')
        self.assertEqual(self.action("apply").returncode, 0)
        self.assertIn('--timer-property=RemainAfterElapse=no', self.log.read_text())
        self.assertLess(self.log.read_text().index('stop quick-deploy-vps-revert.timer'), self.log.read_text().index('systemd-run'))
        self.assertEqual(self.action("abort").returncode, 0)
        (self.state / "prepared").write_text(str(self.state / "checkpoint") + "\n")
        self.assertEqual(self.action("apply").returncode, 0)
        self.assertEqual(self.action("commit").returncode, 0)

    def test_abort_uses_unbounded_lock_and_rollback_has_undo_timer(self):
        self.stub("flock", 'printf "flock %s\\n" "$*" >>"$QD_LOG"\nexec /usr/bin/flock "$@"\n')
        self.assertEqual(self.action("apply").returncode, 0)
        self.assertEqual(self.action("commit").returncode, 0)
        self.assertEqual(self.action("rollback").returncode, 0)
        self.assertEqual((self.state / "pending-kind").read_text().strip(), 'rollback')
        self.assertEqual((self.etc / "sing-box/config.json").read_text(), 'old configuration\n')
        self.assertEqual(self.action("abort").returncode, 0)
        self.assertEqual((self.etc / "sing-box/config.json").read_text(), 'new configuration\n')
        self.assertIn('flock 8\n', self.log.read_text())

    def test_harden_consumes_large_effective_config_with_match_context(self):
        self.assertEqual(self.action("apply").returncode, 0)
        result = self.action("harden")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('sshd -T -C user=root,host=x,addr=198.51.100.5', self.log.read_text())
        self.assertIn('systemctl reload ssh', self.log.read_text())

    def test_no_early_exit_pipeline_consumers_under_pipefail(self):
        for path in MODULE.rglob("*.sh"):
            self.assertIsNone(re.search(r"\|\s*(?:grep\s+-[^\n]*q|head\b)", path.read_text()), str(path))

    def prepare_tools(self):
        # Exercise full prepare without apt/network/root account mutations.
        home = self.root / "account"
        home.mkdir()
        (self.etc / "apt/sources.list.d").mkdir(parents=True)
        self.env["QD_ACCOUNT"] = str(home)
        self.env["QD_CONF"] = str(self.etc / "sing-box/config.json")
        (self.state / "incoming/params.json").write_text(json.dumps({"role": "proxy", "ssh_user": "root", "sing_box_version": "1.14.3", "target": "example.net", "without_tailscale": False}))
        (self.state / "incoming/authorized_keys").write_text('ssh-ed25519 fixture\n')
        (self.state / "incoming/firewall.template").write_text('table inet qd_vps { meta skuid 999 jump proxy_output; }\n')
        (self.state / "incoming/remote.sh").write_text(self.script.read_text())
        self.stub("curl", 'while [ "$#" -gt 0 ]; do if [ "$1" = -o ]; then touch "$2"; exit 0; fi; shift; done\n')
        self.stub("apt-get", '''printf 'apt-get %s frontend=%s\\n' "$*" "${DEBIAN_FRONTEND:-}" >>"$QD_LOG"
if [[ "$*" == *sing-box=* ]]; then echo public-example >"$QD_CONF"; fi
if [[ "${QD_APT_FAIL:-}" == yes ]]; then exit 8; fi
''')
        self.stub("apt-mark", 'printf "apt-mark %s\\n" "$*" >>"$QD_LOG"\n')
        self.stub("dpkg-query", 'printf "%s" "${QD_INSTALLED_VERSION:-1.14.3}"\n')
        self.stub("getent", 'if [ "${2:-}" = root ]; then printf "root:x:0:0::%s:/bin/bash\\n" "$QD_ACCOUNT"; else echo qd-vps-proxy:x:998:998::/nonexistent:/usr/sbin/nologin; fi\n')
        self.stub("chown", 'exit 0\n')
        self.stub("tailscale", '''printf 'tailscale %s\\n' "$*" >>"$QD_LOG"
if [[ "${QD_PREPARE_SIGNAL:-}" != "" && "${1:-}" == up ]]; then kill -"$QD_PREPARE_SIGNAL" "$PPID"; exit 1; fi
if [[ "${2:-}" == --help ]]; then
 echo '--advertise-tags --accept-dns --accept-routes --hostname --timeout --auth-key file: --relay-server-port --relay-server-static-endpoints'
elif [[ "${1:-}" == debug ]]; then
 echo '{"RelayServerPort":null,"RelayServerStaticEndpoints":[]}'
elif [[ "${1:-}" == status ]]; then
 echo '{"BackendState":"Running","Self":{"Tags":["tag:egress"]}}'
elif [[ "${1:-}" == ip ]]; then echo 100.70.0.99
fi
''')
        self.stub("openssl", 'printf "TLSv1.3\\nALPN protocol: h2\\n"\n')

    def test_prepare_replaces_stale_marker_and_cleans_auth_on_success_and_signal(self):
        self.prepare_tools()
        key = self.state / "incoming/auth-key"
        key.write_text('secret fixture')
        result = self.action("prepare")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Replacing stale prepared candidate', result.stdout)
        self.assertFalse(key.exists())
        self.assertIn('apt-mark hold sing-box', self.log.read_text())
        self.assertIn('frontend=noninteractive', self.log.read_text())
        key.write_text('secret fixture')
        result = self.action("prepare", QD_PREPARE_SIGNAL="PIPE")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(key.exists())

    def test_prepare_failure_cleans_owned_mask_and_package_example(self):
        self.prepare_tools()
        key = self.state / "incoming/auth-key"
        key.write_text('secret fixture')
        (self.state / "mask-owned").touch()
        result = self.action("prepare", QD_APT_FAIL="yes")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(key.exists())
        self.assertFalse((self.state / "mask-owned").exists())
        self.assertFalse((self.etc / "sing-box/config.json").exists())
        self.assertIn('unmask --runtime sing-box.service', self.log.read_text())

    def test_prepare_version_mismatch_fails_before_hold_and_removes_auth(self):
        self.prepare_tools()
        key = self.state / "incoming/auth-key"
        key.write_text('secret fixture')
        result = self.action("prepare", QD_INSTALLED_VERSION="1.99.0")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('version mismatch', result.stderr)
        self.assertNotIn('apt-mark hold', self.log.read_text())
        self.assertFalse(key.exists())

    def fresh_prepare_tools(self):
        self.prepare_tools()
        # Choose the fresh-install branch in this fixture copy even on hosts
        # that already have sing-box; the package/service tools remain stubs.
        source = self.script.read_text().replace('if ! command -v sing-box >/dev/null; then', 'if true; then', 1)
        self.script.write_text(source)
        (self.state / "incoming/remote.sh").write_text(source)
        self.stub("systemctl", '''printf 'systemctl %s\\n' "$*" >>"$QD_LOG"
case "$*" in
'is-enabled --quiet sing-box'|'is-active --quiet sing-box') exit 1;;
'unmask --runtime sing-box.service') test ! -e "$QD_CONF";;
esac
''')
        self.stub("apt-get", '''printf 'apt-get %s\\n' "$*" >>"$QD_LOG"
if [[ "$*" == *sing-box=* ]]; then
 echo public-example >"$QD_CONF"
 if [[ "${QD_APT_PIPE:-}" == yes ]]; then kill -PIPE "$PPID"; exit 1; fi
fi
''')

    def test_fresh_package_example_is_removed_while_masked_and_not_snapshotted(self):
        self.fresh_prepare_tools()
        result = self.action("prepare")
        self.assertEqual(result.returncode, 0, result.stderr)
        checkpoint = Path((self.state / "prepared").read_text().strip())
        self.assertFalse((checkpoint / "config.json").exists())
        self.assertFalse((checkpoint / "was-enabled").exists())
        self.assertFalse((self.state / "mask-owned").exists())
        log = self.log.read_text()
        self.assertLess(log.index('mask --runtime sing-box.service'), log.index('apt-get -o DPkg::Lock::Timeout=300 install -y sing-box=1.14.3'))
        self.assertLess(log.index('disable --now sing-box'), log.index('unmask --runtime sing-box.service'))

    def test_fresh_package_sigpipe_cleans_example_key_and_mask(self):
        self.fresh_prepare_tools()
        key = self.state / "incoming/auth-key"
        key.write_text('secret fixture')
        result = self.action("prepare", QD_APT_PIPE="yes")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(key.exists())
        self.assertFalse((self.etc / "sing-box/config.json").exists())
        self.assertFalse((self.state / "mask-owned").exists())
        self.assertIn('unmask --runtime sing-box.service', self.log.read_text())

    def test_without_then_with_tailscale_preserves_running_proxy_idempotently(self):
        self.prepare_tools()
        # Force the missing-install branch in the safe fixture: stage1 must
        # not even download the installer, while stage2 can install/enroll.
        source = self.script.read_text().replace('if ! command -v tailscale >/dev/null; then\n            curl -fIsS', 'if true; then\n            curl -fIsS', 1)
        self.script.write_text(source)
        (self.state / 'incoming/remote.sh').write_text(source)
        params_path = self.state / "incoming/params.json"
        params = json.loads(params_path.read_text())
        params['without_tailscale'] = True
        params_path.write_text(json.dumps(params))
        first = self.action("prepare")
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertNotIn('tailscale ', self.log.read_text())
        self.assertNotIn('tailscaled', self.log.read_text())
        self.assertFalse((self.state / 'install-tailscale.sh').exists())
        self.assertEqual(self.action("apply").returncode, 0)
        health = self.action("health")
        self.assertEqual(health.returncode, 0, health.stderr)
        self.assertIn('SKIP Tailscale', health.stdout)
        self.assertEqual(self.action("tailnet-ip").stdout.strip(), 'skipped')
        self.assertEqual(self.action("commit").returncode, 0)
        config = self.etc / "sing-box/config.json"
        before = config.read_bytes(), config.stat().st_ino, config.stat().st_mtime_ns
        firewall = (self.etc / "quick-deploy-vps.nft").read_bytes()
        # Repeat proxy-only, enable overlay, repeat full stage: none may restart
        # or rewrite the already-correct proxy, or reapply its nft table.
        for without in (True, False, False):
            params['without_tailscale'] = without
            params_path.write_text(json.dumps(params))
            self.log.write_text('')
            result = self.action("prepare")
            self.assertEqual(result.returncode, 0, result.stderr)
            applied = self.action("apply")
            self.assertEqual(applied.returncode, 0, applied.stderr)
            self.assertIn('Proxy unchanged', applied.stdout)
            checked = self.action("health")
            self.assertEqual(checked.returncode, 0, checked.stderr)
            self.assertEqual('SKIP Tailscale' in checked.stdout, without)
            self.assertEqual(self.action("commit").returncode, 0)
            self.assertEqual((config.read_bytes(), config.stat().st_ino, config.stat().st_mtime_ns), before)
            self.assertEqual((self.etc / "quick-deploy-vps.nft").read_bytes(), firewall)
            log = self.log.read_text()
            self.assertNotIn('restart sing-box', log)
            self.assertNotIn('disable --now sing-box', log)
            self.assertNotIn('nft -f ', log)
            self.assertIn('apt-get -o DPkg::Lock::Timeout=300 install -y --no-upgrade ca-certificates', log)
            self.assertEqual('tailscale up ' in log, not without)
            self.assertEqual('tailscale set --relay-server-port=' in log, not without)
            self.assertNotIn('tailscale set --relay-server-port=40000', log)
            self.assertEqual('systemctl enable --now tailscaled' in log, not without)
            self.assertEqual((self.state / 'install-tailscale.sh').exists(), not without)

    def proxy_existing_tailscale(self):
        self.prepare_tools()
        self.env['QD_TS_ABSENT'] = 'no'
        path = self.state / 'incoming/params.json'
        params = json.loads(path.read_text())
        params['without_tailscale'] = True
        path.write_text(json.dumps(params))
        prefs = self.root / 'proxy-relay-prefs.json'
        prefs.write_text(json.dumps({'RelayServerPort': 40000, 'RelayServerStaticEndpoints': ['93.184.216.34:40000']}))
        argv = self.root / 'proxy-ts-argv.jsonl'
        self.stub('tailscale', f'''printf 'tailscale %s\\n' "$*" >>"$QD_LOG"
python3 -c 'import json,sys; print(json.dumps(sys.argv[1:]))' "$@" >>'{argv}'
if [[ "${{1:-}}" == set ]]; then echo '{{"RelayServerPort":null,"RelayServerStaticEndpoints":[]}}' >'{prefs}';
elif [[ "${{1:-}}" == debug ]]; then cat '{prefs}'; fi
''')
        return prefs, argv

    def test_existing_proxy_only_daemon_clears_relay_and_rollback_never_restores_it(self):
        prefs, argv_path = self.proxy_existing_tailscale()
        for action in ('prepare', 'apply', 'commit', 'prepare', 'apply', 'commit'):
            result = self.action(action)
            self.assertEqual(result.returncode, 0, action + result.stderr)
        argv = [json.loads(line) for line in argv_path.read_text().splitlines()]
        self.assertIn(['set', '--relay-server-port=', '--relay-server-static-endpoints='], argv)
        self.assertFalse(any(call[0] in ('up', 'down') for call in argv))
        prefs.write_text('{"RelayServerPort":40000,"RelayServerStaticEndpoints":["93.184.216.34:40000"]}')
        result = self.action('rollback')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(prefs.read_text())['RelayServerStaticEndpoints'], [])
        self.assertIsNone(json.loads(prefs.read_text())['RelayServerPort'])

    def test_inactive_existing_proxy_daemon_warns_in_apply_and_health_without_cli_calls(self):
        _, argv = self.proxy_existing_tailscale()
        self.stub('systemctl', '''printf 'systemctl %s\\n' "$*" >>"$QD_LOG"
if [[ "$*" == 'is-active --quiet tailscaled' ]]; then exit 3; fi
case "$*" in 'show -p User --value sing-box') echo qd-vps-proxy;; 'show -p MainPID --value sing-box') echo 4242;; esac
''')
        for action in ('prepare', 'apply', 'commit'):
            result = self.action(action)
            self.assertEqual(result.returncode, 0, result.stderr)
            if action == 'apply': self.assertIn('could not be verified', result.stderr)
        result = self.action('health')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('could not be verified', result.stderr)
        self.assertIn('SKIP Tailscale', result.stdout)
        self.assertFalse(argv.exists())

    def test_corrupt_stage_mode_never_silently_skips_or_enables_overlay_checks(self):
        (self.state / 'active-params.json').write_text('{"without_tailscale":"false"}')
        result = self.action('health')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('must be boolean', result.stderr)
        self.assertNotIn('SKIP Tailscale', result.stdout)
        self.assertNotEqual(self.action('tailnet-ip').returncode, 0)

    def test_failed_overlay_health_restores_stage_without_stopping_proxy(self):
        self.prepare_tools()
        params_path = self.state / "incoming/params.json"
        params = json.loads(params_path.read_text())
        params['without_tailscale'] = True
        params_path.write_text(json.dumps(params))
        for action in ('prepare', 'apply', 'commit'):
            self.assertEqual(self.action(action).returncode, 0)
        params['without_tailscale'] = False
        params_path.write_text(json.dumps(params))
        self.assertEqual(self.action('prepare').returncode, 0)
        self.log.write_text('')
        self.stub('tailscale', 'if [[ "${1:-}" == debug ]]; then echo \'{"RelayServerPort":null,"RelayServerStaticEndpoints":[]}\'; else echo \'{"BackendState":"Running","Self":{"Tags":[]}}\'; fi\n')
        result = self.action('apply')
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(json.loads((self.state / 'active-params.json').read_text())['without_tailscale'])
        self.assertNotIn('disable --now sing-box', self.log.read_text())
        self.assertNotIn('restart sing-box', self.log.read_text())
        self.assertFalse((self.state / 'pending').exists())
        self.assertIn('SKIP Tailscale', self.action('health').stdout)

    def test_pruning_keeps_last_good_and_recent_checkpoints_only(self):
        checkpoints = []
        for number in range(7):
            path = self.state / f"checkpoint-{number}"
            path.mkdir()
            (path / "secret").write_text('fixture secret')
            os.utime(path, (number, number))
            checkpoints.append(path)
        (self.state / "previous").write_text(str(checkpoints[0]) + "\n")
        foreign = self.state / "unrelated"
        foreign.mkdir()
        link = self.state / "checkpoint-link"
        link.symlink_to(foreign, target_is_directory=True)
        result = self.action("abort")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(checkpoints[0].exists())
        self.assertTrue(all(path.exists() for path in checkpoints[-3:]))
        self.assertTrue(all(not path.exists() for path in checkpoints[1:4]))
        self.assertTrue(foreign.exists())
        self.assertTrue(link.is_symlink())

    def test_health_rejects_unsubstituted_uid_or_root_proxy_process(self):
        self.assertEqual(self.action("health").returncode, 0)
        result = self.action("health", QD_PROCESS_UID="0")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('MainPID does not run as proxy UID', result.stderr)
        self.stub("nft", 'echo "table inet qd_vps { meta skuid 999 jump proxy_output; }"\n')
        result = self.action("health")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('nft proxy UID guard', result.stderr)

    def test_pinned_version_is_required_before_hold(self):
        source = self.script.read_text()
        self.assertIn('apt-get -o DPkg::Lock::Timeout=300 install -y "sing-box=$version"', source)
        self.assertLess(source.index('allow_upgrade == True'), source.index('apt-mark hold sing-box', source.index('prepare)\n')))

    def test_stale_running_binary_forces_restart_even_for_equal_config(self):
        self.prepare_tools()
        for action in ('prepare', 'apply', 'commit'):
            self.assertEqual(self.action(action).returncode, 0)
        self.assertEqual(self.action('prepare').returncode, 0)
        marker = self.root / 'readlink-called'
        self.stub('readlink', f'if [ ! -f "{marker}" ]; then touch "{marker}"; echo "/usr/bin/sing-box (deleted)"; else echo /usr/bin/sing-box; fi\n')
        self.log.write_text('')
        result = self.action('apply')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('restart sing-box', self.log.read_text())
        self.assertNotIn('Proxy unchanged', result.stdout)

    def test_upgrade_requires_authorization_and_reholds_exact_version(self):
        self.prepare_tools()
        params_path = self.state / 'incoming/params.json'
        params = json.loads(params_path.read_text())
        params['allow_upgrade'] = True
        params_path.write_text(json.dumps(params))
        marker = self.root / 'version-read'
        self.stub('dpkg-query', f'if [ ! -f "{marker}" ]; then touch "{marker}"; echo 1.13.12; else echo 1.14.3; fi\n')
        result = self.action('prepare')
        self.assertEqual(result.returncode, 0, result.stderr)
        log = self.log.read_text()
        self.assertLess(log.index('apt-mark unhold sing-box'), log.index('install -y sing-box=1.14.3'))
        self.assertLess(log.index('install -y sing-box=1.14.3'), log.index('apt-mark hold sing-box'))
        self.assertIn('-o Dpkg::Options::=--force-confold install -y sing-box=1.14.3', log)

    def test_official_installer_apt_config_is_inherited_and_cleaned_on_all_exits(self):
        self.prepare_tools()
        source = self.script.read_text().replace('if ! command -v tailscale >/dev/null; then\n            curl -fIsS', 'if true; then\n            curl -fIsS', 1)
        self.script.write_text(source)
        (self.state / 'incoming/remote.sh').write_text(source)
        installer = self.root / 'fake-official-installer.sh'
        record = self.root / 'installer-apt-path'
        contents = self.root / 'installer-apt-contents'
        installer.write_text(f'''#!/bin/sh
set -eu
test -f "$APT_CONFIG"
printf '%s' "$APT_CONFIG" >'{record}'
cat "$APT_CONFIG" >'{contents}'
if [ "${{QD_INSTALL_FAIL:-}}" = yes ]; then exit 7; fi
if [ "${{QD_INSTALL_SIGNAL:-}}" != '' ]; then kill -"$QD_INSTALL_SIGNAL" "$PPID"; exit 1; fi
''')
        self.env['QD_INSTALL_TEMPLATE'] = str(installer)
        self.stub('curl', '''while [ "$#" -gt 0 ]; do
 if [ "$1" = -o ]; then
  if [[ "$2" == */install-tailscale.sh ]]; then cp "$QD_INSTALL_TEMPLATE" "$2"; else touch "$2"; fi
  exit 0
 fi
 shift
done
''')
        for environment in ({}, {'QD_INSTALL_FAIL': 'yes'}, {'QD_INSTALL_SIGNAL': 'TERM'}, {'QD_INSTALL_SIGNAL': 'PIPE'}):
            with self.subTest(environment=environment):
                result = self.action('prepare', **environment)
                self.assertEqual(result.returncode == 0, not bool(environment), result.stderr)
                self.assertEqual(contents.read_text(), 'DPkg::Lock::Timeout "300";\n')
                self.assertFalse(Path(record.read_text()).exists())
                self.assertFalse(list(self.state.glob('installer-apt.*.conf')))

    def test_dependency_bootstrap_and_lock_timeout_precede_python(self):
        source = self.script.read_text().split('prepare)\n', 1)[1]
        self.assertLess(source.index('install -y --no-upgrade'), source.index('prune_checkpoints'))
        self.assertTrue(all('DPkg::Lock::Timeout=300' in line for line in source.splitlines() if 'apt-get ' in line))

    def test_rollback_waits_for_listeners_to_become_ready(self):
        self.assertEqual(self.action('apply').returncode, 0)
        self.assertEqual(self.action('commit').returncode, 0)
        marker = self.root / 'ss-read'
        self.stub('ss', f'if [ ! -f "{marker}" ]; then touch "{marker}"; else echo listening; fi\n')
        result = self.action('rollback')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(marker.exists())

    def test_health_tag_failure_is_explicit(self):
        self.stub("tailscale", 'if [[ "${1:-}" == debug ]]; then echo \'{"RelayServerPort":null,"RelayServerStaticEndpoints":[]}\'; else echo \'{"BackendState":"Running","Self":{"Tags":[]}}\'; fi\n')
        result = self.action("health")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('tag:egress not approved', result.stderr)


if __name__ == "__main__":
    unittest.main()

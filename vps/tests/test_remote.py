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
        (self.state / "incoming/params.json").write_text(json.dumps({"ssh_user": "root"}))
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
        self.stub("ss", 'echo listening\n')
        self.stub("sleep", 'exit 0\n')
        self.stub("tailscale", 'echo \'{"BackendState":"Running","Self":{"Tags":["tag:relay"]}}\'\n')
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
        return subprocess.run(["bash", str(self.script), action], env=dict(self.env, **env), capture_output=True, text=True)

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
        fresh = source.split('apt-get install -y "sing-box=$version"', 1)[1].split('rm -f "$ROOT/mask-owned"', 1)[0]
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
        (self.state / "incoming/params.json").write_text(json.dumps({"ssh_user": "root", "sing_box_version": "1.13.12", "target": "example.net"}))
        (self.state / "incoming/authorized_keys").write_text('ssh-ed25519 fixture\n')
        (self.state / "incoming/firewall.template").write_text('table inet qd_vps { meta skuid 999 jump proxy_output; }\n')
        (self.state / "incoming/remote.sh").write_text(self.script.read_text())
        self.stub("curl", 'while [ "$#" -gt 0 ]; do if [ "$1" = -o ]; then touch "$2"; exit 0; fi; shift; done\n')
        self.stub("apt-get", '''printf 'apt-get %s frontend=%s\\n' "$*" "${DEBIAN_FRONTEND:-}" >>"$QD_LOG"
if [[ "$*" == *sing-box=* ]]; then echo public-example >"$QD_CONF"; fi
if [[ "${QD_APT_FAIL:-}" == yes ]]; then exit 8; fi
''')
        self.stub("apt-mark", 'printf "apt-mark %s\\n" "$*" >>"$QD_LOG"\n')
        self.stub("dpkg-query", 'printf "%s" "${QD_INSTALLED_VERSION:-1.13.12}"\n')
        self.stub("getent", 'if [ "${2:-}" = root ]; then printf "root:x:0:0::%s:/bin/bash\\n" "$QD_ACCOUNT"; else echo qd-vps-proxy:x:998:998::/nonexistent:/usr/sbin/nologin; fi\n')
        self.stub("chown", 'exit 0\n')
        self.stub("tailscale", '''if [[ "${QD_PREPARE_SIGNAL:-}" != "" && "${1:-}" == up ]]; then kill -"$QD_PREPARE_SIGNAL" "$PPID"; exit 1; fi
if [[ "${2:-}" == --help ]]; then
 echo '--advertise-tags --accept-dns --accept-routes --hostname --timeout --auth-key file: --relay-server-port'
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
        self.assertIn('apt-get install -y "sing-box=$version"', source)
        self.assertLess(source.index('installed_version == "$version"'), source.index('apt-mark hold sing-box'))

    def test_health_tag_failure_is_explicit(self):
        self.stub("tailscale", 'echo \'{"BackendState":"Running","Self":{"Tags":[]}}\'\n')
        result = self.action("health")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('tag:relay not approved', result.stderr)


if __name__ == "__main__":
    unittest.main()

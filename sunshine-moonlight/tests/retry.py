#!/usr/bin/python3
"""Real guard subprocesses; retry timing below is a model, not a live manager."""
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import tempfile
import unittest

MODULE = Path(__file__).resolve().parents[1]
UNIT = 'app-dev.lizardbyte.app.Sunshine.service'
VALID = 'address_family = ipv4\n# exact identity\nbind_address = 100.64.0.2\n'


class RetryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='qd-retry-')
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / 'home'
        self.directory = self.home / '.config/sunshine'
        self.directory.mkdir(parents=True)
        self.conf = self.directory / 'sunshine.conf'
        self.conf.write_text(VALID)
        self.bin = Path(self.tmp.name) / 'bin'
        self.bin.mkdir()
        ip = self.bin / 'ip'
        ip.write_text('''#!/usr/bin/python3
import os, sys
assert sys.argv[1:] == ['-o', '-4', 'address', 'show', 'dev', 'tailscale0']
if os.environ.get('TEST_IP_FAIL'):
    sys.exit(1)
a = os.environ.get('TEST_ADDRESS', '100.64.0.2')
if a:
    print('7: tailscale0 inet ' + a + '/32 scope global tailscale0')
''')
        ip.chmod(0o755)
        self.env = dict(os.environ, HOME=str(self.home), PATH=f'{self.bin}:/usr/bin:/bin')
        for key in ('XDG_CONFIG_HOME', 'CONFIGURATION_DIRECTORY', 'QD_SUNSHINE_CONFIG_DIR'):
            self.env.pop(key, None)
        self.drop = subprocess.check_output(
            ['/bin/bash', '-c', '. "$1/lib/common.sh"; qd_retry_content "$2"',
             'fixture', str(MODULE), str(self.directory)], env=self.env, text=True)
        pre = next(line.split('=', 1)[1] for line in self.drop.splitlines()
                   if line.startswith('ExecStartPre='))
        self.assertTrue(pre.startswith(':/usr/bin/python3 '))
        self.command = shlex.split(pre[1:].replace('%%', '%'))
        helper = Path(self.command[1])
        helper.parent.mkdir(parents=True)
        shutil.copyfile(MODULE / 'service/check-tailnet.py', helper)
        self.app = self.bin / 'clean-exit-app'
        self.app.write_text('#!/bin/sh\nprintf "launch\\n" >> "$HOME/launches"\nexit 0\n')
        self.app.chmod(0o755)

    def guard(self, **changes):
        return subprocess.run(self.command, env=dict(self.env, **changes),
                              capture_output=True, text=True, timeout=10)

    def test_exact_address_and_no_mutation(self):
        before = self.conf.read_bytes()
        self.assertEqual(self.guard().returncode, 0)
        self.assertEqual(self.conf.read_bytes(), before)
        self.assertFalse((self.home / 'launches').exists())

    def test_absent_or_other_address_never_launches_clean_exit_app(self):
        for address in ('', '100.64.0.3', '192.168.1.2'):
            with self.subTest(address=address):
                result = self.guard(TEST_ADDRESS=address)
                if result.returncode == 0:
                    subprocess.run([self.app], env=self.env, check=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('not assigned to tailscale0', result.stderr)
                self.assertFalse((self.home / 'launches').exists())
        self.assertNotEqual(self.guard(TEST_IP_FAIL='1').returncode, 0)
        # A clean exit is real here; Restart=on-failure alone cannot retry it.
        result = subprocess.run([self.app], env=self.env)
        self.assertEqual(result.returncode, 0)
        self.assertEqual((self.home / 'launches').read_text(), 'launch\n')

    def test_malformed_relevant_config(self):
        variants = [
            '', VALID.replace('ipv4', 'both'), VALID.replace('100.64.0.2', '0.0.0.0'),
            VALID.replace('100.64.0.2', '100.064.0.2'),
            VALID + 'bind_address = 100.64.0.3\n',
            VALID + 'bind_address = 100.64.0.2\n',
            VALID + 'address_family = ipv4\n',
            VALID + 'address_family\n',
            VALID.replace('100.64.0.2\n', '100.64.0.2 # native trailing space\n'),
            VALID.replace('100.64.0.2\n', '100.64.0.2 \n'),
            'unknown = [\n' + VALID + ']\n',
        ]
        for text in variants:
            with self.subTest(config=text):
                self.conf.write_text(text)
                self.assertNotEqual(self.guard().returncode, 0)
                self.assertEqual(self.conf.read_text(), text)
        self.conf.write_text(VALID + 'unknown_setting = preserved\n')
        self.assertEqual(self.guard().returncode, 0)

    def test_missing_symlink_or_nonregular_config(self):
        self.conf.unlink()
        self.assertNotEqual(self.guard().returncode, 0)
        alternate = self.home / 'other.conf'
        alternate.write_text(VALID)
        self.conf.symlink_to(alternate)
        self.assertNotEqual(self.guard().returncode, 0)
        self.assertEqual(alternate.read_text(), VALID)
        self.conf.unlink()
        os.mkfifo(self.conf)
        self.assertNotEqual(self.guard().returncode, 0)

    def test_symlinked_home_and_xdg_parents_keep_same_source(self):
        for key, target in (('HOME', self.home), ('XDG_CONFIG_HOME', self.directory.parent)):
            with self.subTest(variable=key):
                link = Path(self.tmp.name) / (key + '-link')
                link.symlink_to(target, target_is_directory=True)
                self.assertEqual(self.guard(**{key: str(link)}).returncode, 0)

    def test_environment_drift_to_another_valid_config(self):
        other = self.home / 'other-root'
        (other / 'sunshine').mkdir(parents=True)
        (other / 'sunshine/sunshine.conf').write_text(VALID)
        for variable in ('XDG_CONFIG_HOME', 'CONFIGURATION_DIRECTORY'):
            with self.subTest(variable=variable):
                result = self.guard(**{variable: str(other)})
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('configuration directory changed:', result.stderr)
        self.assertNotEqual(self.guard(CONFIGURATION_DIRECTORY=f'{other}:/tmp').returncode, 0)
        self.assertNotEqual(self.guard(XDG_CONFIG_HOME='relative').returncode, 0)
        # CONFIGURATION_DIRECTORY wins, including when XDG points at another valid config.
        self.assertEqual(self.guard(CONFIGURATION_DIRECTORY=str(self.directory.parent),
                                    XDG_CONFIG_HOME=str(other)).returncode, 0)

    def model(self, *, managed=True, guarded=True, stop_at=None, graphical=False):
        """Discrete clock: vendor pre-sleep, real guard, policy retry. No systemd is run."""
        directives = dict(line.split('=', 1) for line in self.drop.splitlines() if '=' in line)
        self.assertEqual(directives['Restart'], 'on-failure')
        self.assertNotIn('ExecStart', directives)
        self.assertEqual(self.drop.count('ExecStartPre='), 1)  # append, never reset sleep
        interval = int(directives['StartLimitIntervalSec']) if managed else 500
        delay = int(directives['RestartSec'].removesuffix('s'))
        self.assertEqual(delay, 5)
        if graphical:
            self.assertEqual(directives['PartOf'], 'graphical-session.target')
        now, starts, checks, launches = 0, [], [], []
        while now < 700:
            if stop_at is not None and now + 5 >= stop_at:
                return checks, launches, 'stopped'
            if interval and len([t for t in starts if now - t < interval]) >= 5:
                return checks, launches, 'start-limit-hit'
            starts.append(now)
            now += 5  # retained vendor ExecStartPre=/bin/sleep 5
            checks.append(now)
            ready = '100.64.0.2' if now >= 600 else '100.64.0.3'
            status = self.guard(TEST_ADDRESS=ready).returncode if guarded else 0
            if status == 0:
                status = subprocess.run([self.app], env=self.env).returncode
                launches.append(now)
            if status == 0:
                return checks, launches, 'clean-exit'
            now += delay
        self.fail('model did not converge')

    def test_delayed_exact_ip_recovers_beyond_original_500_seconds(self):
        checks, launches, state = self.model()
        self.assertEqual(state, 'clean-exit')
        self.assertEqual(launches, [605])
        self.assertGreater(checks[-1], 500)
        self.assertTrue(all(b - a == 10 for a, b in zip(checks, checks[1:])))
        checks, launches, state = self.model(managed=False)
        self.assertEqual(state, 'start-limit-hit')
        self.assertEqual(len(checks), 5)
        self.assertEqual(launches, [])
        _, launches, state = self.model(guarded=False)
        self.assertEqual(state, 'clean-exit')
        self.assertEqual(launches, [5])  # removing only the limit cannot repair a clean exit

    def test_explicit_and_graphical_stop_end_modeled_retries(self):
        for graphical in (False, True):
            for stop_at in (7, 12, 117):
                with self.subTest(graphical=graphical, stop_at=stop_at):
                    checks, launches, state = self.model(stop_at=stop_at, graphical=graphical)
                    self.assertEqual(state, 'stopped')
                    self.assertEqual(launches, [])
                    self.assertTrue(all(t < stop_at for t in checks))

    def test_custom_selected_directory_is_a_literal_argument(self):
        root = self.home / 'chosen root %h $HOME'
        directory = root / 'sunshine'
        directory.mkdir(parents=True)
        (directory / 'sunshine.conf').write_text(VALID)
        drop = subprocess.check_output(
            ['/bin/bash', '-c', '. "$1/lib/common.sh"; qd_retry_content "$2"',
             'fixture', str(MODULE), str(directory)], env=self.env, text=True)
        pre = next(line.split('=', 1)[1] for line in drop.splitlines()
                   if line.startswith('ExecStartPre='))
        self.assertTrue(pre.startswith(':/usr/bin/python3 '))
        self.assertIn('%%h $HOME', pre)
        # Decode generated arguments only; ':' prevents systemd variable expansion.
        command = shlex.split(pre[1:].replace('%%', '%'))
        self.assertEqual(command[-1], str(directory))
        result = subprocess.run(command, env=dict(self.env, CONFIGURATION_DIRECTORY=str(root)),
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_static_resolved_parent_links_match_owned_dropin(self):
        analyzer = shutil.which('systemd-analyze', path='/usr/bin:/bin')
        if not analyzer:
            self.skipTest('systemd-analyze unavailable; static identity not verified')
        units = Path(self.tmp.name) / 'identity-units'
        units.mkdir()
        unit = units / UNIT
        unit.write_text('[Service]\nExecStartPre=/bin/sleep 5\nExecStart=/bin/true\n')
        for mode in ('plain', 'HOME', 'XDG_CONFIG_HOME'):
            with self.subTest(parent=mode):
                env = dict(self.env)
                if mode != 'plain':
                    link = Path(self.tmp.name) / (mode + '-link')
                    link.symlink_to(self.home if mode == 'HOME' else self.directory.parent,
                                    target_is_directory=True)
                    env[mode] = str(link)
                xdg = Path(env.get('XDG_CONFIG_HOME', str(Path(env['HOME']) / '.config')))
                directory = xdg / 'systemd/user' / (UNIT + '.d')
                drop = directory / 'quick-deploy-retry.conf'
                content = subprocess.check_output(
                    ['/bin/bash', '-c', '. "$1/lib/common.sh"; qd_retry_content "$2"',
                     'fixture', str(MODULE), str(self.directory)], env=env, text=True)
                drop.write_text(content)
                native = subprocess.run(
                    [analyzer, '--user', '--man=no', 'verify', str(unit)],
                    env=dict(env, SYSTEMD_UNIT_PATH=f'{units}:{xdg}/systemd/user:',
                             SYSTEMD_LOG_LEVEL='debug'), capture_output=True, text=True, timeout=20)
                output = native.stdout + native.stderr
                self.assertEqual(native.returncode, 0, output)
                paths = re.findall(r'DropIn Path: (.*)', output)
                self.assertEqual(paths, [str(drop.resolve())])
                self.assertEqual(str(drop) == paths[0], mode == 'plain')
                # Native loader resolves drop-in identity, not the command's argv.
                self.assertIn('Command Line: /usr/bin/python3 ' + str(directory / 'check-tailnet.py')
                              + ' ' + str(self.directory), output)
                script = '''. "$1/lib/common.sh"
qd_unit_property() {
 case "$2" in
 Restart) echo on-failure;; RestartUSec) echo 5s;; StartLimitIntervalUSec) echo 0;; PartOf) echo graphical-session.target;;
 ExecStartPre) printf '{ path=/bin/sleep ; argv[]=/bin/sleep 5 ; ignore_errors=no ; } ; { path=/usr/bin/python3 ; argv[]=/usr/bin/python3 %s/check-tailnet.py %s ; ignore_errors=no ; }\\n' "$(qd_retry_dir)" "$CONFIG_EXPECTED";;
 esac
}
qd_check_retry_files "$3" && qd_check_retry_effective "$QD_CANONICAL_UNIT" "$2" "$3"
'''
                def check(path):
                    return subprocess.run(['/bin/bash', '-c', script, 'fixture', str(MODULE),
                                           path, str(self.directory)],
                                          env=dict(env, CONFIG_EXPECTED=str(self.directory)),
                                          capture_output=True, text=True, timeout=10)
                self.assertEqual(check(paths[0]).returncode, 0)
                self.assertEqual(check(str(drop)).returncode, 0)
                # Canonical identity must not become any same-byte or same-inode file.
                foreign = units / 'quick-deploy-retry.conf'
                os.link(drop, foreign)
                self.assertNotEqual(check(str(foreign)).returncode, 0)
                foreign.unlink()
                alias = units / 'different-leaf.conf'
                alias.symlink_to(drop)
                self.assertNotEqual(check(str(alias)).returncode, 0)
                alias.unlink()
                self.assertNotEqual(check(paths[0] + ' ' + paths[0]).returncode, 0)

    def test_static_systemd_dropin_parsing(self):
        analyzer = shutil.which('systemd-analyze', path='/usr/bin:/bin')
        if not analyzer:
            self.skipTest('systemd-analyze unavailable; no real manager validation claimed')
        units = Path(self.tmp.name) / 'units'
        units.mkdir()
        # Native policy fixture; /bin/true replaces Sunshine only for static verification.
        unit = units / UNIT
        unit.write_text('''[Unit]
Description=Sunshine static fixture
StartLimitIntervalSec=500
StartLimitBurst=5
After=graphical-session.target xdg-desktop-autostart.target xdg-desktop-portal.service
[Service]
ExecStartPre=/bin/sleep 5
ExecStart=/bin/true
Restart=on-failure
RestartSec=5s
[Install]
WantedBy=graphical-session.target
Alias=sunshine.service
''')
        dropdir = units / (UNIT + '.d')
        dropdir.mkdir()
        (dropdir / 'quick-deploy-retry.conf').write_text(self.drop)
        result = subprocess.run([analyzer, '--user', '--man=no', 'verify', str(unit)],
                                env=dict(self.env, SYSTEMD_UNIT_PATH=str(units) + ':'),
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn('Unknown', result.stderr)
        self.assertNotIn('Failed to parse', result.stderr)


if __name__ == '__main__':
    unittest.main(verbosity=2)

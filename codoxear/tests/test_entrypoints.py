"""Shell entrypoint contracts; only copied scripts run, all effects stay in fixtures.

No real updater, dependency installer, shell rc, server, broker or agent is invoked.
The entrypoint suite itself needs only the Python standard library and bash.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


SOURCE = Path(__file__).resolve().parents[1]
ADAPTER_PYTHON = '''#!/usr/bin/env bash
set -eu
if [[ "$1" == -c ]]; then
  printf 'adapter-check:%s\\n' "$2" >> "$FIXTURE_LOG"
  if [[ "${FIXTURE_IMPORT_STATUS:-0}" != 0 ]]; then
    echo 'fixture: aiohttp unavailable' >&2
    exit "$FIXTURE_IMPORT_STATUS"
  fi
elif [[ "$1" == -m ]]; then
  printf 'adapter-pip:%s\\n' "$*" >> "$FIXTURE_LOG"
else
  printf 'adapter:%s\\n' "$*" >> "$FIXTURE_LOG"
  if [[ "${FIXTURE_ADAPTER_STATUS:-0}" != 0 ]]; then
    echo 'fixture: adapter startup failed' >&2
    exit "$FIXTURE_ADAPTER_STATUS"
  fi
fi
'''


class EntrypointTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='codoxear-entrypoints-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / 'fixture with spaces'
        self.internal = self.root / '.internal'
        self.internal.mkdir(parents=True)
        self.owner = self.root / 'owner-groups'
        self.owner.mkdir()
        self.home = self.root / 'home'
        self.home.mkdir()
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        self.log = self.root / 'calls'
        self.adapter = self.owner / '.venv/bin/python'
        for script in ('run_server.sh', 'setup.sh', 'owner-groups/setup.sh', 'owner-groups/requirements.txt'):
            shutil.copy2(SOURCE / script, self.root / script)
        # Restricted PATH cannot discover the real uv, pip, git or server.
        self.bash = shutil.which('bash')
        for command in ('bash', 'dirname', 'basename', 'cat'):
            (self.bin / command).symlink_to(shutil.which(command))
        self.env = {key: value for key, value in os.environ.items()
                    if key not in ('BASH_ENV', 'ENV') and not key.startswith(('FIXTURE_', 'CODOXEAR_'))}
        self.env.update(HOME=str(self.home), SHELL='/bin/bash', PATH=str(self.bin),
                        FIXTURE_LOG=str(self.log), FIXTURE_PYTHON=sys.executable)
        (self.root / '.env').write_text('CODEX_WEB_PASSWORD=synthetic-only\n')
        (self.internal / 'common.sh').write_text('''
ENV_FILE="${SCRIPT_DIR}/.env"
UPSTREAM_REPO_URL="fixture-default"
record() { printf '%s\\n' "$*" >> "$FIXTURE_LOG"; }
need_cmd() { record "need:$1"; }
validate_env() { record validate; return "${FIXTURE_VALIDATE_STATUS:-0}"; }
sync_remote_main() {
  record "sync:$UPSTREAM_REPO_URL"
  if [[ "${FIXTURE_SYNC_STATUS:-0}" != 0 ]]; then
    echo 'fixture: sync failed' >&2; return "$FIXTURE_SYNC_STATUS"
  fi
  echo fixture-revision
}
ensure_env_file() { record ensure-env; }
install_remote_main_if_needed() { record "install:$1"; return "${FIXTURE_INSTALL_STATUS:-0}"; }
python_launcher() { printf '%s\\n' "$FIXTURE_PYTHON"; }
''')
        (self.internal / 'launch.py').write_text('''import os, sys
with open(os.environ['FIXTURE_LOG'], 'a') as log:
    log.write('native:' + ' '.join(sys.argv[1:]) + '\\n')
''')
        (self.internal / 'update_shell_rc.py').write_text('''import json, os, sys
with open(os.environ['FIXTURE_LOG'], 'a') as log:
    log.write('shell-rc:' + json.dumps(sys.argv[1:]) + '\\n')
''')
        # Used only by the real, copied owner setup's no-uv branch.
        self.write_installer('python3')

    def write_executable(self, path, content):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        path.chmod(0o755)

    def install_adapter(self):
        self.write_executable(self.adapter, ADAPTER_PYTHON)

    def write_installer(self, name):
        self.write_executable(self.bin / name, f'''#!{sys.executable}
import json, os
from pathlib import Path
import sys
with open(os.environ['FIXTURE_LOG'], 'a') as log:
    log.write({name!r} + ':' + json.dumps(sys.argv[1:]) + '\\n')
args = sys.argv[1:]
if args[0] == 'venv' or args[:2] == ['-m', 'venv']:
    target = Path(args[-1]) / 'bin/python'
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text({ADAPTER_PYTHON!r})
    target.chmod(0o755)
elif args[:2] == ['pip', 'install']:
    if os.environ.get('FIXTURE_PIP_STATUS', '0') != '0':
        print('fixture: dependency install failed', file=sys.stderr)
        sys.exit(int(os.environ['FIXTURE_PIP_STATUS']))
else:
    raise RuntimeError('Unexpected installer invocation in fixture')
''')

    def run_script(self, script='run_server.sh', *args, **overrides):
        return subprocess.run([self.bash, str(self.root / script), *args], cwd=self.root,
                              env={**self.env, **overrides}, text=True, capture_output=True, timeout=10)

    def calls(self):
        return self.log.read_text().splitlines() if self.log.exists() else []

    def clear_calls(self):
        self.log.unlink(missing_ok=True)

    def update_calls(self):
        return ['validate', 'sync:fixture-default', 'install:fixture-revision',
                'native:describe fixture-revision']

    def test_default_and_legacy_flag_launch_managed_proxy_after_update_and_describe(self):
        self.install_adapter()
        for args in ((), ('--owner-groups',)):
            with self.subTest(args=args):
                self.clear_calls()
                result = self.run_script('run_server.sh', *args)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(self.calls(), ['adapter-check:import aiohttp', *self.update_calls(),
                    f'adapter:{self.owner}/proxy.py --managed-upstream'])

    def test_native_rollback_has_no_adapter_dependency_or_probe(self):
        for installed in (False, True):
            with self.subTest(adapter_installed=installed):
                if installed:
                    self.install_adapter()
                self.clear_calls()
                result = self.run_script('run_server.sh', '--no-owner-groups', FIXTURE_IMPORT_STATUS='9')
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(self.calls(), [*self.update_calls(), 'native:server'])

    def test_missing_adapter_fails_before_any_updater_effect(self):
        for args in ((), ('--owner-groups',)):
            with self.subTest(args=args):
                result = self.run_script('run_server.sh', *args)
                self.assertEqual(result.returncode, 1)
                self.assertIn('负责人分组依赖未安装', result.stderr)
                self.assertIn(str(self.root / 'setup.sh'), result.stderr)
                self.assertEqual(self.calls(), [])

    def test_broken_adapter_import_fails_explicitly_before_updater(self):
        self.install_adapter()
        for args in ((), ('--owner-groups',)):
            with self.subTest(args=args):
                self.clear_calls()
                result = self.run_script('run_server.sh', *args, FIXTURE_IMPORT_STATUS='9')
                self.assertEqual(result.returncode, 1)
                self.assertIn('负责人分组依赖不可用', result.stderr)
                self.assertIn(str(self.root / 'setup.sh'), result.stderr)
                self.assertEqual(self.calls(), ['adapter-check:import aiohttp'])

    def test_adapter_startup_failure_does_not_launch_native_fallback(self):
        self.install_adapter()
        result = self.run_script(FIXTURE_ADAPTER_STATUS='23')
        self.assertEqual(result.returncode, 23)
        self.assertIn('adapter startup failed', result.stderr)
        self.assertEqual(self.calls(), ['adapter-check:import aiohttp', *self.update_calls(),
            f'adapter:{self.owner}/proxy.py --managed-upstream'])

    def test_missing_env_fails_without_updater_in_both_modes(self):
        self.install_adapter()
        (self.root / '.env').unlink()
        for args in ((), ('--no-owner-groups',)):
            with self.subTest(args=args):
                self.clear_calls()
                result = self.run_script('run_server.sh', *args)
                self.assertEqual(result.returncode, 1)
                self.assertIn('找不到', result.stderr)
                self.assertEqual(self.calls(), [] if args else ['adapter-check:import aiohttp'])

    def test_validation_and_update_failures_stop_in_original_order(self):
        self.install_adapter()
        for variable, calls in (
            ('FIXTURE_VALIDATE_STATUS', ['validate']),
            ('FIXTURE_SYNC_STATUS', ['validate', 'sync:fixture-default']),
            ('FIXTURE_INSTALL_STATUS', ['validate', 'sync:fixture-default', 'install:fixture-revision']),
        ):
            for args in ((), ('--no-owner-groups',)):
                with self.subTest(variable=variable, args=args):
                    self.clear_calls()
                    result = self.run_script('run_server.sh', *args, **{variable: '7'})
                    self.assertEqual(result.returncode, 7, result.stderr)
                    self.assertEqual(self.calls(), ([] if args else ['adapter-check:import aiohttp']) + calls)

    def test_run_help_and_invalid_arguments_have_no_external_effects(self):
        for flag in ('--help', '-h'):
            result = self.run_script('run_server.sh', flag)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('默认启用', result.stdout)
            self.assertIn('--no-owner-groups', result.stdout)
            self.assertEqual(self.calls(), [])
        for args in (('--unknown',), ('--owner-groups', '--no-owner-groups'),
                     ('--no-owner-groups', '--owner-groups'), ('--owner-groups', 'extra'),
                     ('--help', 'extra'), ('',)):
            with self.subTest(args=args):
                result = self.run_script('run_server.sh', *args)
                self.assertEqual(result.returncode, 2)
                self.assertIn('error:', result.stderr)
                self.assertEqual(self.calls(), [])

    def test_normal_setup_installs_default_isolated_dependencies_without_starting_service(self):
        self.write_installer('uv')
        result = self.run_script('setup.sh')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.adapter.is_file())
        self.assertEqual(self.calls(), [
            'need:git', 'need:python3', 'sync:fixture-default', 'ensure-env', 'install:fixture-revision',
            'uv:' + json.dumps(['venv', '--allow-existing', '--python', 'python3', str(self.owner / '.venv')]),
            'uv:' + json.dumps(['pip', 'install', '--python', str(self.adapter), '-r', str(self.owner / 'requirements.txt')]),
            'shell-rc:' + json.dumps([str(self.home / '.bashrc'), sys.executable, str(self.internal / 'launch.py')]),
        ])
        self.assertIn(f'{self.owner}/../run_server.sh\n', result.stdout)
        self.assertIn('run_server.sh --no-owner-groups', result.stdout)
        self.assertIn('No service was started or restarted', result.stdout)
        self.assertFalse((self.home / '.bashrc').exists())  # rc updater was stubbed, too.

    def test_setup_without_uv_reuses_existing_venv_pip_path_and_repo_option(self):
        result = self.run_script('setup.sh', '--repo-url', 'https://fixture.invalid/upstream.git', SHELL='/bin/zsh')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls(), [
            'need:git', 'need:python3', 'sync:https://fixture.invalid/upstream.git', 'ensure-env', 'install:fixture-revision',
            'python3:' + json.dumps(['-m', 'venv', str(self.owner / '.venv')]),
            f'adapter-pip:-m pip install -r {self.owner}/requirements.txt',
            'shell-rc:' + json.dumps([str(self.home / '.zshrc'), sys.executable, str(self.internal / 'launch.py')]),
        ])

    def test_setup_dependency_failure_is_not_reported_ready_or_followed_by_rc_update(self):
        self.write_installer('uv')
        result = self.run_script('setup.sh', FIXTURE_PIP_STATUS='17')
        self.assertEqual(result.returncode, 17)
        self.assertIn('dependency install failed', result.stderr)
        self.assertNotIn('Ready.', result.stdout)
        self.assertEqual(len(self.calls()), 7)
        self.assertTrue(self.calls()[-1].startswith('uv:["pip", "install"'))
        self.assertFalse(any(call.startswith(('shell-rc:', 'native:', 'adapter:')) for call in self.calls()))

    def test_setup_help_and_invalid_arguments_have_no_external_effects(self):
        for flag in ('--help', '-h'):
            result = self.run_script('setup.sh', flag)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('默认负责人分组', result.stdout)
            self.assertEqual(self.calls(), [])
        for args in (('--unknown',), ('--repo-url',)):
            with self.subTest(args=args):
                result = self.run_script('setup.sh', *args)
                self.assertEqual(result.returncode, 2)
                self.assertIn('error:', result.stderr)
                self.assertEqual(self.calls(), [])


if __name__ == '__main__':
    unittest.main()

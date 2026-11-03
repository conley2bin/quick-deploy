"""Real CLI serialization with mocked attestation: no files, Store, or actors."""
from contextlib import ExitStack, nullcontext, redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from test_artifacts import context, io as handoff_io


def cli_stdout(argv, *, artifact=None, rejected=False):
    """Serialize fixture output through the real CLI; never open an operation/Store."""
    stdout, stderr = io.StringIO(), io.StringIO()
    sub = argv[0]
    with ExitStack() as stack:
        stack.enter_context(redirect_stdout(stdout))
        stack.enter_context(redirect_stderr(stderr))
        stack.enter_context(patch.object(handoff_io.succession, 'Adapter'))
        directory = stack.enter_context(patch.object(context, '_operation_dir', return_value=nullcontext(17)))
        if '--help' in argv or '-h' in argv:
            try:
                handoff_io.main(argv)
            except SystemExit as exc:
                if exc.code != 0:
                    raise
            directory.assert_not_called()
            return stdout.getvalue()
        if sub == 'check-owner':
            stack.enter_context(patch.object(handoff_io.succession, 'read', side_effect=[{},
                {'understanding_sha256': '0' * 64, 'review_sha256': '1' * 64}]))
            stack.enter_context(patch.object(handoff_io.succession, 'validate_ready',
                **({'side_effect': context.ContextError('rejected')} if rejected else
                   {'return_value': {'current_binding_valid': True}})))
        else:
            kind = next((word.split('=', 1)[1] for word in argv if word.startswith('--kind=')), None)
            if kind is None:
                kind = argv[argv.index('--kind') + 1]
            if artifact is None:
                if sub == 'attest':
                    artifact = {'kind': kind, 'accepted': True}
                else:
                    artifact = dict(version=1, operation_id='synthetic-operation', source_sha256='0' * 64,
                                    body='Synthetic report', _sha256='1' * 64)
                    if kind != 'understanding':
                        artifact['verdict'] = argv[argv.index('--verdict') + 1]
            stack.enter_context(patch.object(handoff_io, 'attest' if sub == 'attest' else 'submit',
                **({'side_effect': context.ContextError('rejected')} if rejected else {'return_value': artifact})))
        code = handoff_io.main(argv)
        if sub != 'check-owner':
            directory.assert_not_called()
    if code != (2 if rejected else 0):
        raise AssertionError(f'fixture CLI failed: {code}: {stderr.getvalue()}')
    return stderr.getvalue() if rejected else stdout.getvalue()


class AttestationCLIContractTests(unittest.TestCase):
    def test_attest_main_wraps_flat_identity_and_custody_returns(self):
        manifest = Path('/synthetic-operation/manifest.json')
        for kind in ('identity', 'custody'):
            with self.subTest(kind=kind):
                draft = manifest.parent / (kind + '-draft.json')
                artifact = {'kind': kind, 'accepted': True}
                stdout, stderr = io.StringIO(), io.StringIO()
                with patch.object(handoff_io, 'attest', return_value=artifact) as attest, \
                        patch.object(handoff_io, 'submit') as submit, \
                        patch.object(context, '_operation_dir') as operation_dir, \
                        patch.object(handoff_io.succession, 'Adapter') as adapter, \
                        redirect_stdout(stdout), redirect_stderr(stderr):
                    code = handoff_io.main(['attest', '--manifest', str(manifest),
                        '--kind', kind, '--body-file', str(draft)])
                self.assertEqual(code, 0)
                self.assertEqual(stderr.getvalue(), '')
                self.assertEqual(json.loads(stdout.getvalue()),
                    {'kind': kind, 'artifact': {'kind': kind, 'accepted': True}})
                self.assertEqual(artifact, {'kind': kind, 'accepted': True})
                attest.assert_called_once_with(manifest, kind, draft)
                submit.assert_not_called()
                operation_dir.assert_not_called()
                adapter.assert_not_called()

    def test_submit_and_check_owner_fixture_outputs_use_real_main(self):
        for kind, verdict in [('understanding', None), ('review', 'clear'), ('resolution', 'ready')]:
            argv = ['submit', '--manifest', '/synthetic-operation/manifest.json', '--kind', kind,
                    '--body-file', '/synthetic-operation/body.md']
            if verdict:
                argv += ['--verdict', verdict]
            output = cli_stdout(argv)
            record = json.loads(output)
            self.assertEqual(record['kind'], kind)
            self.assertEqual(record['artifact']['version'], 1)
            self.assertEqual(record['artifact'].get('verdict'), verdict)
            self.assertEqual(output, json.dumps(record, ensure_ascii=False, sort_keys=True) + '\n')
        self.assertEqual(cli_stdout(['check-owner', '--manifest', '/synthetic-operation/manifest.json']),
                         '{"current_binding_valid": true}\n')

    def test_rejected_attestation_has_no_success_stdout(self):
        manifest = Path('/synthetic-operation/manifest.json')
        for kind in ('identity', 'custody'):
            with self.subTest(kind=kind):
                draft = manifest.parent / (kind + '-draft.json')
                stdout, stderr = io.StringIO(), io.StringIO()
                with patch.object(handoff_io, 'attest', side_effect=context.ContextError('rejected')) as attest, \
                        patch.object(context, '_operation_dir') as operation_dir, \
                        patch.object(handoff_io.succession, 'Adapter') as adapter, \
                        redirect_stdout(stdout), redirect_stderr(stderr):
                    code = handoff_io.main(['attest', '--manifest', str(manifest),
                        '--kind', kind, '--body-file', str(draft)])
                self.assertEqual(code, 2)
                self.assertEqual(stdout.getvalue(), '')
                self.assertEqual(json.loads(stderr.getvalue())['error'], 'artifact_rejected')
                attest.assert_called_once_with(manifest, kind, draft)
                operation_dir.assert_not_called()
                adapter.assert_not_called()


if __name__ == '__main__':
    unittest.main()

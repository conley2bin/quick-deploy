"""Real CLI serialization with mocked attestation: no files, Store, or actors."""
from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from test_artifacts import context, io as handoff_io


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

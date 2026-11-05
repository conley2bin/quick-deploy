"""CLI result contracts for the generic handoff report submissions."""
from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from test_artifacts import context, io as handoff_io


class SubmitCLIContractTests(unittest.TestCase):
    def test_submit_serializes_each_report(self):
        for kind, verdict in [('understanding', None), ('review', 'clear'), ('resolution', 'ready')]:
            with self.subTest(kind=kind):
                argv = ['submit', '--manifest', '/operation/manifest.json', '--kind', kind,
                        '--body-file', '/operation/body.md']
                if verdict:
                    argv += ['--verdict', verdict]
                artifact = {'version': 1, 'body': 'synthetic'}
                stdout, stderr = io.StringIO(), io.StringIO()
                with patch.object(handoff_io, 'submit', return_value=artifact) as submit, \
                        redirect_stdout(stdout), redirect_stderr(stderr):
                    code = handoff_io.main(argv)
                self.assertEqual(code, 0)
                self.assertEqual(stderr.getvalue(), '')
                self.assertEqual(json.loads(stdout.getvalue()), {'kind': kind, 'artifact': artifact})
                submit.assert_called_once_with(Path('/operation/manifest.json'), kind,
                                               Path('/operation/body.md'), verdict=verdict, unreviewed=False)

    def test_rejected_submit_fails_without_success_output(self):
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.object(handoff_io, 'submit', side_effect=context.ContextError('bad report')), \
                redirect_stdout(stdout), redirect_stderr(stderr):
            code = handoff_io.main(['submit', '--manifest', '/operation/manifest.json',
                                    '--kind', 'understanding', '--body-file', '/operation/body.md'])
        self.assertEqual(code, 2)
        self.assertEqual(stdout.getvalue(), '')
        self.assertEqual(json.loads(stderr.getvalue())['error'], 'artifact_rejected')


if __name__ == '__main__':
    unittest.main()

"""Bounded observer contracts; no native actor/service or real credentials."""
import ast
import asyncio
import copy
import inspect
import json
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest

import owner_succession_native as native
from owner_observer_fixtures import HELPER, IDENTITY, ToolTrace, source_trace, target_trace
from owner_observer_vectors import (CHAIN_COMMAND, CHAIN_OUTPUT, CHECK_COMMAND,
                                    INSPECTION_COMMAND, PRECHECK_COMMAND, PRECHECK_OUTPUT)
from test_handoff_io_cli import cli_stdout


def check(trace):
    return native.assert_actor_tools(*trace.tuple(), target=True, identity=IDENTITY)


def wait_predicate(trace, project, baseline):
    """Exercise the actual nested _run predicate, including the old D4 behavior."""
    tree = ast.parse(inspect.getsource(native._run))
    action = copy.deepcopy(next(node for node in ast.walk(tree)
                                if isinstance(node, ast.AsyncFunctionDef) and node.name == 'action_done'))
    action.body = [node for node in action.body if not isinstance(node, ast.Nonlocal)]
    namespace = {**vars(native), 'operation': {'target_header': {'log_path': '/synthetic-session.jsonl'}},
                 'project': project, 'baseline': baseline, 'turn': None,
                 'transcript_calls': lambda path: trace.tuple()}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[action], type_ignores=[])),
                 '<actual action_done predicate>', 'exec'), namespace)
    return asyncio.run(namespace['action_done']())


def real_shell_trace(template, main_value):
    """Fresh local Bash oracle; reached/asserted markers live outside task files."""
    with tempfile.TemporaryDirectory(prefix='owner-observer-shell-') as temp:
        project = Path(temp) / 'project'
        project.mkdir()
        main = project / 'main.py'
        main.write_text('def result():\n    return "X"\n')
        baseline = native.project_fingerprint(project)
        main.write_text(f'def result():\n    return {main_value!r}\n')
        code = ('from pathlib import Path; Path("../python-ran").touch(); import main; '
                'assert main.result() == "Y"; Path("../asserted").touch()')
        command = template.format(python=shlex.quote(sys.executable), code=shlex.quote(code), rawcode=code)
        env = dict(HOME=temp, PATH='/usr/bin:/bin', GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL='/dev/null')
        if 'git diff' in command:
            subprocess.run(['git', '-c', 'init.defaultBranch=fixture', 'init', '-q'],
                           cwd=project, env=env, check=True)
        result = subprocess.run(['/bin/bash', '--noprofile', '--norc', '-c', command],
                                cwd=project, env=env, capture_output=True, text=True)
        if main_value == 'Y':
            native.assert_turn_goal(project, baseline)
        trace = target_trace(verify=False, ended=False)
        trace.verify(command, result.stdout + result.stderr)
        trace.results['verify']['isError'] = result.returncode != 0
        trace.stop()
        observation = dict(command=command, main=main_value, exit=result.returncode,
                           python_ran=(Path(temp) / 'python-ran').exists(),
                           asserted=(Path(temp) / 'asserted').exists(), output=result.stdout + result.stderr)
    return trace, observation


class ObserverContractsTests(unittest.TestCase):
    def test_t16_real_bash_assertion_reachability_oracle(self):
        py = '{python} -B -c {code}'
        heredoc = "set -e\n{python} -B - <<'PY'\n{rawcode}\nPY\n"
        # row, main, command, actual (rc, reached Python, completed assertion), accepted
        cases = [
            ('N1', 'Y', f"set -e; cd ./not-present && {py}; printf 'final diagnostic\\n'", (0, False, False), False),
            ('N2', 'Y', f'set -e; false && {py}; true', (0, False, False), False),
            ('N3', 'X', f'false && set -e; {py}; true', (0, True, False), False),
            ('N4', 'Y', f'set -e; exit 0; {py}', (0, False, False), False),
            ('N5', 'X', f"set -e; trap 'exit 0' ERR; {py}", (0, True, False), False),
            ('N6', 'Y', f"set -e; cd ./not-present &&\n{py}; printf 'final diagnostic\\n'", (0, False, False), False),
            ('N7', 'Y', f'true ||\n{py}', (0, False, False), False),
            ('N8', 'X', f'set -e; true && set +e; {py}; true', (0, True, False), False),
            ('N9', 'Y', f'set -n; {py}', (0, False, False), False),
            ('N10', 'X', f'{py}; printf done', (0, True, False), False),
            ('N11', 'Y', f'set -e\nif false; then\n{py}\nfi\ntrue', (0, False, False), False),
            ('N12', 'Y', f"eval 'exit 0'; {py}", (0, False, False), False),
            ('N13', 'Y', f'cd ./not-present && {py}', (1, False, False), False),
            ('P1', 'Y', heredoc + "printf 'diff\\n'", (0, True, True), True),
            ('P2', 'Y', f'cd . && {py}', (0, True, True), True),
            ('P3', 'Y', f'set -euo pipefail; {py}; printf done', (0, True, True), True),
            ('P4', 'Y', py, (0, True, True), True),
            ('P1-git-diff', 'Y', heredoc + 'git diff', (0, True, True), True),
        ]
        for row, main_value, command, measured, expected in cases:
            with self.subTest(row=row):
                trace, observed = real_shell_trace(command, main_value)
                self.assertEqual((observed['exit'], observed['python_ran'], observed['asserted']), measured)
                self.assertEqual(native.target_turn_state(trace.rows)['state'], 'ended')
                try:
                    proofs = check(trace)
                except AssertionError as exc:
                    self.assertIn('no recognized successful post-edit', str(exc))
                    proofs = []
                accepted = bool(proofs)
                print('T16 ' + json.dumps(dict(row=row, accepted=accepted, **observed), sort_keys=True))
                if accepted:
                    self.assertEqual(observed['exit'], 0)
                    self.assertTrue(observed['asserted'], 'accepted implies Python reached and asserted Y')
                self.assertEqual(accepted, expected)

    def test_rev3_literal_normal_form_and_continuations(self):
        for operator in ('&&', '||', '|', '|&'):
            with self.subTest(operator=operator):
                segments = native.shell_segments('echo guard ' + operator + '\n# comment\n\npython -B -c "pass"')
                self.assertEqual(segments[-1].before, operator)
        py = 'python -B -c \'import main; assert main.result() == "Y"\''
        for prefix in ('set -n', 'set -f', 'set --', 'set positional', 'set -o unknown',
                       'true && set -e', 'set -e && true', 'exit', 'exec', 'return', 'logout',
                       'break', 'continue', 'trap', 'eval', 'source', '.', 'builtin', 'command',
                       'alias', 'shopt', 'hash', 'enable'):
            with self.subTest(prefix=prefix):
                self.assertIsNone(native.assertion_errexit_states(native.shell_segments(prefix + '; ' + py)))
        for command in ('set -e; set +o errexit; ' + py,
                        'set -o errexit; set +e; ' + py):
            self.assertEqual(native.assertion_errexit_states(native.shell_segments(command)), [False, True, False])

    def test_t3_inspection_echo_comment_and_heredoc_are_not_executions(self):
        mentions = [INSPECTION_COMMAND.replace('@HELPER@', shlex.quote(str(HELPER))),
                    'echo "python ' + str(HELPER) + ' check-owner"',
                    '# python ' + str(HELPER) + ' check-owner',
                    "cat <<'EOF'\npython " + str(HELPER) + ' check-owner\nEOF']
        for command in mentions:
            with self.subTest(command=command):
                trace = target_trace()
                trace.tool('mention', command, 'not a helper result')
                check(trace)

    def test_t4_captured_compound_diagnostics_then_real_cli_record(self):
        trace = target_trace()
        trace.calls[4]['arguments']['command'] = CHECK_COMMAND.replace('@HELPER@', shlex.quote(str(HELPER)))
        trace.results['4']['content'][0]['text'] = (
            'actual own marker/header verified: synthetic-target-Pi\n'
            'ready resolution published and hash verified\n' +
            cli_stdout(['check-owner', '--manifest', '/synthetic-operation/manifest.json']))
        check(trace)

    def test_t6_repeated_fresh_check_in_postedit_verification_is_legitimate(self):
        trace = target_trace()
        verify = next(call for call in trace.calls if call['id'] == 'verify')
        verify['arguments']['command'] = trace.calls[4]['arguments']['command'] + ' && ' + verify['arguments']['command']
        trace.results['verify']['content'][0]['text'] = cli_stdout(
            ['check-owner', '--manifest', '/synthetic-operation/manifest.json'])
        check(trace)

    def test_t8_actual_wait_predicate_must_not_release_on_preedit_inspection(self):
        trace = target_trace(captured=True, verify=False, ended=False)
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp)
            main = project / 'main.py'
            main.write_text('def result():\n    return "X"\n')
            baseline = native.project_fingerprint(project)
            main.write_text('def result():\n    return "Y"\n')
            self.assertFalse(wait_predicate(trace, project, baseline),
                'pre-edit captured X inspection must not release the wait/cleanup barrier')

    def test_t1_real_cli_records_and_flat_return_boundary(self):
        for sub, kind, verdict in [('attest', 'identity', None), ('attest', 'custody', None),
                                   ('submit', 'understanding', None), ('submit', 'review', 'clear'),
                                   ('submit', 'resolution', 'ready'), ('check-owner', None, None)]:
            trace = ToolTrace()
            trace.helper('cli', sub, kind, verdict)
            record = json.loads(trace.results['cli']['content'][0]['text'])
            self.assertTrue(native.cli_record_ok(sub, kind, verdict, record))
            executions, mentions = native.helper_executions(*trace.tuple(), HELPER)
            self.assertEqual((executions[0]['status'], mentions), ('ok', 0))
        for kind in ('identity', 'custody'):
            self.assertFalse(native.cli_record_ok('attest', kind, None, {'kind': kind, 'accepted': True}))
        record = json.loads(cli_stdout(['submit', '--manifest', '/synthetic-operation/manifest.json',
            '--kind', 'review', '--body-file', '/synthetic-operation/body-draft.json', '--verdict', 'clear']))
        for field, value in [('version', 2), ('_sha256', 'not-a-hash'), ('verdict', None)]:
            broken = copy.deepcopy(record)
            broken['artifact'][field] = value
            self.assertFalse(native.cli_record_ok('submit', 'review', 'clear', broken))
        del record['artifact']['verdict']
        self.assertFalse(native.cli_record_ok('submit', 'review', None, record))

    def test_t2_actual_enveloped_trace_has_postedit_proof_and_pin(self):
        trace = target_trace()
        native.assert_helper_pinned(trace.rows, HELPER)
        self.assertEqual([call['id'] for call in check(trace)], ['verify'])

    def test_t5_records_fail_closed_including_malformed_next_to_valid(self):
        valid = cli_stdout(['check-owner', '--manifest', '/synthetic-operation/manifest.json'])
        rejected = cli_stdout(['check-owner', '--manifest', '/synthetic-operation/manifest.json'], rejected=True)
        cases = [('{"current_binding_valid": tru\n' + valid, 'malformed record'),
                 (valid + '{"current_binding_valid": tru\n', 'malformed record'),
                 ('diag' + valid, 'malformed record'),
                 ('{"current_binding_valid":true}\n', 'malformed record'),
                 ('{"current_binding_valid": true, "extra": 1}\n', 'malformed record'),
                 ('{"current_binding_valid": true, "current_binding_valid": true}\n', 'malformed record'),
                 (valid * 2, 'absent/ambiguous record'), ('diagnostic only\n', 'absent/ambiguous record'),
                 (rejected, 'rejected'), ('{"current_binding_valid": false}\n', 'malformed record'),
                 ('{"current_binding_valid": 1}\n', 'malformed record')]
        for output, status in cases:
            with self.subTest(output=output):
                trace = ToolTrace()
                trace.helper('check', 'check-owner')
                trace.results['check']['content'][0]['text'] = output
                executions, _ = native.helper_executions(*trace.tuple(), HELPER)
                self.assertEqual(executions[0]['status'], status)
        for missing, expected in [(False, 'returned an error'), (True, 'no actual tool result')]:
            trace = ToolTrace()
            trace.helper('check', 'check-owner')
            if missing:
                del trace.results['check']
            else:
                trace.results['check']['isError'] = True
            self.assertEqual(native.helper_executions(*trace.tuple(), HELPER)[0][0]['status'], expected)

    def test_t5_multiple_executions_pair_records_in_order(self):
        trace = ToolTrace()
        first = trace.helper('first', 'attest', 'identity')
        second = trace.helper('second', 'submit', 'understanding')
        first['arguments']['command'] += ' && ' + second['arguments']['command']
        trace.results['first']['content'][0]['text'] += trace.results['second']['content'][0]['text']
        executions, _ = native.helper_executions(trace.rows, [first], trace.results, HELPER)
        self.assertEqual([item['status'] for item in executions], ['ok', 'ok'])
        lines = trace.results['first']['content'][0]['text'].splitlines(keepends=True)
        trace.results['first']['content'][0]['text'] = ''.join(reversed(lines))
        executions, _ = native.helper_executions(trace.rows, [first], trace.results, HELPER)
        self.assertEqual([item['status'] for item in executions], ['malformed record', 'malformed record'])

    def test_t6_pre_ready_probe_is_ignored_but_later_failed_check_rejects(self):
        trace = target_trace(ended=False)
        probe = ToolTrace()
        probe.helper('probe', 'check-owner')
        probe.results['probe']['isError'] = True
        trace.rows[1:1] = probe.rows[1:]
        trace.calls.insert(0, probe.calls[0])
        trace.results.update(probe.results)
        check(trace)
        trace.helper('later', 'check-owner')
        trace.results['later']['isError'] = True
        with self.assertRaisesRegex(AssertionError, 'returned an error'):
            check(trace)

    def test_t6_only_postedit_check_cannot_authorize_edit(self):
        trace = target_trace()
        call_row = next(row for row in trace.rows if row.get('id') == 'call-4')
        result_row = next(row for row in trace.rows if row.get('message', {}).get('toolCallId') == '4')
        trace.rows.remove(call_row)
        trace.rows.remove(result_row)
        trace.rows[-1:-1] = [call_row, result_row]
        with self.assertRaisesRegex(AssertionError, 'toy edit preceded current-owner check'):
            check(trace)

    def test_t7_source_help_custody_and_review_use_real_cli(self):
        trace = source_trace()
        native.assert_helper_pinned(trace.rows, HELPER)
        native.assert_actor_tools(*trace.tuple(), target=False)
        for verdict in ('corrections', 'conflict'):
            other = ToolTrace()
            other.helper('custody', 'attest', 'custody')
            other.helper('review', 'submit', 'review', verdict)
            native.assert_actor_tools(*other.tuple(), target=False)

    def test_t8_captured_x_prints_remain_invalid_after_edit(self):
        for command, output in [(CHAIN_COMMAND, CHAIN_OUTPUT), (PRECHECK_COMMAND, PRECHECK_OUTPUT)]:
            with self.subTest(command=command):
                trace = target_trace(verify=False, ended=False)
                trace.verify(command, output)
                with self.assertRaisesRegex(AssertionError, 'no recognized successful post-edit'):
                    check(trace)

    def test_t9_supported_assert_and_observed_print_forms(self):
        examples = [
            ("python -B - <<'PY'\nimport main\nassert main.result() == 'Y'\nPY", ''),
            ('''python3 -B -c 'from main import result; assert result() == "Y"' ''', ''),
            ('''/synthetic-venv/bin/python3.12 -IB -W ignore -X utf8 -c 'import main as m; v = m.result(); assert "Y" == v, "goal"' ''', ''),
            ('''python -B -c 'from main import result as r; v = r(); assert v == "Y"' && git diff''', ''),
            ("set -e\npython -B <<'PY'\nimport main\nassert main.result() == 'Y'\nPY\ngit diff", ''),
            ('''set -o errexit; python -B -c 'import main; assert main.result() == "Y"'; git diff''', ''),
            ('''python -B -c 'import main; actual = main.result(); print("meets current goal Y =", actual == "Y")' ''', 'meets current goal Y = True\n'),
            ('''python -B -c 'import main; print(f"{main.result()=}")' ''', "main.result()='Y'\n"),
            ('''python -B -c 'import main as m; print("result:", repr(m.result()), str(m.result()))' ''', "result: 'Y' Y\n"),
            ('''command -v python3; python -B -c 'import main; print(main.result())' ''', 'Y\n'),
        ]
        for command, output in examples:
            with self.subTest(command=command):
                trace = target_trace(verify=False, ended=False)
                trace.verify(command, output)
                self.assertEqual([call['id'] for call in check(trace)], ['verify'])

    def test_t10_unsupported_or_masked_proof_never_counts(self):
        code = 'import main; assert main.result() == "Y"'
        examples = [f"python -BO -c '{code}'", f"PYTHONOPTIMIZE=1 python -B -c '{code}'",
                    f"python -B -c '{code}'; git status", f"python -B -c '{code}' || true",
                    f"false || python -B -c '{code}'", f"echo x | python -B -c '{code}'",
                    f"if true; then python -B -c '{code}'; fi", f"timeout 3 python -B -c '{code}'",
                    f"if false; then\npython -B -c '{code}'\nfi",
                    f"verify() {{\npython -B -c '{code}'\n}}",
                    f"{{\npython -B -c '{code}'\n}}",
                    "python -B -c 'import vendor; assert vendor.result() == \"Y\"'",
                    "python -B -c 'import main; assert main.result() != \"X\"'", 'python -B verify.py',
                    'echo "assert main.result() == \'Y\'"', "python -B -c 'print(\"Y\")'",
                    "python -B -c 'import main; main = None; assert main.result() == \"Y\"'",
                    "python -B -c 'from main import result; result = lambda: \"Y\"; assert result() == \"Y\"'",
                    "python -B -c 'import main; v = main.result(); v = \"Y\"; assert v == \"Y\"'",
                    "python -B -c 'import main; print(f\"{main.result():s}\")'",
                    '''python -B -c 'from main import result; print(f"value={result()!r}, goal={result() == chr(89)}")' ''',
                    f"set -e; set +e; python -B -c '{code}'; git status", f"python -c '{code}'",
                    'python -B -m verifier', 'python -B -c "invalid python: code"',
                    "python -B - <<'PY'\nimport main\nif True:\n    assert main.result() == 'Y'\nPY"]
        for command in examples:
            with self.subTest(command=command):
                trace = target_trace(verify=False, ended=False)
                trace.verify(command, 'Y\n')
                with self.assertRaisesRegex(AssertionError, 'no recognized successful post-edit'):
                    check(trace)
        trace = target_trace()
        trace.results['verify']['isError'] = True
        with self.assertRaisesRegex(AssertionError, 'no recognized successful post-edit'):
            check(trace)

    def test_t11_turn_states_retry_omission_and_fast_goal_failure(self):
        trace = ToolTrace()
        trace.stop(row_id='earlier-stop')
        trace.user('new continuation')
        self.assertEqual(native.target_turn_state(trace.rows)['state'], 'running')
        trace.stop('toolUse')
        self.assertEqual(native.target_turn_state(trace.rows)['state'], 'running')
        for reason in ('error', 'length', 'aborted'):
            trace.stop(reason, row_id='problem', errorMessage='e' * 300)
            state = native.target_turn_state(trace.rows)
            self.assertEqual((state['state'], state['stopReason'], state['row_id']), ('unresolved', reason, 'problem'))
            self.assertEqual(len(state['errorMessage']), 200)
            trace.rows.append(dict(type='context_edit', targetId='problem', replacement=None))
            self.assertEqual(native.target_turn_state(trace.rows)['state'], 'running')
        trace.stop()
        self.assertEqual(native.target_turn_state(trace.rows)['state'], 'ended')
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp)
            (project / 'main.py').write_text('def result():\n    return "X"\n')
            baseline = native.project_fingerprint(project)
            self.assertTrue(wait_predicate(trace, project, baseline))
            with self.assertRaisesRegex(AssertionError, 'target ended its turn without the intended main.py action'):
                native.assert_turn_goal(project, baseline)
            trace.rows.pop()
            trace.stop('error', row_id='retryable-error')
            self.assertFalse(wait_predicate(trace, project, baseline))
            trace.rows.append(dict(type='context_edit', targetId='retryable-error', replacement=None))
            self.assertFalse(wait_predicate(trace, project, baseline))

    def test_t13_helper_pin_and_unrecognized_paths_fail_closed(self):
        for script in ('/other/handoff_io.py', 'handoff_io.py'):
            for prefix in ('', 'cd /synthetic-operation && '):
                with self.subTest(script=script, prefix=prefix):
                    trace = target_trace()
                    trace.calls[1]['arguments']['command'] = prefix + 'python -B ' + script + ' attest --kind identity'
                    with self.assertRaisesRegex(AssertionError, r'missing/duplicate.*unrecognized mentions: 1'):
                        check(trace)
        for script in ('/other/handoff_io.py', 'handoff_io.py'):
            trace = target_trace()
            trace.calls[4]['arguments']['command'] = 'python -B ' + script + ' check-owner'
            with self.assertRaisesRegex(AssertionError, r'check-owner must follow.*unrecognized mentions: 1'):
                check(trace)
        trace = target_trace()
        for row in trace.rows:
            if row.get('message', {}).get('role') == 'user':
                row['message']['content'] = 'Use /different/handoff_io.py'
        with self.assertRaisesRegex(AssertionError, 'observer helper pin differs from the product prompt'):
            native.assert_helper_pinned(trace.rows, HELPER)
        trace = target_trace()
        trace.calls[1]['arguments']['command'] = "python -c 'import runpy; runpy.run_path(\"" + str(HELPER) + "\")'"
        with self.assertRaisesRegex(AssertionError, r'missing/duplicate.*unrecognized mentions: 1'):
            check(trace)

    def test_t14_delayed_or_parallel_call_is_not_postedit_verification(self):
        for mode in ('delayed', 'parallel', 'before-edit', 'result-before-call'):
            trace = target_trace()
            verify_row = next(row for row in trace.rows if row.get('id') == 'call-verify')
            edit_row = next(row for row in trace.rows if row.get('id') == 'call-edit')
            result_row = next(row for row in trace.rows if row.get('message', {}).get('toolCallId') == 'verify')
            trace.rows.remove(verify_row)
            if mode == 'parallel':
                edit_row['message']['content'].insert(0, verify_row['message']['content'][0])
            elif mode == 'result-before-call':
                trace.rows.insert(trace.rows.index(result_row) + 1, verify_row)
            else:
                trace.rows.insert(trace.rows.index(edit_row), verify_row)
                if mode == 'before-edit':
                    trace.rows.remove(result_row)
                    trace.rows.insert(trace.rows.index(edit_row), result_row)
            with self.subTest(mode=mode), self.assertRaisesRegex(AssertionError, 'no recognized successful post-edit'):
                check(trace)

    def test_t15_failed_edit_result_never_qualifies(self):
        trace = target_trace()
        trace.results['edit']['isError'] = True
        with self.assertRaisesRegex(AssertionError, 'no successful separately observed actor main.py edit'):
            check(trace)

    def test_shell_quotes_comments_redirections_and_unsupported_wrappers(self):
        command = ('FLAG=value python3 -B ' + shlex.quote(str(HELPER)) + ' check-owner 2>/dev/null >& 2 && '
                   'echo "quoted; && | text" # ignored ; python fake\n'
                   "cat <<-'EOF'\n\tpython " + str(HELPER) + ' check-owner\n\tEOF\n'
                   '(python ' + str(HELPER) + ' check-owner); echo $(python ' + str(HELPER) + ' check-owner)')
        segments = native.shell_segments(command)
        self.assertEqual(segments[0].assigns, {'FLAG': 'value'})
        self.assertEqual(segments[0].argv[-1], 'check-owner')
        self.assertEqual(segments[1].argv, ['echo', 'quoted; && | text'])
        self.assertIn('python ', segments[2].heredoc)
        self.assertEqual(sum(native.python_invocation(item) is not None for item in segments), 1)
        self.assertEqual(native.python_invocation(native.shell_segments('python\\\n3 -B -c "pass"')[0]).flags, {'B'})
        self.assertIsNone(native.python_invocation(native.shell_segments('env python -B -c "pass"')[0]))
        self.assertIsNone(native.shell_segments('python -B "unterminated')[0].argv)

    def test_helper_equals_options_and_quoted_pin(self):
        helper = Path('/synthetic helper/handoff_io.py')
        trace = ToolTrace()
        trace.user('Use ' + shlex.quote(str(helper)))
        trace.tool('equals', 'python3 -B ' + shlex.quote(str(helper)) +
                   ' attest --kind=identity --manifest /synthetic-operation/manifest.json --body-file /synthetic-operation/draft.json',
                   cli_stdout(['attest', '--manifest', '/synthetic-operation/manifest.json', '--kind=identity',
                               '--body-file', '/synthetic-operation/draft.json']))
        native.assert_helper_pinned(trace.rows, helper)
        self.assertEqual(native.helper_executions(*trace.tuple(), helper)[0][0]['status'], 'ok')

    def test_run_wiring_retains_turn_diagnostics_on_failure(self):
        tree = ast.parse(inspect.getsource(native._run))
        handler = next(node for node in ast.walk(tree) if isinstance(node, ast.ExceptHandler) and
                       any(isinstance(item, ast.If) and ast.unparse(item.test) == 'turn is not None' for item in node.body))
        body = copy.deepcopy(handler.body[:-1])  # The original re-raise is tested by the surrounding run contract.
        state = dict(state='unresolved', stopReason='error', errorMessage='retry status unknown', row_id='problem')
        namespace = dict(report={}, turn=state, exc=TimeoutError('existing deadline'))
        exec(compile(ast.fix_missing_locations(ast.Module(body=body, type_ignores=[])), '<actual failure report>', 'exec'), namespace)
        self.assertEqual(namespace['report']['target_turn'], state)
        self.assertEqual(namespace['report']['status'], 'failed')
        source = inspect.getsource(native._run)
        barrier = source.index('await watcher.until(action_done,')
        goal = source.index('assert_turn_goal(project, baseline)', barrier)
        self.assertLess(barrier, goal)
        self.assertLess(goal, source.index("after = store.current_snapshot()", goal))

    def test_unexpected_and_unclassified_helper_calls_are_not_ignored(self):
        trace = target_trace()
        trace.helper('custody', 'attest', 'custody')
        with self.assertRaisesRegex(AssertionError, 'unexpected helper execution'):
            check(trace)
        trace = ToolTrace()
        trace.tool('no-kind', 'python ' + shlex.quote(str(HELPER)) + ' attest',
                   cli_stdout(['attest', '--manifest', '/synthetic-operation/manifest.json', '--kind', 'identity',
                               '--body-file', '/synthetic-operation/body-draft.json']))
        self.assertEqual(native.helper_executions(*trace.tuple(), HELPER)[0][0]['status'], 'unclassified')

    def test_t12_captured_skeleton_fails_exactly_for_missing_verification(self):
        trace = target_trace(captured=True, verify=False)
        native.assert_helper_pinned(trace.rows, HELPER)
        self.assertEqual(native.target_turn_state(trace.rows)['state'], 'ended')
        with self.assertRaisesRegex(AssertionError, 'no recognized successful post-edit Python -B verification'):
            check(trace)
        trace.rows.pop()  # Genuine proof precedes the final normal stop.
        trace.verify()
        trace.stop()
        self.assertEqual(native.target_turn_state(trace.rows)['state'], 'ended')
        self.assertEqual([call['id'] for call in check(trace)], ['verify'])


if __name__ == '__main__':
    unittest.main()

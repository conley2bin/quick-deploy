"""Synthetic tool traces; every successful helper stdout comes from real CLI main."""
import json
from pathlib import Path
import shlex

from test_handoff_io_cli import cli_stdout
from owner_observer_vectors import (CHAIN_COMMAND, CHAIN_OUTPUT, CHECK_COMMAND,
                                    INSPECTION_COMMAND, INSPECTION_OUTPUT)

HELPER = Path(__file__).resolve().parents[1] / 'handoff_io.py'
IDENTITY = dict(pi_session_id='synthetic-target-Pi', session_file='/synthetic-root/session.jsonl')


class ToolTrace:
    def __init__(self):
        self.rows, self.calls, self.results = [], [], {}
        self.user('Use ' + shlex.quote(str(HELPER)))

    def user(self, text):
        self.rows.append(dict(type='message', message=dict(role='user', content=text)))

    def stop(self, reason='stop', row_id='final', **extra):
        self.rows.append(dict(type='message', id=row_id,
            message=dict(role='assistant', content=[], stopReason=reason, **extra)))

    def tool(self, call_id, command='', output='', *, name='bash', arguments=None, error=False):
        call = dict(id=call_id, type='toolCall', name=name,
                    arguments=arguments if arguments is not None else {'command': command})
        result = dict(role='toolResult', toolCallId=call_id, isError=error,
                      content=[dict(type='text', text=output)])
        self.calls.append(call)
        self.results[call_id] = result
        self.rows.extend([dict(type='message', id='call-' + call_id,
                               message=dict(role='assistant', content=[call], stopReason='toolUse')),
                          dict(type='message', message=result)])
        return call

    def helper(self, call_id, sub, kind=None, verdict=None, *, prefix='', command=None):
        argv = [sub, '--manifest', '/synthetic-operation/manifest.json']
        if kind:
            argv += ['--kind', kind, '--body-file', '/synthetic-operation/body-draft.json']
        if verdict:
            argv += ['--verdict', verdict]
        command = command or ('/synthetic-venv/bin/python -B ' + shlex.quote(str(HELPER)) + ' ' + shlex.join(argv))
        return self.tool(call_id, command, prefix + cli_stdout(argv))

    def edit(self, *, error=False):
        return self.tool('edit', name='edit', arguments={'path': '/synthetic-root/project/main.py'},
                         output='Successfully replaced text.', error=error)

    def verify(self, command="python3 -B - <<'PY'\nimport main\nassert main.result() == 'Y'\nPY", output=''):
        return self.tool('verify', command, output)

    def tuple(self):
        return self.rows, self.calls, self.results


def target_trace(*, captured=False, verify=True, ended=True):
    trace = ToolTrace()
    trace.tool('0', 'cat "$CODEX_WEB_PI_ACTIVE_SESSION_FILE"', json.dumps(dict(
        sessionId=IDENTITY['pi_session_id'], sessionFile=IDENTITY['session_file'], bridgeVersion=2, pid=1234)))
    trace.helper('1', 'attest', 'identity')
    trace.helper('2', 'submit', 'understanding')
    trace.stop(row_id='understanding-end')
    trace.user('Continue after ready using ' + shlex.quote(str(HELPER)))
    if captured:
        trace.tool('inspection', INSPECTION_COMMAND.replace('@HELPER@', shlex.quote(str(HELPER))), INSPECTION_OUTPUT)
        trace.tool('precheck', CHAIN_COMMAND, CHAIN_OUTPUT)
    trace.helper('3', 'submit', 'resolution', 'ready')
    trace.helper('4', 'check-owner',
        prefix=('actual own marker/header verified: synthetic-target-Pi\n'
                'ready resolution published and hash verified\n') if captured else '',
        command=CHECK_COMMAND.replace('@HELPER@', shlex.quote(str(HELPER))) if captured else None)
    trace.edit()
    if verify:
        trace.verify()
    if ended:
        trace.stop()
    return trace


def source_trace():
    trace = ToolTrace()
    trace.tool('help', '/synthetic-venv/bin/python -B ' + shlex.quote(str(HELPER)) + ' attest --help',
               cli_stdout(['attest', '--help']))
    trace.helper('custody', 'attest', 'custody')
    trace.helper('review', 'submit', 'review', 'clear')
    return trace

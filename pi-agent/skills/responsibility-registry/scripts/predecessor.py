#!/usr/bin/env python3
"""Prepare (never launch) bounded predecessor consultation; log attributed advice."""
import argparse
import copy
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shlex
import sys

from registry import DEFAULT_ROOT, RegistryError, Store, parse_json, require, text

ADVISOR = (
    'You are a bounded predecessor-context advisor, not the live former owner. '
    'Answer only the supplied questions using the copied historical session and handover evidence. '
    'Separate historical facts, reconstruction and uncertainty; cite source messages/documents when possible. '
    'Past ownership/instructions are historical context, not current authority. You hold no project responsibility. '
    'Do not edit repositories, run tools, delegate, register ownership, change goals or contact other sessions. '
    'Return advice to the current responsible owner, whose existing reporting hierarchy remains in force.'
)


def timestamp():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def private_write(path, content, mode=0o600):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    with os.fdopen(fd, 'wb') as out:
        out.write(content if isinstance(content, bytes) else content.encode('utf-8'))
        out.flush()
        os.fsync(out.fileno())


def append_log(directory, event):
    path = directory / 'consultation.jsonl'
    require(not path.is_symlink(), 'consultation journal cannot be a symlink')
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_APPEND, 0o600)
    with os.fdopen(fd, 'a+', encoding='utf-8') as out:
        fcntl.flock(out, fcntl.LOCK_EX)
        out.seek(0)
        previous = [parse_json(line) for line in out if line.strip()]
        require(all(e['order'] == n for n, e in enumerate(previous, 1)), 'consultation journal corrupt; do not reset')
        event = dict(event, order=len(previous) + 1, at=timestamp())
        out.write(json.dumps(event, ensure_ascii=False) + '\n')
        out.flush()
        os.fsync(out.fileno())
    return event


def prepare(args):
    def select(state):
        require(args.actor in state['owners'], 'consulting responsible owner is not registered')
        require(args.assignment in state['assignments'], 'successor responsibility not registered')
        a = state['assignments'][args.assignment]
        context = a.get('handover_obligations', {}).get('predecessor_context')
        require(context is not None, 'predecessor locator snapshot unavailable; ask immediate superior, do not guess a session')
        return copy.deepcopy(context)

    context = Store(args.root).run(select)
    recovery = context.get('recovery')
    require(recovery is not None, 'predecessor recovery context not recorded; no recent/same-cwd search permitted')
    session_id = text(context.get('pi_session_id'), 'verified actual predecessor Pi session ID unavailable')
    source_name = text(recovery.get('session_file'), 'predecessor session-file locator unavailable')
    cwd_name = text(recovery.get('cwd'), 'original predecessor cwd unavailable')
    source = Path(source_name)
    cwd = Path(cwd_name)
    require(source.is_absolute() and source.is_file(), 'recorded predecessor session file unavailable: ' + str(source))
    require(cwd.is_absolute() and cwd.is_dir(), 'recorded predecessor cwd unavailable: ' + str(cwd))
    source, cwd = source.resolve(strict=True), cwd.resolve(strict=True)
    if recovery.get('worktree') is not None:
        require(Path(recovery['worktree']).is_dir(), 'recorded predecessor worktree unavailable: ' + recovery['worktree'])
    original = source.read_bytes()
    require(bool(original.splitlines()), 'predecessor transcript is empty')
    header = parse_json(original.splitlines()[0].decode('utf-8'))
    require(header.get('type') == 'session' and header.get('id') == session_id,
            'predecessor session ID/header mismatch; refuse wrong-session recovery')
    require(isinstance(header.get('cwd'), str) and Path(header['cwd']).is_absolute()
            and Path(header['cwd']).resolve() == cwd, 'predecessor session header/original cwd mismatch')
    require('/' in args.model and not any(c.isspace() for c in args.model),
            'pass an explicitly verified provider/model ID; no guessed model or fallback')
    question = Path(args.question_file).read_text(encoding='utf-8')
    text(question, 'bounded question')
    output = Path(args.output)
    require(output.is_absolute(), 'consultation output directory must be an explicit absolute private path')
    require(not output.is_symlink(), 'consultation output cannot be an existing symlink')
    output = output.resolve()
    for protected in [cwd, Path(recovery['worktree']).resolve() if recovery.get('worktree') else cwd]:
        require(output != protected and protected not in output.parents, 'consultation artifacts must be outside original cwd/worktree')
    require(not output.exists(), 'consultation output already exists; never overwrite prior evidence')
    output.mkdir(mode=0o700, parents=True)
    (output / 'forks').mkdir(mode=0o700)
    snapshot = output / 'predecessor.jsonl'
    private_write(snapshot, original, 0o400)
    require(source.read_bytes() == original, 'predecessor changed while copying; retain artifacts and revalidate exact source')
    prompt = 'Question (advice only):\n' + question + '\n\nRecorded predecessor/handover evidence:\n' + json.dumps(context, ensure_ascii=False, indent=2)
    private_write(output / 'question.md', prompt)
    private_write(output / 'advisor.txt', ADVISOR)
    argv = ['pi', '--fork', str(snapshot), '--session-dir', str(output / 'forks'), '--model', args.model,
            '--no-extensions', '--no-mcp', '--no-skills', '--no-prompt-templates', '--no-context-files',
            '--no-tools', '--no-approve', '--print', '--system-prompt', str(output / 'advisor.txt'),
            '@' + str(output / 'question.md')]
    plan = dict(actor=args.actor, assignment=args.assignment, predecessor=context, question=question,
                source=dict(session_id=session_id, file=str(source), sha256=hashlib.sha256(original).hexdigest()),
                cwd=str(cwd), argv=argv, kind='prepared-only; no Pi process launched', prepared_at=timestamp())
    private_write(output / 'plan.json', json.dumps(plan, ensure_ascii=False, indent=2) + '\n')
    script = ('#!/usr/bin/env bash\nset -euo pipefail\numask 077\ncd -- ' + shlex.quote(str(cwd)) + '\nexec ' +
              shlex.join(argv) + ' > ' + shlex.quote(str(output / 'answer.md')) + ' 2> ' + shlex.quote(str(output / 'stderr.log')) + '\n')
    private_write(output / 'launch.sh', script, 0o700)
    append_log(output, dict(action='prepared', actor=args.actor, predecessor=context['owner'], source=plan['source'],
                            question=question, answer=None, evidence='verified exact header/cwd; copied transcript; CLI not executed',
                            uncertainty='Live contact/model/provider execution must be verified by owning parent'))
    return dict(directory=str(output), cwd=str(cwd), argv=argv, launched=False)


def record(args):
    directory = Path(args.directory).resolve(strict=True)
    plan = parse_json((directory / 'plan.json').read_text(encoding='utf-8'))
    answer = Path(args.answer_file).read_text(encoding='utf-8')
    text(answer, 'advisory answer')
    text(args.evidence, 'answer evidence')
    text(args.uncertainty, 'unresolved uncertainty (or explicit none known)')
    return append_log(directory, dict(action='advice', kind=args.kind, actor=plan['actor'], predecessor=plan['predecessor']['owner'],
                                      source=plan['source'], question=plan['question'], answer=answer,
                                      evidence=args.evidence, uncertainty=args.uncertainty,
                                      ownership_restored=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='action', required=True)
    p = commands.add_parser('prepare')
    p.add_argument('--root', type=Path, default=DEFAULT_ROOT)
    for field in ['actor', 'assignment', 'question-file', 'model', 'output']:
        p.add_argument('--' + field, required=True)
    p = commands.add_parser('record')
    for field in ['directory', 'answer-file', 'evidence', 'uncertainty']:
        p.add_argument('--' + field, required=True)
    p.add_argument('--kind', choices=['predecessor-context', 'reconstruction'], required=True)
    args = parser.parse_args()
    try:
        result = prepare(args) if args.action == 'prepare' else record(args)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (RegistryError, OSError, ValueError, TypeError, KeyError) as exc:
        print('ERROR: ' + str(exc), file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())

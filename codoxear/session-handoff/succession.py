"""Private handoff protocol and strict adapter for the ONE installed registry API.

No Store implementation, configurable runtime path, owner lookup, or graph writes.
The registry remains the authority for digest, transactions and current binding.
"""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import hashlib
import importlib.util
import inspect
import os
from pathlib import Path
import secrets
import sys

if __package__:
    from . import context
else:
    import context

SCHEMA = 'responsibility-session-succession/v1'
IMMUTABLE = ('version', 'operation_id', 'store_id', 'era', 'owner_id', 'source_pi_session_id')
PLAN = set(IMMUTABLE) | {'document_token', 'observation_revision'}
ENVELOPE = {'schema', 'status', 'operation_id', 'intent_digest', 'plan', 'receipt',
            'current_binding_valid', 'no_owner_valid', 'error'}
RECEIPT = {'version', 'operation_id', 'intent_digest', 'store_id', 'era', 'owner_id',
           'source_pi_session_id', 'target_pi_session_id', 'outcome', 'revision_before',
           'revision_after', 'transaction_id', 'event_id', 'document_token_after'}
ERROR = {'code', 'message', 'retryable', 'commit_state'}
STATUSES = {'prepared', 'committed', 'already_committed', 'not_applicable', 'superseded',
            'not_found', 'conflict', 'needs_recovery', 'unavailable', 'invalid_request'}


class GateError(context.ContextError):
    def __init__(self, code='succession_invalid'):
        self.code = code
        super().__init__('负责人接续未放行：' + code)


def require(ok, code='succession_invalid'):
    if not ok:
        raise GateError(code)


def keys(value, expected):
    require(isinstance(value, dict) and set(value) == set(expected))


def text(value):
    require(isinstance(value, str) and bool(value.strip()))


def hashed(value):
    require(isinstance(value, str) and context._HASH.fullmatch(value) is not None)


def immutable(plan):
    return {k: plan[k] for k in IMMUTABLE}


def validate_plan(plan, op):
    keys(plan, PLAN)
    require(type(plan['version']) is int and plan['version'] == 1 and plan['operation_id'] == op)
    for k in ('operation_id', 'store_id', 'era', 'source_pi_session_id', 'document_token'):
        text(plan[k])
    if plan['owner_id'] is not None:
        text(plan['owner_id'])
    require(type(plan['observation_revision']) is int and plan['observation_revision'] >= 0)


def validate_custody(custody, source):
    keys(custody, ('status', 'source_supervisor_pi_session_id', 'active_runs', 'evidence',
                   'successor_result_delivery', 'unknown_reason'))
    require(custody['status'] in ('unknown', 'none_verified', 'known_active'))
    require(custody['source_supervisor_pi_session_id'] == source)
    require(isinstance(custody['active_runs'], list))
    for item in custody['active_runs']:
        keys(item, ('run_id', 'custodian_pi_session_id', 'custodian_intercom_id', 'result_delivery'))
        for k in ('run_id', 'custodian_pi_session_id', 'result_delivery'):
            text(item[k])
        if item['custodian_intercom_id'] is not None:
            text(item['custodian_intercom_id'])
    text(custody['evidence'])
    text(custody['successor_result_delivery'])
    require(isinstance(custody['unknown_reason'], str))
    if custody['status'] == 'unknown':
        text(custody['unknown_reason'])
    else:
        require(custody['unknown_reason'] == '')
        require(bool(custody['active_runs']) == (custody['status'] == 'known_active'))


class Adapter:
    def __init__(self, service=None):
        self.service = service

    def load(self):
        if self.service is not None:
            return self.service
        path = Path.home() / '.pi/agent/skills/responsibility-registry/scripts/session_succession.py'
        try:
            spec = importlib.util.spec_from_file_location('_codoxear_registry_succession_v1', path)
            module = importlib.util.module_from_spec(spec)
            # The installed stdlib service may import its canonical sibling modules.
            sys.path.insert(0, str(path.parent))
            try:
                spec.loader.exec_module(module)
            finally:
                sys.path.remove(str(path.parent))
        except Exception:
            raise GateError('succession_api_unavailable') from None
        self.service = module
        return module

    def api(self):
        service = self.load()
        samples = {
            'prepare_owner_session_succession': (('op', 'source'), {}),
            'build_intent_digest': (({}, {}, {}, {}), {}),
            'commit_owner_session_succession': (({}, {}), dict(expected_document='token', authority={}, custody={})),
            'get_owner_session_succession': (('op', 'digest'), {}),
        }
        try:
            for name, (args, kwargs) in samples.items():
                function = getattr(service, name)
                require(callable(function), 'succession_api_invalid')
                inspect.signature(function).bind(*args, **kwargs)
        except (AttributeError, TypeError, ValueError):
            raise GateError('succession_api_invalid') from None
        return service

    def digest(self, plan, target, authority, custody):
        try:
            digest = self.api().build_intent_digest(plan, target, authority, custody)
            hashed(digest)
            return digest
        except GateError:
            raise
        except Exception:
            raise GateError('succession_digest_invalid') from None

    def call(self, name, op, *args, digest=None, **kwargs):
        try:
            value = getattr(self.api(), name)(*args, **kwargs)
        except GateError:
            raise
        except Exception:
            raise GateError('succession_outcome_unknown') from None
        keys(value, ENVELOPE)
        require(value['schema'] == SCHEMA and value['operation_id'] == op)
        status = value['status']
        require(status in STATUSES)
        require(type(value['current_binding_valid']) is bool and type(value['no_owner_valid']) is bool)
        require(value['current_binding_valid'] == (status in ('committed', 'already_committed')))
        require(value['no_owner_valid'] == (status == 'not_applicable'))
        require(value['intent_digest'] == digest)
        require((value['plan'] is not None) == (status == 'prepared'))
        if status == 'prepared':
            validate_plan(value['plan'], op)
        if value['receipt'] is not None:
            receipt = value['receipt']
            keys(receipt, RECEIPT)
            require(type(receipt['version']) is int and receipt['version'] == 1)
            require(receipt['operation_id'] == op and receipt['intent_digest'] == digest)
            for k in ('store_id', 'era', 'source_pi_session_id', 'target_pi_session_id', 'transaction_id', 'document_token_after'):
                text(receipt[k])
            require(receipt['outcome'] in ('committed', 'not_applicable'))
            for k in ('revision_before', 'revision_after'):
                require(type(receipt[k]) is int and receipt[k] >= 0)
            if receipt['outcome'] == 'not_applicable':
                require(receipt['owner_id'] is None and receipt['event_id'] is None and receipt['revision_before'] == receipt['revision_after'])
            else:
                text(receipt['owner_id'])
                text(receipt['event_id'])
                require(receipt['revision_after'] > receipt['revision_before'])
        require(value['receipt'] is not None or status not in ('committed', 'already_committed', 'not_applicable', 'superseded'))
        if status in ('committed', 'already_committed', 'not_applicable'):
            require(value['receipt']['outcome'] == ('not_applicable' if status == 'not_applicable' else 'committed'))
        if status in ('conflict', 'needs_recovery', 'unavailable', 'invalid_request'):
            keys(value['error'], ERROR)
            text(value['error']['code'])
            text(value['error']['message'])
            require(type(value['error']['retryable']) is bool)
            require(value['error']['commit_state'] in ('not_committed', 'committed', 'unknown'))
        else:
            require(value['error'] is None)
        allowed = {'prepare_owner_session_succession': STATUSES - {'committed', 'already_committed', 'not_applicable', 'not_found', 'superseded'},
                   'commit_owner_session_succession': STATUSES - {'prepared', 'not_found'},
                   'get_owner_session_succession': STATUSES - {'prepared', 'committed'}}
        require(status in allowed[name])
        return value

    def prepare(self, op, source):
        result = self.call('prepare_owner_session_succession', op, op, source)
        require(result['status'] == 'prepared', 'succession_prepare_blocked')
        require(result['plan']['source_pi_session_id'] == source)
        return result['plan']

    def get(self, op, digest):
        return self.call('get_owner_session_succession', op, op, digest, digest=digest)


def read(directory_fd, name, *, missing=False):
    raw = context._read_at(directory_fd, name, context.MAX_MANIFEST_BYTES, missing=missing)
    return None if raw is None else context._json(raw)


def publish(directory_fd, name, value):
    context._publish_at(directory_fd, name, context._encode(value))


def freeze(directory_fd, name, value):
    old = read(directory_fd, name, missing=True)
    if old is None:
        publish(directory_fd, name, value)
    else:
        require(old == value, 'succession_frozen_input_changed')


@contextmanager
def operation_lock(directory_fd):
    fd = os.open('succession.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600, dir_fd=directory_fd)
    try:
        info = context._regular(fd, private=True)
        require(info.st_nlink == 1)
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def prepare_source(directory, op, source_pi, adapter):
    plan = adapter.prepare(op, source_pi)
    value = dict(version=1, operation_id=op, plan=plan, challenge=secrets.token_hex(32),
                 authority=dict(issuer='user', reason='本次会话接力自动接续同一稳定负责人身份',
                    evidence='已认证同源 start 按钮的本操作用户同意；功能范围仅轮换源会话的既有 owner identity/recovery，不任命或转交 assignment；operation=' + op))
    with context._operation_dir(directory) as fd:
        publish(fd, 'succession-source.json', value)
    return dict(version=1, source_sha256=hashlib.sha256(context._encode(value)).hexdigest(),
                challenge_sha256=hashlib.sha256(value['challenge'].encode()).hexdigest())


def check_source(fd, manifest):
    marker = manifest['succession']
    keys(marker, ('version', 'source_sha256', 'challenge_sha256'))
    require(marker['version'] == 1)
    source = read(fd, 'succession-source.json')
    require(hashlib.sha256(context._encode(source)).hexdigest() == marker['source_sha256'])
    require(source['operation_id'] == manifest['operation_id'])
    require(hashlib.sha256(source['challenge'].encode()).hexdigest() == marker['challenge_sha256'])
    validate_plan(source['plan'], manifest['operation_id'])
    require(source['plan']['source_pi_session_id'] == manifest['source']['pi_session_id'])
    return source


def header(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = context._regular(fd)
        raw = os.read(fd, 65537)
        require(b'\n' in raw)
        line = raw.split(b'\n', 1)[0] + b'\n'
        require(len(line) <= 65536)
        value = context._json(line)
        require(value.get('type') == 'session')
        text(value.get('id'))
        return value['id'], hashlib.sha256(line).hexdigest(), info.st_dev, info.st_ino
    finally:
        os.close(fd)


def bind_target(directory, manifest, row, identity):
    pi, sha, dev, ino = header(identity['log_path'])
    require(pi == identity['pi_session_id'] and (dev, ino) == (identity['device'], identity['inode']))
    launch = {k: row.get(k) for k in ('launch_id', 'broker_pid', 'spawn_nonce')}
    require(bool(launch['launch_id']) or type(launch['broker_pid']) is int)
    proof = dict(version=1, operation_id=manifest['operation_id'], pi_session_id=pi,
                 session_file=identity['log_path'], cwd=row['cwd'], broker_session_id=row['session_id'],
                 launch_binding=context._encode(launch).decode().strip(), session_header_sha256=sha,
                 device=dev, inode=ino, challenge_sha256=manifest['succession']['challenge_sha256'])
    with context._operation_dir(directory) as fd:
        check_source(fd, manifest)
        publish(fd, 'succession-target.json', proof)


def check_target(fd, manifest, *, report=None):
    source = check_source(fd, manifest)
    proof = read(fd, 'succession-target.json')
    if report is None:
        report = read(fd, 'succession-identity.json')
    keys(report, ('version', 'operation_id', 'challenge', 'pi_session_id', 'session_file',
                  'intercom_id', 'contact_source', 'unknown_reason', 'source'))
    require(type(report['version']) is int and report['version'] == 1)
    require(proof['operation_id'] == report['operation_id'] == manifest['operation_id'])
    require(report['challenge'] == source['challenge'])
    require(proof['challenge_sha256'] == manifest['succession']['challenge_sha256'])
    require(report['source'] == 'current-session')
    require(report['pi_session_id'] == proof['pi_session_id'] != manifest['source']['pi_session_id'])
    require(report['session_file'] == proof['session_file'])
    pi, sha, dev, ino = header(proof['session_file'])
    require((pi, sha, dev, ino) == (proof['pi_session_id'], proof['session_header_sha256'], proof['device'], proof['inode']), 'succession_target_changed')
    require(isinstance(report['unknown_reason'], str))
    if report['intercom_id'] is None:
        require(report['contact_source'] == 'unavailable')
        text(report['unknown_reason'])
    else:
        text(report['intercom_id'])
        require(report['contact_source'] == 'target-intercom-status')
    return source, proof, report


def unknown_custody(source):
    return dict(status='unknown', source_supervisor_pi_session_id=source, active_runs=[],
                evidence='显式未经旧会话审阅继续，未得到实际子代理保管证据',
                successor_result_delivery='旧会话仍保管其实际子代理；结果转送尚未核实，不得假设已 reparent',
                unknown_reason='未得到旧会话实际运行清单/无子代理证明')


def freeze_intent(directory, manifest, understanding_sha256, review_sha256, adapter):
    with context._operation_dir(directory) as fd:
        source, proof, report = check_target(fd, manifest)
        if review_sha256 == 'unreviewed':
            custody = unknown_custody(manifest['source']['pi_session_id'])
        else:
            custody_report = read(fd, 'succession-custody.json')
            require(custody_report['operation_id'] == manifest['operation_id'])
            require(custody_report['understanding_sha256'] == understanding_sha256)
            custody = custody_report['custody']
        validate_custody(custody, manifest['source']['pi_session_id'])
        target = dict(identity=dict(pi_session_id=proof['pi_session_id'], intercom_id=report['intercom_id'],
                        source='current-session', unknown_reason=report['unknown_reason']),
            recovery=dict(session_file=proof['session_file'], cwd=proof['cwd'], repo=None, worktree=None,
                summary='本次接力的独立理解与校对见固定私有 operation 报告', decisions=None, pending_issues=None,
                context_refs=[str(directory / 'manifest.json'), str(directory / 'understanding.json')],
                source='codoxear-bound-target/header+challenged-report-v1',
                unknown_reason='repo/worktree/decisions/pending_issues 未独立核实；不得从 cwd 推测'),
            evidence=dict(operation_id=manifest['operation_id'], challenge_sha256=proof['challenge_sha256'],
                broker_session_id=proof['broker_session_id'], launch_binding=proof['launch_binding'],
                session_header_sha256=proof['session_header_sha256'],
                verification_source='codoxear-bound-target/header+challenged-report-v1', contact_source=report['contact_source']))
        digest = adapter.digest(source['plan'], target, source['authority'], custody)
        intent = dict(version=1, operation_id=manifest['operation_id'], plan=source['plan'], target=target,
                      authority=source['authority'], custody=custody, intent_digest=digest,
                      source_sha256=manifest['source_sha256'], understanding_sha256=understanding_sha256,
                      review_sha256=review_sha256)
        freeze(fd, 'succession-intent.json', intent)
        return intent


def validate_intent(fd, manifest, understanding_sha256, review_sha256, adapter):
    source, proof, report = check_target(fd, manifest)
    intent = read(fd, 'succession-intent.json')
    keys(intent, ('version', 'operation_id', 'plan', 'target', 'authority', 'custody', 'intent_digest',
                  'source_sha256', 'understanding_sha256', 'review_sha256'))
    require(intent['version'] == 1 and intent['operation_id'] == manifest['operation_id'])
    require(intent['source_sha256'] == manifest['source_sha256'])
    require(intent['understanding_sha256'] == understanding_sha256 and intent['review_sha256'] == review_sha256)
    require(intent['plan'] == source['plan'] and intent['authority'] == source['authority'])
    require(intent['target']['identity']['pi_session_id'] == proof['pi_session_id'])
    require(intent['target']['identity']['intercom_id'] == report['intercom_id'])
    require(intent['target']['recovery']['session_file'] == proof['session_file'])
    require(intent['target']['evidence'] == dict(operation_id=manifest['operation_id'],
        challenge_sha256=proof['challenge_sha256'], broker_session_id=proof['broker_session_id'],
        launch_binding=proof['launch_binding'], session_header_sha256=proof['session_header_sha256'],
        verification_source='codoxear-bound-target/header+challenged-report-v1', contact_source=report['contact_source']))
    validate_custody(intent['custody'], manifest['source']['pi_session_id'])
    require(adapter.digest(intent['plan'], intent['target'], intent['authority'], intent['custody']) == intent['intent_digest'], 'succession_digest_changed')
    return intent


def permission(result, intent):
    if result['status'] in ('committed', 'already_committed'):
        require(result['current_binding_valid'])
    elif result['status'] == 'not_applicable':
        require(result['no_owner_valid'])
    else:
        raise GateError('succession_' + result['status'])
    receipt = result['receipt']
    for k in ('operation_id', 'store_id', 'era', 'owner_id', 'source_pi_session_id'):
        require(receipt[k] == intent['plan'][k])
    require(receipt['target_pi_session_id'] == intent['target']['identity']['pi_session_id'])
    require(receipt['intent_digest'] == intent['intent_digest'])


def gate(fd, manifest, understanding_sha256, review_sha256, adapter):
    intent = validate_intent(fd, manifest, understanding_sha256, review_sha256, adapter)
    op, digest = intent['operation_id'], intent['intent_digest']
    # A durable attempt marker precedes EVERY potentially effectful service call.
    # After an unknown response, not_found alone cannot prove no effects.
    attempted = read(fd, 'succession-attempt.json', missing=True)
    if attempted is not None:
        require(attempted == dict(operation_id=op, intent_digest=digest))
        result = adapter.get(op, digest)
        permission(result, intent)
    else:
        fresh = adapter.prepare(op, intent['plan']['source_pi_session_id'])
        require(immutable(fresh) == immutable(intent['plan']), 'succession_source_changed')
        publish(fd, 'succession-attempt.json', dict(operation_id=op, intent_digest=digest))
        for attempt in range(2):
            result = adapter.call('commit_owner_session_succession', op, fresh, intent['target'],
                expected_document=fresh['document_token'], authority=intent['authority'], custody=intent['custody'], digest=digest)
            error = result['error']
            retryable = (result['status'] == 'conflict' and error['code'] == 'document_conflict'
                         and error['retryable'] and error['commit_state'] == 'not_committed')
            if not retryable or attempt == 1:
                break
            fresh = adapter.prepare(op, intent['plan']['source_pi_session_id'])
            require(immutable(fresh) == immutable(intent['plan']), 'succession_source_changed')
        permission(result, intent)
    # Store only the service envelope, never raw challenge or private graph.
    # Immutable initial receipt remains evidence; recovery always calls fresh get.
    freeze(fd, 'succession-receipt.json', read(fd, 'succession-receipt.json', missing=True) or result)
    return result


def validate_ready(directory, manifest, understanding_sha256, review_sha256, adapter):
    with context._operation_dir(directory) as fd:
        intent = validate_intent(fd, manifest, understanding_sha256, review_sha256, adapter)
        stored = read(fd, 'succession-receipt.json')
        permission(stored, intent)
        result = adapter.get(intent['operation_id'], intent['intent_digest'])
        permission(result, intent)
        return result

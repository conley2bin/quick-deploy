#!/usr/bin/env python3
"""Versioned, stdlib-only same-owner carrier succession over the Store protocol.

Load this file by absolute path with importlib.util; neither cwd nor sys.path is
part of the interface. This service never discovers sessions or executes recovery.
"""
import builtins
import copy
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import sys
import threading
import types
import uuid

SCHEMA = 'responsibility-session-succession/v1'
_SPLIT_FORMAT = 'responsibility-registry/v3'
_PLAN_FIELDS = ('version', 'operation_id', 'store_id', 'era', 'owner_id',
                'source_pi_session_id', 'document_token', 'observation_revision')
_IMMUTABLE_PLAN = _PLAN_FIELDS[:6]
_HEX = re.compile(r'[0-9a-f]{64}\Z')
_SCRIPTS = Path(__file__).resolve().parent
_PACKAGE = '_responsibility_succession_' + hashlib.sha256(str(_SCRIPTS).encode()).hexdigest()


def _sibling(name):
    """Load trusted installed siblings in a path-specific private namespace.

    Existing registry siblings use absolute imports. A module-local import hook
    resolves only actual siblings, without changing sys.path, global __import__,
    or generic sys.modules entries. Their stdlib imports retain normal behavior.
    """
    package = sys.modules.get(_PACKAGE)
    if package is None:
        candidate = types.ModuleType(_PACKAGE)
        candidate.__path__ = [str(_SCRIPTS)]
        candidate._load_lock = threading.RLock()
        package = sys.modules.setdefault(_PACKAGE, candidate)
    with package._load_lock:
        fullname = _PACKAGE + '.' + name
        if fullname in sys.modules:
            return sys.modules[fullname]
        spec = importlib.util.spec_from_file_location(fullname, _SCRIPTS / (name + '.py'))
        module = importlib.util.module_from_spec(spec)

        def private_import(import_name, globals=None, locals=None, fromlist=(), level=0):
            if (level in (0, 1) and import_name.isidentifier()
                    and (_SCRIPTS / (import_name + '.py')).is_file()):
                return _sibling(import_name)
            return builtins.__import__(import_name, globals, locals, fromlist, level)

        module.__dict__['__builtins__'] = dict(vars(builtins), __import__=private_import)
        sys.modules[fullname] = module
        try:
            # Installed updates can preserve timestamp/size. Execute fresh source
            # bytes rather than accepting a timestamp-valid stale .pyc sibling.
            source_path = _SCRIPTS / (name + '.py')
            exec(compile(source_path.read_bytes(), str(source_path), 'exec', dont_inherit=True), module.__dict__)
        except BaseException:
            sys.modules.pop(fullname, None)
            raise
        setattr(package, name, module)
        return module


_codec = _sibling('registry_document')


class SuccessionValidationError(ValueError):
    """Typed invalid-input error from build_intent_digest (which returns a str)."""
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.commit_state = 'not_committed'
        self.retryable = False


class _Fault(Exception):
    def __init__(self, status, code, message, *, retryable=False, commit_state='not_committed'):
        super().__init__(message)
        self.status, self.code = status, code
        self.retryable, self.commit_state = retryable, commit_state


def _check(condition, code, message):
    if not condition:
        raise SuccessionValidationError(code, message)


def _fields(value, fields, code):
    _check(type(value) is dict and set(value) == set(fields), code, 'Object has invalid fields.')


def _text(value, code, label):
    _check(isinstance(value, str) and bool(value.strip()), code, label + ' must be nonempty text.')
    try:
        value.encode('utf-8')
    except UnicodeError as exc:
        raise SuccessionValidationError(code, label + ' must be UTF-8 text.') from exc


def _uuid(value, code='invalid_operation_id'):
    try:
        valid = isinstance(value, str) and str(uuid.UUID(value)) == value
    except (ValueError, AttributeError):
        valid = False
    _check(valid, code, 'Canonical lowercase hyphenated UUID required.')


def _json(value, code):
    # JSON inputs have string object keys, finite numbers and no Python extensions.
    def validate(item):
        if type(item) is dict:
            _check(all(type(k) is str for k in item), code, 'JSON object keys must be strings.')
            for child in item.values():
                validate(child)
        elif type(item) is list:
            for child in item:
                validate(child)
        else:
            _check(item is None or type(item) in (str, int, float, bool), code, 'Strict JSON required.')
    try:
        encoded = json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)
        validate(value)
        encoded.encode('utf-8')
        return encoded
    except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
        raise SuccessionValidationError(code, 'Strict finite UTF-8 JSON required.') from exc


def _plan(plan):
    code = 'invalid_plan'
    _json(plan, code)
    _fields(plan, _PLAN_FIELDS, code)
    _check(type(plan['version']) is int and plan['version'] == 1, code, 'Unsupported plan version.')
    _uuid(plan['operation_id'])
    for field in ('store_id', 'source_pi_session_id', 'document_token'):
        _text(plan[field], code, field)
    if plan['era'] != _codec.TEMPLATE_ERA:
        _uuid(plan['era'], code)
    if plan['owner_id'] is not None:
        _text(plan['owner_id'], code, 'owner_id')
        _check(plan['owner_id'] != 'user' and not plan['owner_id'].startswith('owner:'), code,
               'owner_id must be a bare stable owner ID.')
    _check(type(plan['observation_revision']) is int and plan['observation_revision'] >= 0,
           code, 'Invalid observation revision.')


def _target(target, plan):
    code = 'invalid_target'
    _json(target, code)
    _fields(target, ('identity', 'recovery', 'evidence'), code)
    try:
        _codec.identity(target['identity'])
        _codec.recovery_context(target['recovery'])
    except (ValueError, TypeError, KeyError, _codec.RegistryError) as exc:
        raise SuccessionValidationError(code, 'Invalid canonical target identity or recovery.') from exc
    _check(target['identity']['source'] == 'current-session', code, 'Verified actual target Pi identity required.')
    evidence = target['evidence']
    _fields(evidence, ('operation_id', 'challenge_sha256', 'broker_session_id', 'launch_binding',
                       'session_header_sha256', 'verification_source', 'contact_source'), code)
    _check(evidence['operation_id'] == plan['operation_id'], code, 'Target evidence operation mismatch.')
    for field in ('challenge_sha256', 'session_header_sha256'):
        _check(isinstance(evidence[field], str) and _HEX.fullmatch(evidence[field]) is not None,
               code, 'Lowercase SHA256 evidence required.')
    for field in ('broker_session_id', 'launch_binding'):
        _text(evidence[field], code, field)
    _check(evidence['verification_source'] == 'codoxear-bound-target/header+challenged-report-v1',
           code, 'Invalid target verification source.')
    contact_source = 'unavailable' if target['identity']['intercom_id'] is None else 'target-intercom-status'
    _check(evidence['contact_source'] == contact_source, code, 'Contact provenance mismatch.')


def _authority(authority, plan):
    code = 'invalid_authority'
    _json(authority, code)
    _fields(authority, ('issuer', 'reason', 'evidence'), code)
    for field in authority:
        _text(authority[field], code, field)
    permitted = ['user'] + (['owner:' + plan['owner_id']] if plan['owner_id'] is not None else [])
    _check(authority['issuer'] in permitted, code, 'Only the source owner or authorized user may attest succession.')


def _custody(custody, plan):
    code = 'invalid_custody'
    _json(custody, code)
    _fields(custody, ('status', 'source_supervisor_pi_session_id', 'active_runs', 'evidence',
                      'successor_result_delivery', 'unknown_reason'), code)
    _check(custody['status'] in ('none_verified', 'known_active', 'unknown'), code, 'Invalid custody status.')
    _check(custody['source_supervisor_pi_session_id'] == plan['source_pi_session_id'], code,
           'Custody must describe the actual source supervisor.')
    for field in ('evidence', 'successor_result_delivery'):
        _text(custody[field], code, field)
    _check(isinstance(custody['unknown_reason'], str), code, 'Custody unknown_reason must be text.')
    if custody['status'] == 'unknown':
        _text(custody['unknown_reason'], code, 'unknown_reason')
    else:
        _check(custody['unknown_reason'] == '', code, 'Known custody cannot contain an unknown reason.')
    runs = custody['active_runs']
    _check(type(runs) is list, code, 'active_runs must be an explicit list.')
    if custody['status'] == 'none_verified':
        _check(not runs, code, 'Verified no runs requires an empty run list.')
    if custody['status'] == 'known_active':
        _check(bool(runs), code, 'Known active custody requires actual runs.')
    seen = set()
    for run in runs:
        _fields(run, ('run_id', 'custodian_pi_session_id', 'custodian_intercom_id', 'result_delivery'), code)
        for field in ('run_id', 'custodian_pi_session_id', 'result_delivery'):
            _text(run[field], code, field)
        if run['custodian_intercom_id'] is not None:
            _text(run['custodian_intercom_id'], code, 'custodian_intercom_id')
        _check(run['run_id'] not in seen, code, 'Duplicate custody run ID.')
        seen.add(run['run_id'])


def _intent(plan, target, authority, custody):
    _plan(plan)
    _target(target, plan)
    _authority(authority, plan)
    _custody(custody, plan)
    return copy.deepcopy(dict(version=1, plan={k: plan[k] for k in _IMMUTABLE_PLAN},
                              target=target, authority=authority, custody=custody))


def build_intent_digest(plan: dict, target: dict, authority: dict, custody: dict) -> str:
    """Hash validated frozen intent; invalid inputs raise SuccessionValidationError."""
    intent = _intent(plan, target, authority, custody)
    try:
        return hashlib.sha256(_json(intent, 'invalid_target').encode('utf-8')).hexdigest()
    except UnicodeError as exc:
        raise SuccessionValidationError('invalid_target', 'Strict UTF-8 JSON required.') from exc


def _envelope(operation_id, status, *, digest=None, plan=None, receipt=None, error=None,
              binding=False, no_owner=False):
    return dict(schema=SCHEMA, status=status, operation_id=operation_id, intent_digest=digest,
                plan=plan, receipt=receipt, current_binding_valid=binding, no_owner_valid=no_owner,
                error=error)


def _error(operation_id, exc, digest=None):
    if isinstance(exc, SuccessionValidationError):
        status = 'invalid_request'
    elif isinstance(exc, _Fault):
        status = exc.status
    else:
        # Only the documented typed Store error protocol is normalized. Programmer
        # exceptions propagate; callers must query the same op, never assume no effect.
        code = getattr(exc, 'code', None)
        status = {
            'document_conflict': 'conflict', 'era_changed': 'conflict',
            'operation_intent_mismatch': 'conflict', 'transaction_pending': 'needs_recovery',
            'commit_unknown': 'needs_recovery', 'audit_unavailable': 'unavailable',
            'audit_damaged': 'unavailable', 'registry_unavailable': 'unavailable',
            'registry_invalid': 'unavailable', 'storage_not_migrated': 'unavailable',
            'codec_incompatible': 'unavailable',
        }.get(code)
        if (status is None or getattr(exc, 'commit_state', None) not in ('not_committed', 'committed', 'unknown')
                or type(getattr(exc, 'retryable', None)) is not bool):
            raise exc
    message = str(exc) if isinstance(exc, (SuccessionValidationError, _Fault)) else 'Registry transaction could not be confirmed: ' + exc.code + '.'
    return _envelope(operation_id, status, digest=digest,
                     error=dict(code=exc.code, message=message, retryable=exc.retryable,
                                commit_state=exc.commit_state))


def _store(store):
    if store is None:
        store = _sibling('registry').Store()
    if not all(callable(getattr(store, method, None)) for method in
               ('current_snapshot', 'operation_status', 'transact_current')):
        raise _Fault('unavailable', 'codec_incompatible', 'Installed Store lacks the current transaction protocol.')
    return store


def _snapshot(snapshot, *, commit_state='not_committed'):
    try:
        _fields(snapshot, ('state', 'era', 'document_token', 'format', 'audit_head', 'store_id'), 'invalid_plan')
        _codec.validate_current_envelope(snapshot['state'], snapshot['era'])
        for field in ('document_token', 'store_id'):
            _text(snapshot[field], 'invalid_plan', field)
        if snapshot['audit_head'] is not None:
            _uuid(snapshot['audit_head'], 'invalid_plan')
        _check(snapshot['format'] in (_SPLIT_FORMAT, 'responsibility-registry/v2'), 'invalid_plan', 'Invalid storage format.')
    except (ValueError, TypeError, KeyError, _codec.RegistryError) as exc:
        raise _Fault('unavailable', 'registry_invalid', 'Full current registry is invalid or unavailable.',
                     commit_state=commit_state) from exc
    return snapshot


def _claims(state, pi):
    return [oid for oid, owner in state['owners'].items() if owner['identity']['pi_session_id'] == pi]


def _source_owner(state, source):
    owners = _claims(state, source)
    if len(owners) > 1:
        raise _Fault('conflict', 'source_identity_conflict', 'Multiple owners claim the actual source Pi identity.')
    return owners[0] if owners else None


def _policy(state, era, plan, target):
    _codec.validate_current_envelope(state, era)
    if era != plan['era']:
        raise _Fault('conflict', 'era_changed', 'Registry era changed; the frozen source plan is no longer valid.')
    oid = _source_owner(state, plan['source_pi_session_id'])
    expected = plan['owner_id']
    if expected is None and oid is not None:
        raise _Fault('conflict', 'source_registration_changed', 'Source became registered after the no-owner plan.')
    if expected is not None:
        if expected not in state['owners']:
            raise _Fault('conflict', 'source_owner_missing', 'The frozen source owner is absent.')
        if oid is not None and oid != expected:
            raise _Fault('conflict', 'source_owner_changed', 'Actual source now belongs to another owner.')
        if state['owners'][expected]['identity']['pi_session_id'] != plan['source_pi_session_id']:
            raise _Fault('conflict', 'source_identity_conflict', 'The source owner no longer has the frozen Pi identity.')
    target_pi = target['identity']['pi_session_id']
    if target_pi == plan['source_pi_session_id']:
        raise _Fault('conflict', 'source_identity_conflict', 'A new operation must name a distinct target Pi identity.')
    if any(other != expected for other in _claims(state, target_pi)):
        raise _Fault('conflict', 'target_identity_occupied', 'Target Pi is currently claimed by another owner.')


def prepare_owner_session_succession(operation_id: str, source_pi_session_id: str, *, store=None) -> dict:
    """Read the complete current Store; never register, repair or hydrate history."""
    try:
        _uuid(operation_id)
        _text(source_pi_session_id, 'invalid_plan', 'source_pi_session_id')
        snapshot = _snapshot(_store(store).current_snapshot())
        oid = _source_owner(snapshot['state'], source_pi_session_id)
        plan = dict(version=1, operation_id=operation_id, store_id=snapshot['store_id'], era=snapshot['era'],
                    owner_id=oid, source_pi_session_id=source_pi_session_id,
                    document_token=snapshot['document_token'], observation_revision=snapshot['state']['revision'])
        return _envelope(operation_id, 'prepared', plan=plan)
    except Exception as exc:
        return _error(operation_id if isinstance(operation_id, str) else None, exc)


def _binding(snapshot, intent):
    plan, target = intent['plan'], intent['target']
    if snapshot['store_id'] != plan['store_id'] or snapshot['era'] != plan['era']:
        return False
    state = snapshot['state']
    if plan['owner_id'] is None:
        return not _claims(state, plan['source_pi_session_id']) and not _claims(state, target['identity']['pi_session_id'])
    owner = state['owners'].get(plan['owner_id'])
    target_pi = target['identity']['pi_session_id']
    # Frozen metadata proves the original intent, not ongoing carrier identity.
    return (owner is not None and owner['identity']['source'] == 'current-session'
            and owner['identity']['pi_session_id'] == target_pi
            and _claims(state, target_pi) == [plan['owner_id']])


def _receipt(record, operation_id, digest):
    prepare, outcome = record['prepare'], record['outcome']
    intent = prepare['intent']
    plan = intent['plan']
    event = prepare['event']
    kind = 'committed' if outcome['status'] == 'COMMITTED' else 'not_applicable'
    # Only Store's publication/recovery observation belongs in this historical
    # receipt. A later current read qualifies the flags, not an invented old token.
    token = outcome.get('document_token_after')
    return dict(version=1, operation_id=operation_id, intent_digest=digest, store_id=plan['store_id'],
                era=plan['era'], owner_id=plan['owner_id'], source_pi_session_id=plan['source_pi_session_id'],
                target_pi_session_id=intent['target']['identity']['pi_session_id'], outcome=kind,
                revision_before=prepare['base']['revision'], revision_after=prepare['after']['revision'],
                transaction_id=record['transaction_id'], event_id=event['id'] if event else None,
                document_token_after=token)


def _record(record, operation_id, digest):
    """Reject incompatible/non-service receipts before interpreting their authority."""
    committed = (isinstance(record, dict) and isinstance(record.get('outcome'), dict)
                 and record['outcome'].get('status') in ('COMMITTED', 'NOT_APPLICABLE'))
    try:
        _fields(record, ('transaction_id', 'prepare', 'outcome', 'result', 'current', 'pending'), 'invalid_plan')
        _uuid(record['transaction_id'], 'invalid_plan')
        _check(type(record['pending']) is bool, 'invalid_plan', 'Invalid pending flag.')
        prepare, outcome = record['prepare'], record['outcome']
        if prepare is not None:
            _check(type(prepare) is dict, 'invalid_plan', 'Invalid PREPARE.')
            intent = prepare['intent']
            _fields(intent, ('version', 'plan', 'target', 'authority', 'custody'), 'invalid_plan')
            _check(type(intent['version']) is int and intent['version'] == 1, 'invalid_plan', 'Invalid intent version.')
            _fields(intent['plan'], _IMMUTABLE_PLAN, 'invalid_plan')
            observed_plan = dict(intent['plan'], document_token=prepare['base']['document_token'],
                                 observation_revision=prepare['base']['revision'])
            calculated = build_intent_digest(observed_plan, intent['target'], intent['authority'], intent['custody'])
            _check(intent['plan']['operation_id'] == operation_id and calculated == digest
                   and prepare['intent_digest'] == digest, 'invalid_plan', 'Receipt intent mismatch.')
        if outcome is not None:
            _check(type(outcome) is dict and prepare is not None, 'invalid_plan', 'Outcome lacks PREPARE.')
            if committed:
                before, after = prepare['base']['revision'], prepare['after']['revision']
                noop = outcome['status'] == 'NOT_APPLICABLE'
                _check(type(after) is int and after == before + (0 if noop else 1), 'invalid_plan', 'Invalid receipt revisions.')
                _check((intent['plan']['owner_id'] is None) == noop, 'invalid_plan', 'Receipt owner/outcome mismatch.')
                event = prepare['event']
                _check(event is None if noop else type(event) is dict, 'invalid_plan', 'Receipt event mismatch.')
                if event is not None:
                    _text(event['id'], 'invalid_plan', 'event_id')
                token = outcome.get('document_token_after')
                if token is not None:
                    _text(token, 'invalid_plan', 'document_token_after')
    except (ValueError, TypeError, KeyError) as exc:
        raise _Fault('unavailable', 'audit_damaged', 'Stored transaction is incompatible or its service intent is invalid.',
                     commit_state='committed' if committed else 'unknown') from exc
    return record


def _present(record, operation_id, digest, *, new=False):
    """Separate historical disposition from fresh authority; pending never grants ready."""
    _record(record, operation_id, digest)
    outcome = record['outcome']
    terminal = outcome['status'] if outcome else None
    known = terminal in ('COMMITTED', 'NOT_APPLICABLE')
    commit_state = 'committed' if known else 'unknown'
    if record['pending']:
        raise _Fault('needs_recovery', 'transaction_pending', 'Transaction metadata needs explicit registry recovery.',
                     commit_state=commit_state)
    if record['prepare'] is None and outcome is None:
        return _envelope(operation_id, 'not_found', digest=digest)
    if terminal is None or terminal == 'PUBLICATION_UNKNOWN':
        raise _Fault('needs_recovery', 'commit_unknown', 'Registry publication is not definitively known.', commit_state='unknown')
    if terminal == 'ABORTED':
        raise _Fault('conflict', 'document_conflict', 'This transaction was definitively aborted; it cannot be replayed.')
    if not known:
        raise _Fault('unavailable', 'audit_damaged', 'Unrecognized durable transaction outcome.', commit_state='unknown')
    if record['current'] is None:
        raise _Fault('unavailable', 'registry_invalid', 'Current registry cannot qualify the historical receipt.', commit_state='committed')
    snapshot = _snapshot(record['current'], commit_state='committed')
    receipt = _receipt(record, operation_id, digest)
    valid = _binding(snapshot, record['prepare']['intent'])
    if not valid:
        return _envelope(operation_id, 'superseded', digest=digest, receipt=receipt)
    noop = terminal == 'NOT_APPLICABLE'
    status = 'not_applicable' if noop else ('committed' if new else 'already_committed')
    return _envelope(operation_id, status, digest=digest, receipt=receipt, binding=not noop, no_owner=noop)


def commit_owner_session_succession(plan: dict, target: dict, *, expected_document: str,
                                    authority: dict, custody: dict, store=None) -> dict:
    """Commit one identity/recovery patch, with receipt lookup before source/CAS checks."""
    operation_id = plan.get('operation_id') if isinstance(plan, dict) else None
    digest = None
    try:
        intent = _intent(plan, target, authority, custody)
        digest = build_intent_digest(plan, target, authority, custody)
        _text(expected_document, 'invalid_plan', 'expected_document')
        store = _store(store)
        prior = store.operation_status(operation_id, digest)
        if prior['prepare'] is not None or prior['outcome'] is not None or prior['pending']:
            return _present(prior, operation_id, digest)
        snapshot = _snapshot(prior['current'])
        if snapshot['store_id'] != plan['store_id']:
            raise _Fault('conflict', 'store_mismatch', 'Frozen plan belongs to another configured Store.')
        if snapshot['format'] != _SPLIT_FORMAT:
            raise _Fault('unavailable', 'storage_not_migrated', 'Succession requires explicitly migrated current storage.')

        callback_called = False

        def change(current, era):
            nonlocal callback_called
            callback_called = True
            _policy(current, era, intent['plan'], intent['target'])
            oid = intent['plan']['owner_id']
            if oid is None:
                return {'outcome': 'not_applicable'}
            # No start-time record snapshot is copied back. The callback receives
            # the latest locked graph, and only these three fields may change.
            current['owners'][oid].update(identity=copy.deepcopy(intent['target']['identity']),
                                           recovery=copy.deepcopy(intent['target']['recovery']),
                                           updated_at=datetime.now(timezone.utc).isoformat(timespec='seconds'))
            return {'outcome': 'committed'}

        actor = 'user' if authority['issuer'] == 'user' else plan['owner_id']
        record = store.transact_current(operation_id, digest, intent, expected_document, change,
                                        action='session-succession', actor=actor, reason=authority['reason'],
                                        authority=authority['evidence'], persist_noop=plan['owner_id'] is None)
        # Another caller may have committed the same op between our read-only
        # status check and the locked transaction's own receipt lookup.
        return _present(record, operation_id, digest, new=callback_called)
    except Exception as exc:
        return _error(operation_id if isinstance(operation_id, str) else None, exc, digest)


def get_owner_session_succession(operation_id: str, intent_digest: str, *, store=None) -> dict:
    """Strictly read-only receipt lookup plus fresh current-binding qualification."""
    try:
        _uuid(operation_id)
        _check(isinstance(intent_digest, str) and _HEX.fullmatch(intent_digest) is not None,
               'invalid_digest', 'Canonical lowercase SHA256 intent digest required.')
        record = _store(store).operation_status(operation_id, intent_digest)
        return _present(record, operation_id, intent_digest)
    except Exception as exc:
        return _error(operation_id if isinstance(operation_id, str) else None, exc, intent_digest if isinstance(intent_digest, str) else None)

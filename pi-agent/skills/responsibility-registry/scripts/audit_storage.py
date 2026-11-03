"""Private immutable transaction evidence. Callers hold the stable registry lock.

Only explicit history/checkpoint operations walk the previous chain. Ordinary IO
is direct-addressed and bounded by one pending and one latest terminal attempt.
"""
from datetime import datetime
import hashlib
import json
import os
import re
from pathlib import Path
import stat
import tempfile
import uuid

if __package__:
    from .registry_document import RegistryError, parse_json, require, keys, text, validate_event, validate_time
else:
    from registry_document import RegistryError, parse_json, require, keys, text, validate_event, validate_time


class AuditDamaged(RegistryError):
    pass


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False) + '\n').encode('utf-8')


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def identifier(value):
    try:
        require(isinstance(value, str) and str(uuid.UUID(value)) == value, 'noncanonical audit UUID')
    except (ValueError, TypeError, AttributeError) as exc:
        raise AuditDamaged('invalid audit UUID') from exc
    return value


def sync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def private_directory(path, create=False):
    path = Path(path)
    if create and not path.exists() and not path.is_symlink():
        path.mkdir(mode=0o700)
        sync_directory(path.parent)
    info = path.lstat()
    require(stat.S_ISDIR(info.st_mode) and not stat.S_ISLNK(info.st_mode), 'private directory is not a real directory')
    require(stat.S_IMODE(info.st_mode) == 0o700, 'private directory permissions must be 0700')


def read_private(path):
    """No-follow regular private file read; absence differs from corruption."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as source:
        info = os.fstat(source.fileno())
        require(stat.S_ISREG(info.st_mode), 'private audit object is not regular')
        require(stat.S_IMODE(info.st_mode) == 0o600, 'private audit file permissions must be 0600')
        return source.read()


class AuditStorage:
    def __init__(self, root, checkpoint=None):
        self.root = Path(root)
        self.path = self.root / 'audit'
        self.ops = self.path / 'ops'
        self.legacy = self.path / 'legacy'
        self.checkpoints = self.path / 'checkpoints'
        self.pending_path = self.path / 'pending.json'
        self.head_path = self.path / 'head.json'
        self.checkpoint = checkpoint or (lambda phase: None)

    def directories(self, create=False):
        private_directory(self.root)
        for path in (self.path, self.ops, self.legacy, self.checkpoints):
            private_directory(path, create)

    def exists(self):
        if self.path.is_symlink():
            raise AuditDamaged('audit directory cannot be symlink')
        return self.path.exists()

    def read(self, path, optional=False):
        try:
            raw = read_private(path)
            value = parse_json(raw.decode('utf-8'))
            require(isinstance(value, dict), 'audit object must be an object')
            # Canonical bytes prevent alternate encodings from hiding a hash conflict.
            require(encoded(value) == raw, 'noncanonical/corrupt audit object')
            return value
        except FileNotFoundError:
            if optional:
                return None
            raise AuditDamaged('missing referenced audit object: ' + path.name)
        except (OSError, ValueError, TypeError, RegistryError) as exc:
            raise AuditDamaged('audit evidence damaged: ' + path.name + ': ' + str(exc)) from exc

    def publish(self, path, raw, *, immutable, phase):
        """A final immutable name only ever exposes a complete fsynced object."""
        if path.is_symlink():
            raise AuditDamaged('audit destination cannot be symlink')
        if immutable:
            try:
                existing = read_private(path)
            except FileNotFoundError:
                pass
            else:
                require(existing == raw, 'immutable audit object conflicts; preserve evidence')
                sync_directory(path.parent)
                return
        elif path.exists():
            read_private(path)  # refuse nonregular or permissive existing objects
        fd, name = tempfile.mkstemp(prefix='.' + path.name + '-', dir=path.parent)
        try:
            with os.fdopen(fd, 'wb') as out:
                out.write(raw)
                out.flush()
                os.fsync(out.fileno())
            self.checkpoint(phase + '_temp_synced')
            if immutable:
                try:
                    os.link(name, path, follow_symlinks=False)
                except FileExistsError:
                    require(read_private(path) == raw, 'immutable audit object conflicts; preserve evidence')
            else:
                os.replace(name, path)
            self.checkpoint(phase + '_published')
            sync_directory(path.parent)
            self.checkpoint(phase + '_synced')
        finally:
            if os.path.exists(name):
                os.unlink(name)

    def write(self, path, value, *, immutable, phase):
        self.publish(path, encoded(value), immutable=immutable, phase=phase)

    def prepare_path(self, op):
        return self.ops / (identifier(op) + '.prepare.json')

    def outcome_path(self, op):
        return self.ops / (identifier(op) + '.outcome.json')

    def validate_prepare(self, value, op=None):
        keys(value, ['schema', 'op_id', 'operation_id', 'intent_digest', 'intent', 'previous', 'at',
                     'actor', 'authority', 'action', 'reason', 'disposition', 'base', 'after', 'event',
                     'continuity', 'references', 'result'])
        require(value.get('schema') == 1, 'invalid prepare schema')
        identifier(value.get('op_id'))
        require(op is None or value['op_id'] == op, 'prepare operation mismatch')
        require(isinstance(value.get('operation_id'), str) and value['operation_id'], 'missing external operation ID')
        require(isinstance(value.get('intent_digest'), str) and value['intent_digest'], 'missing intent digest')
        for field in ('base', 'after', 'continuity', 'references'):
            require(isinstance(value.get(field), dict), 'invalid prepare ' + field)
        require(isinstance(value['intent'], dict), 'invalid frozen intent')
        keys(value['continuity'], ['kind', 'previous_committed_sha256'])
        require(value['continuity']['kind'] in ('managed_match', 'unmanaged_change_observed', 'unanchored'), 'invalid continuity classification')
        previous_commit = value['continuity']['previous_committed_sha256']
        require(previous_commit is None or (isinstance(previous_commit, str) and re.fullmatch('[0-9a-f]{64}', previous_commit)), 'invalid continuity hash')
        for field in ('at', 'actor', 'action', 'reason'):
            text(value[field], 'prepare.' + field)
        require(value['authority'] is None or isinstance(value['authority'], str), 'invalid authority')
        require(datetime.fromisoformat(value['at'].replace('Z', '+00:00')).utcoffset() is not None, 'prepare time requires timezone')
        previous = value['previous']
        if previous is not None:
            keys(previous, ['op_id', 'prepare_sha256', 'outcome_sha256'])
            identifier(previous['op_id'])
            for field in ('prepare_sha256', 'outcome_sha256'):
                require(isinstance(previous[field], str) and re.fullmatch('[0-9a-f]{64}', previous[field]), 'invalid previous evidence hash')
        for side in ('base', 'after'):
            point = value[side]
            keys(point, ['era', 'revision', 'document_sha256', 'audit_head'] + (['document_token'] if side == 'base' else []))
            text(point['era'], side + '.era')
            require(type(point['revision']) is int and point['revision'] >= 0, 'invalid evidence revision')
            require(isinstance(point['document_sha256'], str) and re.fullmatch('[0-9a-f]{64}', point['document_sha256']), 'invalid document hash')
            if point['audit_head'] is not None:
                identifier(point['audit_head'])
        text(value['base']['document_token'], 'base document token')
        if value['event'] is not None:
            validate_event(value['event'], value['after']['revision'])
        require(value.get('disposition') in ('publication', 'noop'), 'invalid prepare disposition')
        if value['disposition'] == 'noop':
            require(value['event'] is None and value['after'] == {key: field for key, field in value['base'].items() if key != 'document_token'}, 'noop cannot change current')
        else:
            require(value['after']['audit_head'] == value['op_id'], 'publication must carry operation metadata')
        require(isinstance(value.get('result'), dict), 'invalid business result')
        return value

    def validate_outcome(self, value, prepare):
        keys(value, ['schema', 'op_id', 'intent_digest', 'prepare_sha256', 'status', 'basis', 'recorded_at',
                     'observed', 'document_token_after', 'commit'])
        text(value['basis'], 'outcome basis')
        require(datetime.fromisoformat(value['recorded_at'].replace('Z', '+00:00')).utcoffset() is not None, 'outcome time requires timezone')
        keys(value['observed'], ['document_sha256', 'era', 'revision', 'current_valid'])
        require(type(value['observed']['current_valid']) is bool, 'invalid current observation')
        observed_hash = value['observed']['document_sha256']
        require(observed_hash is None or (isinstance(observed_hash, str) and re.fullmatch('[0-9a-f]{64}', observed_hash)), 'invalid observed hash')
        require(value['document_token_after'] is None or isinstance(value['document_token_after'], str), 'invalid observed token')
        require(value.get('schema') == 1 and value.get('op_id') == prepare['op_id'], 'outcome operation mismatch')
        require(value.get('intent_digest') == prepare['intent_digest'] and value.get('prepare_sha256') == sha(encoded(prepare)), 'outcome prepare hash mismatch')
        require(value.get('status') in ('COMMITTED', 'NOT_APPLICABLE', 'ABORTED', 'PUBLICATION_UNKNOWN'), 'invalid outcome status')
        require(value.get('status') != 'COMMITTED' or value.get('commit') == {
            'era': prepare['after']['era'], 'revision': prepare['after']['revision'],
            'after_sha256': prepare['after']['document_sha256']}, 'invalid committed evidence')
        require(value.get('status') != 'NOT_APPLICABLE' or prepare['disposition'] == 'noop', 'invalid noop receipt')
        require(value['status'] == 'COMMITTED' or value['commit'] is None, 'noncommit cannot carry committed authority')
        return value

    def pending(self):
        value = self.read(self.pending_path, optional=True)
        if value is not None:
            keys(value, ['schema', 'op_id', 'prepare_sha256', 'prepare'])
            require(value.get('schema') == 1, 'invalid pending schema')
            self.validate_prepare(value.get('prepare', {}), value.get('op_id'))
            require(value.get('prepare_sha256') == sha(encoded(value['prepare'])), 'pending prepare hash mismatch')
        return value

    def lookup(self, op):
        prepare = self.read(self.prepare_path(op), optional=True)
        outcome = self.read(self.outcome_path(op), optional=True)
        if prepare is not None:
            self.validate_prepare(prepare, op)
        require(outcome is None or prepare is not None, 'outcome without prepare; preserve evidence')
        if outcome is not None:
            self.validate_outcome(outcome, prepare)
        return prepare, outcome

    def head(self):
        head = self.read(self.head_path, optional=True)
        if head is None:
            return None
        keys(head, ['schema', 'op_id', 'prepare_sha256', 'outcome_sha256', 'last_committed'])
        require(head.get('schema') == 1, 'invalid head schema')
        prepare, outcome = self.lookup(identifier(head.get('op_id')))
        require(prepare is not None and outcome is not None, 'head references incomplete attempt')
        require(head.get('prepare_sha256') == sha(encoded(prepare)) and head.get('outcome_sha256') == sha(encoded(outcome)), 'head object hash mismatch')
        last = head.get('last_committed')
        if outcome['status'] == 'COMMITTED':
            require(last == dict(op_id=head['op_id'], **outcome['commit']), 'head lost latest committed pointer')
        else:
            expected = prepare['continuity']['previous_committed_sha256']
            require((last is None and expected is None) or (isinstance(last, dict) and last.get('after_sha256') == expected), 'head lost prior committed pointer')
        if last is not None:
            identifier(last.get('op_id'))
            require(set(last) == {'op_id', 'after_sha256', 'era', 'revision'}, 'invalid last committed pointer')
            committed_prepare, committed_outcome = (prepare, outcome) if last['op_id'] == head['op_id'] else self.lookup(last['op_id'])
            require(committed_prepare is not None and committed_outcome is not None and committed_outcome['status'] == 'COMMITTED', 'last committed pointer has no commit')
            require(last == dict(op_id=last['op_id'], **committed_outcome['commit']), 'last committed evidence mismatch')
        return head

    def begin(self, prepare):
        self.directories(create=True)
        require(self.pending() is None, 'pending transaction; explicit recovery required')
        self.validate_prepare(prepare)
        journal = dict(schema=1, op_id=prepare['op_id'], prepare_sha256=sha(encoded(prepare)), prepare=prepare)
        self.write(self.pending_path, journal, immutable=False, phase='pending')
        self.write(self.prepare_path(prepare['op_id']), prepare, immutable=True, phase='prepare')

    def finish(self, prepare, outcome):
        self.validate_outcome(outcome, prepare)
        self.write(self.outcome_path(prepare['op_id']), outcome, immutable=True, phase='outcome')
        head = self.head()
        previous = prepare['previous']
        same = head is not None and head['op_id'] == prepare['op_id']
        if not same:
            actual = {k: head[k] for k in ('op_id', 'prepare_sha256', 'outcome_sha256')} if head else None
            require(previous == actual, 'head continuity conflict; preserve evidence')
        last = head['last_committed'] if head else None
        if outcome['status'] == 'COMMITTED':
            last = dict(op_id=prepare['op_id'], **outcome['commit'])
        new_head = dict(schema=1, op_id=prepare['op_id'], prepare_sha256=sha(encoded(prepare)),
                        outcome_sha256=sha(encoded(outcome)), last_committed=last)
        self.write(self.head_path, new_head, immutable=False, phase='head')
        self.checkpoint('pending_before_unlink')
        self.pending_path.unlink(missing_ok=True)
        self.checkpoint('pending_unlinked')
        sync_directory(self.path)
        self.checkpoint('pending_cleanup_synced')

    def archive(self, op, raw, manifest):
        identifier(op)
        self.directories(create=True)
        self.publish(self.legacy / (op + '.md'), raw, immutable=True, phase='archive')
        self.write(self.legacy / (op + '.manifest.json'), manifest, immutable=True, phase='archive_manifest')

    def history(self, head=None):
        """Explicit expensive audit traversal; never called by ordinary mutations."""
        head = self.head() if head is None else head
        attempts, seen = [], set()
        pointer = head
        while pointer:
            op = identifier(pointer['op_id'])
            require(op not in seen, 'cyclic audit history')
            seen.add(op)
            prepare, outcome = self.lookup(op)
            require(prepare is not None and outcome is not None, 'incomplete audit history')
            require(pointer['prepare_sha256'] == sha(encoded(prepare)) and pointer['outcome_sha256'] == sha(encoded(outcome)), 'historical evidence hash mismatch')
            attempts.append(dict(prepare=prepare, outcome=outcome))
            pointer = prepare['previous']
        return list(reversed(attempts))

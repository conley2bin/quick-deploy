#!/usr/bin/env python3
"""Read-only owner sidebar projection. Shared pure installed codec; no Store, discovery or mutations.

Transport entry points own one RegistryReader with fixed default paths per app.
project()/stateless read_registry(root) are also usable by isolated tests.
"""
from contextlib import contextmanager
from copy import deepcopy
import fcntl
import importlib.util
import os
from pathlib import Path
import re
import stat
import threading

ROOT = Path.home() / '.pi/agent/.memory/local/responsibility-registry'
DOCUMENT = Path.home() / '.pi/agent/skills/responsibility-registry/RESPONSIBILITIES.md'
CODEC = DOCUMENT.parent / 'scripts/registry_document.py'
FORMAT = 'responsibility-registry/v2'
SCHEMA = 'codoxear-owner-groups/v1'
MAX_OWNERS = 500
MAX_DEPTH = 64


def require(condition):
    if not condition:
        raise ValueError('invalid registry')


def text(value):
    return isinstance(value, str) and bool(value.strip())


def display(value):
    require(isinstance(value, str))
    return re.sub(r'[\x00-\x1f\x7f]', ' ', value)[:160]


def failure(code):
    messages = {
        'unavailable': 'Owner registry unavailable or not initialized.',
        'invalid': 'Owner registry is invalid or exceeds supported owner/depth limits; no partial tree shown.',
        'incompatible-codec': 'Owner registry codec lacks the current-view API; install a compatible registry codec.',
    }
    return {'schema': SCHEMA, 'ok': False, 'error': messages[code], 'owners': []}


def project(state):
    require(isinstance(state, dict) and type(state.get('version')) is int and state['version'] == 1)
    owners, assignments, history = (state.get(k) for k in ('owners', 'assignments', 'history'))
    require(isinstance(owners, dict) and isinstance(assignments, dict) and isinstance(history, list))
    require(type(state.get('revision')) is int and state['revision'] == len(history))
    return _project_current_rows(state)


def project_current(state, codec):
    """Validate the five current fields, then project; no fabricated audit state."""
    codec.validate_current(state)
    return _project_current_rows(state)


def _project_current_rows(state):
    owners, assignments = state['owners'], state['assignments']
    require(len(owners) <= MAX_OWNERS)
    for oid, owner in owners.items():
        require(text(oid) and oid != 'user' and not oid.startswith('owner:'))
        require(isinstance(owner, dict) and owner.get('id') == oid)
        require(text(owner.get('name')) and text(owner.get('role')))
        parent = owner.get('parent')
        require(text(parent) and (parent == 'user' or
                (parent.startswith('owner:') and text(parent[6:]) and parent[6:] == parent[6:].strip())))
        identity = owner.get('identity')
        require(isinstance(identity, dict))
        source, pi, contact = (identity.get(k) for k in ('source', 'pi_session_id', 'intercom_id'))
        require(source in ('current-session', 'intercom-status', 'unavailable'))
        require((pi is None or text(pi)) and (contact is None or text(contact)))
        require(isinstance(identity.get('unknown_reason'), str))
        require((pi is not None and contact is not None) or text(identity['unknown_reason']))
        require(source != 'current-session' or text(pi))
        require(source != 'intercom-status' or (pi is None and text(contact)))
        require(source != 'unavailable' or (pi is None and contact is None))
        seen, here = set(), oid
        while here in owners:
            require(here not in seen and len(seen) < MAX_DEPTH)
            seen.add(here)
            ancestor = owners[here]
            require(isinstance(ancestor, dict) and text(ancestor.get('parent')))
            here = ancestor['parent'][6:] if ancestor['parent'].startswith('owner:') else None

    scopes = {oid: [] for oid in owners}
    for aid, assignment in assignments.items():
        require(isinstance(assignment, dict) and assignment.get('id') == aid)
        oid, status = assignment.get('owner'), assignment.get('status')
        require(oid in owners and status in ('active', 'ended', 'handed-over'))
        require(text(assignment.get('task')))
        require(isinstance(assignment.get('project'), dict) and text(assignment['project'].get('name')))
        scopes[oid].append({'task': display(assignment['task']),
                            'project': display(assignment['project']['name']), 'status': status})
    result, claims = [], {}
    # The canonical CLI appends owners and preserves object insertion order on save.
    # Export only the ordinal, not timestamps or private appointment history.
    for registration_order, oid in enumerate(owners):
        owner, identity = owners[oid], owners[oid]['identity']
        pi = identity['pi_session_id'] if identity['source'] == 'current-session' else None
        if pi:
            claims.setdefault(pi, []).append(oid)
        statuses = {scope['status'] for scope in scopes[oid]}
        responsibility = next(iter(statuses)) if len(statuses) == 1 else ('mixed' if statuses else 'no-scope')
        parent = owner['parent']
        result.append({'id': oid, 'name': display(owner['name']), 'role': display(owner['role']),
                       'parent': parent, 'registration_order': registration_order,
                       'parent_status': 'root' if parent == 'user' else
                           ('resolved' if parent[6:] in owners else 'unresolved'),
                       'pi_session_id': pi, 'identity_source': identity['source'],
                       'identity_note': display(identity['unknown_reason']) if pi is None else '',
                       'state': responsibility,
                       'scopes': sorted(scopes[oid], key=lambda s: (s['status'] != 'active', s['task'], s['project']))})
    for owner in result:
        owner['duplicate_identity'] = bool(owner['pi_session_id'] and len(claims[owner['pi_session_id']]) > 1)
    return {'schema': SCHEMA, 'ok': True, 'revision': state['revision'], 'owners': result}


def load_codec(path, source=None):
    spec = importlib.util.spec_from_file_location('owner_registry_document', path)
    require(spec is not None and spec.loader is not None)
    module = importlib.util.module_from_spec(spec)
    if source is None:
        with open_source(path) as source:
            content = source.read()
    else:
        content = source.read()
    # A same-size edit with restored mtime must not reuse importlib's stale .pyc.
    exec(compile(content, str(path), 'exec'), module.__dict__)
    require(module.FORMAT == FORMAT)
    return module


def source_version(info):
    if not stat.S_ISREG(info.st_mode) or not info.st_mode & 0o444:
        raise OSError('registry source must be a readable regular file')
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


@contextmanager
def open_source(path):
    """Open existing readable regular files without following a final symlink."""
    before = source_version(path.lstat())
    # NONBLOCK also prevents a raced-in FIFO from blocking a worker before fstat.
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as source:
        if source_version(os.fstat(source.fileno())) != before:
            raise OSError('registry source changed while opening')
        yield source


def _load_projection(source, codec_source, codec_path):
    """Read only the current prefix; audit bytes are never decoded or parsed."""
    try:
        codec = load_codec(codec_path, codec_source)
        if not all(callable(getattr(codec, name, None))
                   for name in ('parse_current_document', 'validate_current')):
            return failure('incompatible-codec')
        try:
            # TextIOWrapper may decode a prefetched invalid audit byte too early.
            # Binary buffering is bounded; decode only lines requested by the codec.
            state, era = codec.parse_current_document(line.decode('utf-8') for line in source)
            return dict(project_current(state, codec), era=era)
        except codec.RegistryError as exc:
            raise ValueError('invalid current document') from exc
    except (ImportError, SyntaxError):
        return failure('unavailable')
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError):
        return failure('invalid')


class RegistryReader:
    """One generation of public projection, singleflight within this app's workers.

    No raw document, codec module, audit state or per-request identity is retained.
    Shared registry.lock still serializes against cooperating registry writers.
    """
    def __init__(self, root=ROOT, document=DOCUMENT, codec_path=CODEC):
        self.root = Path(root)
        self.document = Path(document)
        self.codec_path = Path(codec_path)
        self._mutex = threading.Lock()
        self._generation = None
        self._projection = None

    def __call__(self):
        # Called through asyncio.to_thread: neither mutex nor flock blocks the loop.
        with self._mutex:
            try:
                with open_source(self.root / 'registry.lock') as lock:
                    fcntl.flock(lock, fcntl.LOCK_SH)
                    with open_source(self.document) as source, open_source(self.codec_path) as codec_source:
                        generation = (source_version(os.fstat(source.fileno())),
                                      source_version(os.fstat(codec_source.fileno())))
                        if generation != self._generation:
                            # Discard success before attempting a new or invalid generation.
                            self._generation = self._projection = None
                            result = _load_projection(source, codec_source, self.codec_path)
                        else:
                            result = self._projection
                        after = (source_version(os.fstat(source.fileno())),
                                 source_version(os.fstat(codec_source.fileno())))
                        paths = (source_version(self.document.lstat()),
                                 source_version(self.codec_path.lstat()))
                        if generation != after or generation != paths:
                            raise OSError('registry source changed while reading')
                        self._generation, self._projection = generation, result
                        return deepcopy(result)
            except (OSError, ImportError, SyntaxError):
                code = 'unavailable'
            except (ValueError, TypeError, KeyError, AttributeError, RecursionError):
                code = 'invalid'
            self._generation = self._projection = None
            return failure(code)


def read_registry(root=ROOT, document=DOCUMENT, codec_path=CODEC):
    """Stateless compatibility entrypoint; transports install their own reader."""
    return RegistryReader(root, document, codec_path)()

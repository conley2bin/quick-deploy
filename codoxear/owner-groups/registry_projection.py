#!/usr/bin/env python3
"""Read-only owner sidebar projection. No skill imports, discovery or mutations.

Transport entry points must call read_registry() with its fixed default path.
project()/read_registry(root) are also usable by isolated tests.
"""
import fcntl
import json
from pathlib import Path
import re

ROOT = Path.home() / '.pi/agent/.memory/local/responsibility-registry'
SCHEMA = 'codoxear-owner-groups/v1'
MAX_BYTES = 8 * 1024 * 1024
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
        'invalid': 'Owner registry is invalid or exceeds supported size/depth; no partial tree shown.',
    }
    return {'schema': SCHEMA, 'ok': False, 'error': messages[code], 'owners': []}


def project(state):
    require(isinstance(state, dict) and type(state.get('version')) is int and state['version'] == 1)
    owners, assignments, history = (state.get(k) for k in ('owners', 'assignments', 'history'))
    require(isinstance(owners, dict) and isinstance(assignments, dict) and isinstance(history, list))
    require(type(state.get('revision')) is int and state['revision'] == len(history))
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
    for oid in sorted(owners):
        owner, identity = owners[oid], owners[oid]['identity']
        pi = identity['pi_session_id'] if identity['source'] == 'current-session' else None
        if pi:
            claims.setdefault(pi, []).append(oid)
        statuses = {scope['status'] for scope in scopes[oid]}
        responsibility = next(iter(statuses)) if len(statuses) == 1 else ('mixed' if statuses else 'no-scope')
        parent = owner['parent']
        result.append({'id': oid, 'name': display(owner['name']), 'role': display(owner['role']),
                       'parent': parent,
                       'parent_status': 'root' if parent == 'user' else
                           ('resolved' if parent[6:] in owners else 'unresolved'),
                       'pi_session_id': pi, 'identity_source': identity['source'],
                       'identity_note': display(identity['unknown_reason']) if pi is None else '',
                       'state': responsibility,
                       'scopes': sorted(scopes[oid], key=lambda s: (s['status'] != 'active', s['task'], s['project']))})
    for owner in result:
        owner['duplicate_identity'] = bool(owner['pi_session_id'] and len(claims[owner['pi_session_id']]) > 1)
    return {'schema': SCHEMA, 'ok': True, 'revision': state['revision'], 'owners': result}


def unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result)
        result[key] = value
    return result


def read_registry(root=ROOT):
    try:
        # Existing lock opened read-only. Never mkdir/create/repair/query the registry.
        with (root / 'registry.lock').open('rb') as lock:
            fcntl.flock(lock, fcntl.LOCK_SH)
            with (root / 'registry.json').open('rb') as source:
                raw = source.read(MAX_BYTES + 1)
            require(len(raw) <= MAX_BYTES)
            state = json.loads(raw, object_pairs_hook=unique_pairs,
                               parse_constant=lambda _: require(False))
            return project(state)
    except (OSError, PermissionError):
        return failure('unavailable')
    except (ValueError, TypeError, KeyError, RecursionError):
        return failure('invalid')

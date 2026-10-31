"""Attach a fresh machine's private manager state to an EXISTING managed 3x-ui node.

The server is the only source of owner/config/machine/node facts: a fresh standard
archive is pulled over the pinned SSH transport (never `backup`, which stops FRONT),
inspected by the unchanged local backup gate, cross-checked against native outputs
by `validate_outputs`, and committed by `remember_commit` like any other snapshot.

The vps module is passed in, not imported: setup.sh runs it as `__main__`, and a
second import would lose its per-invocation SSH carrier context.
"""
import os
from pathlib import Path
import secrets
import sys
import tarfile
import time

import host

INFO_KEYS = {'format', 'owner', 'machine_id', 'node_id', 'endpoint', 'config', 'published'}


def local_binding(vps, state):
    """Existing committed document, or None for an absent state. Refuse anything half-made."""
    if (state / 'pending.json').exists() or (state / 'pending.json').is_symlink():
        raise host.Error('Local transaction pending; run recover first or choose another --state-dir')
    path = state / 'state.json'
    if not path.exists():
        if path.is_symlink():
            raise host.Error('Refusing symlink state.json')
        return None
    document = vps.load(path)
    if document.get('format') != host.FORMAT or not isinstance(document.get('current'), dict):
        raise host.Error('Existing local state has no committed node; choose another --state-dir')
    return document


def connect_record(vps, args, document):
    current = document['current'] if document else None
    if current and args.ssh == current['target'] and args.port is None and args.identity is None:
        return dict(current)  # keeps saved alias, trust metadata and options exactly
    return vps.target_record(args, current)


def checked_info(info, record):
    if not isinstance(info, dict) or not INFO_KEYS <= set(info) or info['format'] != host.FORMAT:
        raise host.Error('Unexpected connect-info response; nothing written')
    for key in ('owner', 'machine_id', 'node_id'):
        if not isinstance(info[key], str) or not info[key]:
            raise host.Error('Unexpected connect-info response; nothing written')
    host.validate_config(info['config'])
    if info['endpoint'] != record['endpoint'] or info['published'] is not True:
        raise host.Error('Server is not a published node at the SSH endpoint; nothing written')
    return info


def old_manifest(vps, document):
    """The saved archive, only if it is still present and valid; never required."""
    if not document.get('backup'):
        return None
    try:
        return vps.checked_backup(document, document['current'], Path(document['backup']))
    except (host.Error, OSError, ValueError, KeyError, tarfile.TarError):
        return None


def check_binding(vps, document, record, info):
    """A foreign or replaced node is refused before any local write."""
    current = document['current']
    if (document['owner'] != info['owner'] or document['config'] != info['config']
            or current['endpoint'] != info['endpoint']):
        raise host.Error('Local state belongs to a different deployment; choose another --state-dir')
    if current.get('machine_id') not in (None, info['machine_id']) or current.get('node_id') not in (None, info['node_id']):
        raise host.Error('Local state belongs to a different machine/node; choose another --state-dir')
    old = old_manifest(vps, document)
    if 'node_id' not in current and old is not None and old['node_id'] != info['node_id']:
        raise host.Error('Local state belongs to a replaced node; choose another --state-dir')


def pull(vps, state, record, request):
    directory = vps.private_dir(state / 'backups')
    final = directory / ('connect-' + time.strftime('%Y%m%dT%H%M%S') + '-' + secrets.token_hex(4) + '.tar')
    part = final.with_suffix('.incomplete')
    fd = os.open(part, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as out:
        vps.remote(record, 'connect-pull', request, stdout=out)
        out.flush()
        os.fsync(out.fileno())
    os.rename(part, final)
    return final, part


def committed_on_disk(vps, state, backup):
    """Judge by the durable binding, not by whether remember_commit returned."""
    """True/False from the durable binding; None when it cannot be read (outcome unknown)."""
    try:
        return vps.load(state / 'state.json').get('backup') == str(backup.absolute())
    except FileNotFoundError:
        return False
    except (host.Error, OSError, ValueError):
        return None


def run(vps, state, args):
    document = local_binding(vps, state)
    binary = vps.resolve_mihomo(args.mihomo)  # real core before any remote I/O
    record = connect_record(vps, args, document)
    lock = vps.release_lock()
    expected = {'endpoint': record['endpoint'], 'ssh_port': record['port'], 'lock': lock}
    if record.get('machine_id'):
        expected['machine_id'] = record['machine_id']
    info = checked_info(vps.remote(record, 'connect-info', expected), record)
    if document:
        check_binding(vps, document, record, info)
    else:
        document = {'format': host.FORMAT, 'owner': info['owner'], 'config': info['config'],
                    'current': None, 'previous': [], 'backup': None}
    record = dict(record, machine_id=info['machine_id'], node_id=info['node_id'])
    request = dict(vps.request_for(document, record), node_id=info['node_id'], lock=lock,
                   adapter_sha256=host.digest(vps.MODULE / 'lib/host.py'))
    backup, part = pull(vps, state, record, request)
    entered = False
    try:
        manifest = vps.checked_backup(document, record, backup)
        if manifest['node_id'] != info['node_id'] or not manifest.get('published'):
            raise host.Error('Pulled archive is not the published node described by connect-info')
        try:
            vps.validate_outputs(state, document, record, binary, backup)
        except host.Error as exc:
            raise host.Error('Server changed or outputs disagree with the pulled archive during connect; '
                             'nothing was committed, retry connect: ' + str(exc)) from exc
        stale_outputs = old_manifest(vps, document) is None and bool(document.get('backup'))
        if stale_outputs:
            document = dict(document, backup=None)  # an absent/stale archive is not needed to recover
        entered = True
        vps.remember_commit(state, document, record, backup, {'machine_id': info['machine_id'], 'node_id': info['node_id']})
    except BaseException as exc:
        status = committed_on_disk(vps, state, backup) if entered else False
        if status:
            # state.json already references this archive: never orphan it.
            raise host.Error('Connect committed locally (state.json references the new snapshot) but finalization failed: '
                             + type(exc).__name__ + '; rerun connect to refresh') from exc
        if status is None:
            raise host.Error('Connect local outcome UNKNOWN (state.json unreadable after commit began); archive kept; '
                             'inspect state.json, then rerun connect (idempotent): ' + type(exc).__name__) from exc
        if backup.exists():
            backup.rename(part)
        raise
    if stale_outputs:
        kept = sorted(n for n in os.listdir(state / 'outputs') if n.endswith(('.yaml', '.txt'))
                      and n not in vps.profile_names(manifest))
        if kept:
            print(f'Warning: previous archive unusable; {len(kept)} older profile file(s) outside the fresh roster were '
                  'kept unverified in outputs/ (not deleted). Remove them yourself if those devices are gone.', file=sys.stderr)
    enabled = sum(1 for account in manifest['accounts'].values() if account['enable'])
    print(f"Connected {record['endpoint']}: {enabled} enabled device(s); private state {state}; snapshot {backup}")

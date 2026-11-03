#!/usr/bin/env python3
"""Private accountability directory. POSIX, Python stdlib; Markdown is authoritative."""
import argparse
import copy
from datetime import datetime, timedelta, timezone
import fcntl
import html
import json
import os
import re
from pathlib import Path
import sys
import tempfile
import hashlib
import uuid
import stat as stat_module

DEFAULT_ROOT = Path.home() / '.pi/agent/.memory/local/responsibility-registry'
DEFAULT_DOCUMENT = Path.home() / '.pi/agent/skills/responsibility-registry/RESPONSIBILITIES.md'


if __package__:
    from .registry_document import (RegistryError, require, parse_json, text, keys, identity,
        validate_time, goal_source, recovery_context, owner, scope, check_graph, validate,
        validate_agreement, agreement_view, assignment_digest, terms_digest, parse_document, serialize_document,
        FORMAT, TEMPLATE_ERA, validate_current, parse_current_document)
    from . import registry_document as document_codec
    from .audit_storage import AuditStorage, AuditDamaged, encoded, sha, identifier, read_private, sync_directory
else:
    from registry_document import (RegistryError, require, parse_json, text, keys, identity,
        validate_time, goal_source, recovery_context, owner, scope, check_graph, validate,
        validate_agreement, agreement_view, assignment_digest, terms_digest, parse_document, serialize_document,
        FORMAT, TEMPLATE_ERA, validate_current, parse_current_document)
    import registry_document as document_codec
    from audit_storage import AuditStorage, AuditDamaged, encoded, sha, identifier, read_private, sync_directory



class RegistryTransactionError(RegistryError):
    def __init__(self, code, message, *, commit_state='not_committed', retryable=False, operation_id=None):
        super().__init__(message)
        self.code = code
        self.commit_state = commit_state
        self.retryable = retryable
        self.operation_id = operation_id
        self.op_id = operation_id


def current_fields(state):
    return {key: state[key] for key in ('version', 'revision', 'owners', 'assignments', 'coordination')}


def coordinate(state, args, payload, stamp):
    records = state['coordination']
    if args.command == 'coord-propose':
        keys(payload, ['id', 'scopes', 'shared_interfaces', 'workspace', 'sequencing', 'integration_owner', 'acceptance'])
        text(payload['id'], 'coordination ID')
        require(payload['id'] not in records, 'agreement ID exists; release old agreement and propose a new ID for renegotiation')
        require(isinstance(payload['scopes'], dict) and payload['scopes'], 'scope-to-write-boundary map required')
        scopes, repo = {}, None
        for aid, boundary in payload['scopes'].items():
            require(aid in state['assignments'], 'unknown direction scope: ' + aid)
            a = state['assignments'][aid]
            require(a['status'] == 'active' and 'repo' in a, 'coordination requires active scope and verified repo identity')
            repo = a['repo'] if repo is None else repo
            require(a['repo'] == repo, 'affected scopes must share canonical repository identity')
            scopes[aid] = dict(owner=a['owner'], write_scope=boundary, assignment_updated_at=a['updated_at'], assignment_digest=assignment_digest(a))
        require(args.actor in {b['owner'] for b in scopes.values()}, 'proposer must be participating responsible owner')
        value = dict(copy.deepcopy(payload), repo=copy.deepcopy(repo), scopes=scopes, status='proposed',
                     acknowledgements={}, created_at=stamp, updated_at=stamp)
        validate_agreement(state, value)
        records[value['id']] = value
    else:
        require(args.coordination in records, 'unknown coordination ID')
        value = records[args.coordination]
        participants = {b['owner'] for b in value['scopes'].values()}
        require(args.actor in participants, 'only a participating owner can acknowledge/release its agreement')
        require(value['status'] != 'released', 'agreement released; propose a new ID')
        if args.command == 'coord-ack':
            require(not any(('scope changed' in r or 'terms changed' in r) for r in agreement_view(state, value)['not_ready_reasons']),
                    'scope changed; release and renegotiate before acknowledgement')
            require(args.actor not in value['acknowledgements'], 'owner already acknowledged; query existing evidence')
            value['acknowledgements'][args.actor] = dict(at=stamp, evidence=args.reason, terms_digest=terms_digest(value))
            if set(value['acknowledgements']) == participants:
                value['status'] = 'confirmed'
        else:
            value['status'] = 'released'
        value['updated_at'] = stamp


def occurrence(args, stamp):
    precision = getattr(args, 'occurrence_precision', None) or 'exact'
    value = getattr(args, 'occurred_at', None)
    if precision == 'exact':
        value = value or datetime.fromisoformat(stamp).isoformat(timespec='seconds')
    result = {'at': value, 'precision': precision}
    validate_time(result)
    if precision == 'exact':
        result['at'] = datetime.fromisoformat(value.replace('Z', '+00:00')).isoformat(timespec='seconds')
    return result


def owner_fact(owners, oid):
    if oid is None:
        return None
    value = owners.get(oid, {})
    return dict(id=oid, name=value.get('name'), role=value.get('role'))


def semantic_changes(before, state, action, args):
    def change(old, new, summary):
        old_id, new_id = (old or {}).get('owner'), (new or {}).get('owner')
        scope_record = new or old or {}
        return dict(project=scope_record.get('project'), task=scope_record.get('task'),
                    old_owner=owner_fact(before['owners'], old_id), new_owner=owner_fact(state['owners'], new_id),
                    from_assignment=(old or {}).get('id'), to_assignment=(new or {}).get('id'),
                    from_parent=before['owners'].get(old_id, {}).get('parent'),
                    to_parent=state['owners'].get(new_id, {}).get('parent'), summary=summary)
    if action == 'handover' and args is not None:
        return [change(before['assignments'][args.assignment], state['assignments'][args.new_assignment],
                       'handover; pending work: ' + args.pending_work + '; reporting: ' +
                       (args.reporting_change or 'superior unchanged'))]
    rows = []
    for aid, current in state['assignments'].items():
        old = before['assignments'].get(aid)
        owner_changed = before['owners'].get(current['owner']) != state['owners'][current['owner']]
        if old != current or (action == 'update-owner' and owner_changed):
            rows.append(change(old, current, action + ': ' + (old or {}).get('status', 'unassigned') + ' → ' + current['status']))
    if not rows and action.startswith('coord-'):
        for cid, agreement in state['coordination'].items():
            if before['coordination'].get(cid) != agreement:
                for aid in agreement['scopes']:
                    rows.append(change(before['assignments'].get(aid), state['assignments'].get(aid), action + ': ' + cid))
    if not rows:
        for oid, current in state['owners'].items():
            if before['owners'].get(oid) != current:
                rows.append(dict(project=None, task=None, old_owner=owner_fact(before['owners'], oid) if oid in before['owners'] else None,
                                 new_owner=owner_fact(state['owners'], oid), from_assignment=None, to_assignment=None,
                                 from_parent=before['owners'].get(oid, {}).get('parent'), to_parent=current['parent'], summary=action))
    return rows


def handover_request(args):
    return {key: getattr(args, key, None) for key in ['actor', 'assignment', 'to_owner', 'new_assignment', 'reason',
            'pending_work', 'coordination_plan', 'reporting_change', 'occurred_at', 'occurrence_precision', 'authority']}


def empty():
    return dict(version=1, revision=0, owners={}, assignments={}, coordination={}, history=[])


def now():
    return datetime.now(timezone.utc).isoformat(timespec='microseconds')


def cell(value):
    if value is None:
        return 'unknown'
    # Escape HTML before adding our own break marker; no raw pipes or newlines in cells.
    return html.escape(str(value), quote=True).replace('|', '&#124;').replace('\r\n', '\n').replace('\r', '\n').replace('\n', '<br>').replace('`', '&#96;')


def table(headers, rows):
    return '\n'.join(['| ' + ' | '.join(headers) + ' |', '| ' + ' | '.join(['---'] * len(headers)) + ' |'] +
                     ['| ' + ' | '.join(cell(v) for v in row) + ' |' for row in rows])


def time_label(value):
    if not value:
        return 'not recorded historically; occurrence time unknown'
    return (value['at'] or 'unknown') + (' (approximate; second-level precision not guaranteed)' if value['precision'] == 'approximate' else
                                     ' (occurrence time unknown)' if value['precision'] == 'unknown' else '')


def recovery_label(value):
    if not value:
        return 'recovery locator not registered; do not guess a session path'
    return ('session file: ' + (value['session_file'] or 'unknown') + '; cwd: ' + (value['cwd'] or 'unknown') +
            '; repo: ' + (value['repo'] or 'unknown') + '; worktree: ' + (value['worktree'] or 'unknown') +
            '; summary: ' + (value['summary'] or 'unknown') + '; decisions: ' + (value['decisions'] or 'unknown') +
            '; pending issues: ' + (value['pending_issues'] or 'unknown') + '; references: ' + ', '.join(value['context_refs']) +
            '; evidence: ' + value['source'] + '; missing: ' + value['unknown_reason'])


def obligation_label(value):
    if not value:
        return '—'
    return ('from ' + value['predecessor'] + '; child work: ' + value['pending_work'] +
            ('; previous arrangement: ' + value['previous_pending_work'] if value['previous_pending_work'] else '') +
            '; coordination: ' + ', '.join(value['coordination_ids']) + '; plan: ' + value['coordination_plan'] +
            '; reporting: ' + value['reporting_change'] +
            ('; predecessor owner context: ' + recovery_label(value['predecessor_context'].get('recovery'))
             if value.get('predecessor_context') else '; predecessor session locator not registered'))


def owner_label(value):
    return '—' if value is None else f'{value["id"]} / {value.get("name") or "name unknown"} / {value.get("role") or "role unknown"}'


def source_label(value):
    return value['issuer'] + ': ' + value['source'] if value else 'not recorded historically; confirmation pending'


def parent_label(state, parent):
    if parent == 'user':
        return 'user (highest authority)'
    oid = parent[6:]
    return f'owner:{oid}' + (f' ({state["owners"][oid]["name"]})' if oid in state['owners'] else ' (not registered; resolution pending)')


def dashboard(state, era):
    owners, assignments = state['owners'], state['assignments']
    lines = ['# Global responsibility registry', '', f'Era {era} · Version 1 · Revision {state["revision"]}', '',
             'Registration records responsibility, not online status; recheck intercom status/list before contact. The user is the highest authority.', '',
             'Only top-level owners with parent:user report to the user; other owners report through the hierarchy, with goals and acceptance criteria delegated level by level. Clarify unresolved superiors; never promote them automatically to the top level.', '',
             'Non-authoritative point-in-time report; any edit or reset may make it stale. The installed RESPONSIBILITIES.md is the sole current source of truth; reread it.', '', '## Owners / Hierarchy / Contacts', '']
    rows = []
    for oid, o in sorted(owners.items()):
        i = o['identity']
        children = ', '.join(k for k, v in sorted(owners.items()) if v['parent'] == 'owner:' + oid) or 'no registered subordinates'
        rows.append([oid, o['name'], o['role'], parent_label(state, o['parent']), children,
                     i['pi_session_id'], i['intercom_id'], i['source'] + ('; ' + i['unknown_reason'] if i['unknown_reason'] else ''),
                     source_label(o.get('appointment_source')), recovery_label(o.get('recovery')), o['updated_at']])
    lines += [table(['owner ID', 'Name', 'Role', 'Superior', 'Subordinates (derived)', 'Pi session ID', 'Intercom contact ID', 'Identity evidence / Missing reason', 'Appointment issuer / Source', 'Predecessor context locator / Handover decisions', 'Updated at'], rows), '', '## Projects / Tasks / Responsibility scopes', '']
    rows = []
    for aid, a in sorted(assignments.items()):
        p = a['project']
        rows.append([aid, p['id'], p['name'], p['path'], a['task'], a['owner'], a['status'], a['objective'],
                     a['acceptance'], source_label(a.get('goal_source')), a.get('repo', {}).get('id', 'not recorded historically; verification pending'),
                     a['appointment'], time_label(a.get('start_time')), time_label(a.get('end_time')) if a['status'] != 'active' else 'ongoing',
                     a['end_reason'] or '—', a['successor'] or '—',
                     obligation_label(a.get('handover_obligations')), a['created_at'], a['updated_at']])
    lines += [table(['Responsibility ID', 'Project ID', 'Project name', 'Project path', 'Task ID', 'owner ID', 'Status', 'Objective / Scope', 'Acceptance criteria', 'Goal issuer / Source', 'Canonical repo ID', 'Appointment evidence', 'Responsibility started (occurred)', 'Responsibility ended (occurred)', 'End reason', 'Successor responsibility ID', 'Inherited child work / Coordination / Reporting arrangements', 'Record created at', 'Record updated at'], rows), '', '## Dependencies (independent of reporting relationships)', '']
    rows = []
    for aid, a in sorted(assignments.items()):
        for d in a['depends_on']:
            resolved = (any(x['project']['id'] == d['project'] and
                            (d['kind'] == 'project' or x['task'] == d['task']) and x['status'] == 'active'
                            for x in assignments.values()))
            rows.append([aid, a['status'], d['kind'], d['project'], d.get('task', 'entire project'), 'registered' if resolved else 'not registered / no active responsibility'])
    lines += [table(['Source responsibility ID', 'Source status', 'Target type', 'Target project ID', 'Target task ID', 'Target resolution'], rows), '',
              '## Same-repository write coordination (a ready agreement does not grant a writer lock)', '',
              'Sending a proposal is not consent; the agreement is ready only after every affected party explicitly acknowledges it. It covers only the listed scopes; a shared cwd still permits only one writer, and isolated worktrees still follow the agreed overlap/interface integration rules.', '']
    rows = []
    for cid, value in sorted(state.get('coordination', {}).items()):
        view = agreement_view(state, value)
        bounds = '; '.join(f'{aid} / {b["owner"]}: {", ".join(b["write_scope"])}' for aid, b in value['scopes'].items())
        acks = '; '.join(f'{oid}: {a["evidence"]} @ {a["at"]}' for oid, a in value['acknowledgements'].items()) or 'no explicit acknowledgement'
        rows.append([cid, value['repo']['id'], value['repo']['path'], bounds, value['status'],
                     'yes (acknowledged scopes only)' if view['agreement_ready'] else 'no: ' + '; '.join(view['not_ready_reasons']),
                     ', '.join(view['participants']), acks, value['shared_interfaces'],
                     value['workspace']['mode'] + ': ' + value['workspace']['plan'], value['sequencing'],
                     value['integration_owner'], value['acceptance']])
    lines += [table(['Agreement ID', 'repo ID', 'Canonical repo path', 'Direction scope / owner / Write boundary', 'Status', 'Write agreement ready', 'Participants', 'Explicit acknowledgement evidence', 'Shared files / Interfaces', 'Branch / Worktree', 'Sequence', 'Integration owner', 'Acceptance agreement'], rows), '',
              '## Responsibility change history (append-only; event order distinguishes same-second changes)', '',
              'All historical owners and appointments are retained indefinitely; an offline session does not trigger handover. Occurrence and recording times are separate; unknown or approximate times never receive fabricated seconds. Names and roles are the facts saved when each event was recorded.', '']
    rows = []
    for event in state['history']:
        details = event.get('changes') or [dict(project=None, task=None, old_owner=None, new_owner=None,
                                               from_parent=None, to_parent=None, from_assignment=None, to_assignment=None,
                                               summary=event['action'] + ' (legacy event has no semantic fields; see canonical snapshots)')]
        for detail in details:
            p = detail.get('project') or {}
            rows.append([event.get('id', f'legacy-{event["revision"]:08d}'), event['revision'],
                         time_label(event.get('occurred')), event['at'], event['action'], p.get('id', '—') + (' / ' + p['name'] if p.get('name') else ''), detail.get('task') or '—',
                         owner_label(detail.get('old_owner')), owner_label(detail.get('new_owner')),
                         (detail.get('from_assignment') or '—') + ' → ' + (detail.get('to_assignment') or '—'),
                         (detail.get('from_parent') or '—') + ' → ' + (detail.get('to_parent') or '—'),
                         event['actor'], event.get('authority') or 'not recorded separately (see reason / appointment evidence)', event['reason'], detail['summary']])
    lines += [table(['Event ID', 'Sequence', 'Occurrence time / Precision', 'Recorded at (with timezone)', 'Action', 'Project ID', 'Task / Direction', 'From (ID / Name / Role)', 'To (ID / Name / Role)', 'Responsibility chain', 'Superior change', 'Actor', 'Authority evidence', 'Reason', 'Change description'], rows), '']
    return '\n'.join(lines)


def atomic_write(path, content, before_replace=None):
    fd, temp = tempfile.mkstemp(prefix='.' + path.name + '-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as out:
            out.write(content)
            out.flush()
            os.fsync(out.fileno())
        if before_replace is not None:
            before_replace()
        os.replace(temp, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def fingerprint(stat):
    return [stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns]


def fsync_directory(path):
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def exclusive_file(path, content):
    # Immutable snapshots/archives must never expose a half-written final name.
    fd, temp = tempfile.mkstemp(prefix='.' + path.name + '-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as out:
            out.write(content if isinstance(content, bytes) else content.encode('utf-8'))
            out.flush()
            os.fsync(out.fileno())
        os.link(temp, path, follow_symlinks=False)
        fsync_directory(path.parent)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


class Store:
    def __init__(self, root=DEFAULT_ROOT, document=None):
        self.root = Path(os.path.abspath(Path(root).expanduser()))
        require(document is not None or self.root == DEFAULT_ROOT.resolve(),
                'alternate --root requires explicit --document (never infer runtime from source)')
        # Do not resolve away a replacement/symlink at the live filename.
        self.document = Path(os.path.abspath(Path(document or DEFAULT_DOCUMENT).expanduser()))
        self.envelope = None
        self.store_id = sha(encoded([str(self.root), str(self.document)]))
        self.audit = AuditStorage(self.root, self.transaction_checkpoint)

    def transaction_checkpoint(self, phase):
        """Deterministic isolated interruption seam; no runtime fault switch."""

    def locked(self, action, create=False):
        # Normalize only acquisition failures. Domain/callback exceptions from
        # action() retain their exact type and never get classified by message.
        fd = None
        try:
            if create:
                self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
            require(not self.root.is_symlink() and self.root.is_dir(), 'private root cannot be symlink/non-directory')
            require(self.root.stat().st_mode & 0o777 == 0o700, 'private root permissions must be 0700')
            evidence = create and any(path.exists() or path.is_symlink() for path in
                                      (self.audit.path, self.root / 'document-control.json'))
            # A prior runtime must keep its stable lock inode. Bootstrap creation
            # is allowed only before any audit/control initialization evidence.
            flags = os.O_RDWR | (os.O_CREAT if create and not evidence else 0)
            fd = os.open(self.root / 'registry.lock', flags | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
            info = os.fstat(fd)
            require(stat_module.S_ISREG(info.st_mode) and info.st_mode & 0o777 == 0o600, 'registry lock must be a private regular file')
            fcntl.flock(fd, fcntl.LOCK_EX)
        except (OSError, RegistryError) as exc:
            if fd is not None:
                os.close(fd)
            raise RegistryTransactionError('registry_unavailable', 'registry lock unavailable: ' + str(exc),
                                           commit_state='not_committed', retryable=False) from exc
        with os.fdopen(fd, 'a'):
            return action()

    def read_document(self):
        try:
            require(not self.document.is_symlink(), 'live document cannot be a symlink')
            fd = os.open(self.document, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(fd, 'rb') as source:
                info = os.fstat(source.fileno())
                require(stat_module.S_ISREG(info.st_mode), 'live document must be regular')
                before = fingerprint(info)
                raw = source.read()
                after = fingerprint(os.fstat(source.fileno()))
            require(before == after == fingerprint(self.document.stat()),
                    'document changed while reading; query again deliberately')
            content = raw.decode('utf-8')
            if re.search(r'^Format: responsibility-registry/v3\r?$', content, re.MULTILINE):
                state, era, metadata = document_codec.parse_store_document(content)
            else:
                state, era = parse_document(content)
                metadata = dict(format=FORMAT, audit_head=None)
        except FileNotFoundError as exc:
            raise RegistryError('missing live Markdown document; no legacy import or reconstruction') from exc
        except (ValueError, TypeError, KeyError, RegistryError) as exc:
            raise RegistryError('document invalid/changed; preserved unchanged: ' + str(exc)) from exc
        digest = hashlib.sha256(raw).hexdigest()
        token = hashlib.sha256(json.dumps([era, digest, after], separators=(',', ':')).encode()).hexdigest()
        return state, dict(era=era, revision=state['revision'], document_token=token,
                           storage_format=metadata['format'], audit_head=metadata['audit_head']), raw, after

    def _snapshot(self):
        state, envelope, _, _ = self.read_document()
        self.envelope = envelope
        return dict(state=current_fields(state), era=envelope['era'], document_token=envelope['document_token'],
                    format=envelope['storage_format'], audit_head=envelope['audit_head'], store_id=self.store_id)

    def _safe_snapshot(self):
        try:
            return self._snapshot()
        except (OSError, RegistryError, UnicodeError):
            return None

    def current_snapshot(self):
        """Fresh read-only authority observation. Never opens private audit."""
        try:
            return self.locked(self._snapshot)
        except RegistryTransactionError:
            raise
        except (OSError, RegistryError, ValueError, TypeError, KeyError) as exc:
            raise RegistryTransactionError('registry_invalid' if self.document.exists() else 'registry_unavailable', str(exc)) from exc

    def _transaction_id(self, operation_id):
        text(operation_id, 'operation ID')
        return str(uuid.uuid5(uuid.NAMESPACE_URL, self.store_id + '\0' + operation_id))

    def _status(self, operation_id, intent_digest):
        op = self._transaction_id(operation_id)
        prepare = outcome = pending = None
        current = self._safe_snapshot()
        if self.audit.exists():
            self.audit.directories()
            self.audit.head()
            pending = self.audit.pending()
            prepare, outcome = self.audit.lookup(op)
            if pending and pending['op_id'] == op:
                if prepare is not None:
                    require(prepare == pending['prepare'], 'pending/final prepare mismatch')
                prepare = pending['prepare']
            require(prepare is None or outcome is not None or (pending and pending['op_id'] == op),
                    'orphan prepare without journal; preserve evidence')
        elif current is not None and current['format'] == document_codec.SPLIT_FORMAT:
            raise RegistryTransactionError('audit_unavailable', 'v3 audit storage missing; preserve current and reconcile explicitly',
                                           commit_state='unknown', operation_id=operation_id)
        if prepare and (prepare['intent_digest'] != intent_digest or prepare['operation_id'] != operation_id):
            raise RegistryTransactionError('operation_intent_mismatch', 'operation already bound to another intent', operation_id=operation_id)
        return dict(transaction_id=op, prepare=prepare, outcome=outcome,
                    result=prepare['result'] if prepare else None, current=current,
                    pending=bool(pending and pending['op_id'] == op))

    def operation_status(self, operation_id, intent_digest):
        def operation():
            try:
                return self._status(operation_id, intent_digest)
            except RegistryTransactionError:
                raise
            except (OSError, RegistryError, ValueError, TypeError, KeyError) as exc:
                raise RegistryTransactionError('audit_damaged', str(exc), commit_state='unknown', operation_id=operation_id) from exc
        try:
            return self.locked(operation)
        except RegistryTransactionError as exc:
            if exc.operation_id is None:
                exc.operation_id = exc.op_id = operation_id
            if exc.code == 'registry_unavailable':
                # Status cannot attest to an old operation without its lock/IO.
                exc.commit_state = 'unknown'
            raise

    def _transaction_ready(self):
        try:
            if self.switch_journal.exists():
                raise RegistryTransactionError('transaction_pending', 'pending legacy switch; run recover-switch', commit_state='unknown')
            control = self.control()
            require(not control or control['status'] == 'completed', 'pending initialization/cutover')
            # v3 managing paths only verify already-provisioned evidence. Legacy
            # callers skip this method until explicit migrate-storage provisions it.
            self.audit.directories()
            if self.audit.pending() is not None:
                raise RegistryTransactionError('transaction_pending', 'pending audit transaction; explicit recover-transaction required', commit_state='unknown')
            return self.audit.head()
        except RegistryTransactionError:
            raise
        except (RegistryError, OSError, ValueError, KeyError, TypeError) as exc:
            raise RegistryTransactionError('audit_damaged', str(exc)) from exc

    def _prepare_transaction(self, operation_id, intent_digest, intent, result, state, envelope, raw,
                             after_state, after_era, content, event, action, actor, reason, authority,
                             *, noop=False, references=None, head=None):
        op = self._transaction_id(operation_id)
        last = head['last_committed'] if head else None
        prior_sha = last['after_sha256'] if last else None
        references = copy.deepcopy(references or {})
        after_head = envelope['audit_head'] if noop else op
        blank = current_fields(empty())
        if (not noop and action == 'migrate-storage' and event is None and
                envelope['era'] == after_era == TEMPLATE_ERA and current_fields(state) == after_state == blank):
            # Format-only empty-template migration preserves era/revision and the
            # codec's no-head template invariant; the private receipt still commits.
            references['template_empty_current'] = blank
            after_head = None
        return dict(schema=1, op_id=op, operation_id=operation_id, intent_digest=intent_digest,
            intent=copy.deepcopy(intent), previous={k: head[k] for k in ('op_id', 'prepare_sha256', 'outcome_sha256')} if head else None,
            at=now(), actor=actor, authority=authority, action=action, reason=reason, disposition='noop' if noop else 'publication',
            base=dict(era=envelope['era'], revision=state['revision'], document_token=envelope['document_token'],
                      document_sha256=sha(raw), audit_head=envelope['audit_head']),
            after=dict(era=after_era, revision=after_state['revision'], document_sha256=sha(content.encode('utf-8')),
                       audit_head=after_head), event=event,
            continuity=dict(kind=('managed_match' if sha(raw) == prior_sha else 'unmanaged_change_observed') if last else 'unanchored',
                            previous_committed_sha256=prior_sha), references=references, result=copy.deepcopy(result))

    def _observed_raw(self):
        try:
            fd = os.open(self.document, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(fd, 'rb') as source:
                if not stat_module.S_ISREG(os.fstat(source.fileno()).st_mode):
                    return None
                return source.read()
        except OSError:
            return None

    def _outcome(self, prepare, status, basis, current):
        observed = self._observed_raw()
        observed_sha = sha(observed) if observed is not None else None
        return dict(schema=1, op_id=prepare['op_id'], intent_digest=prepare['intent_digest'], prepare_sha256=sha(encoded(prepare)),
                    status=status, basis=basis, recorded_at=now(),
                    observed=dict(document_sha256=observed_sha, era=current['era'] if current else None,
                                  revision=current['state']['revision'] if current else None, current_valid=current is not None),
                    document_token_after=current['document_token'] if current else None,
                    commit=dict(era=prepare['after']['era'], revision=prepare['after']['revision'],
                                after_sha256=prepare['after']['document_sha256']) if status == 'COMMITTED' else None)

    def _publish_transaction(self, prepare, content, raw, stat, before_publication=None):
        """One current replace, never rollback or replay after any uncertain error."""
        entered_publication = False
        outcome_durable = False
        try:
            self.audit.begin(prepare)
            if before_publication:
                before_publication()
            if prepare['disposition'] == 'publication':
                fd, temp = tempfile.mkstemp(prefix='.' + self.document.name + '-', dir=self.document.parent)
                try:
                    with os.fdopen(fd, 'wb') as out:
                        out.write(content.encode('utf-8'))
                        out.flush()
                        os.fsync(out.fileno())
                    self.transaction_checkpoint('current_temp_synced')
                    self.recheck(raw, stat)
                    self.transaction_checkpoint('current_before_replace')
                    entered_publication = True
                    os.replace(temp, self.document)
                    self.transaction_checkpoint('current_replaced')
                    sync_directory(self.document.parent)
                    self.transaction_checkpoint('current_synced')
                finally:
                    if os.path.exists(temp):
                        os.unlink(temp)
            current = self._safe_snapshot()
            status = 'NOT_APPLICABLE' if prepare['disposition'] == 'noop' else 'COMMITTED'
            outcome = self._outcome(prepare, status, 'locked_no_change' if status == 'NOT_APPLICABLE' else 'live_replace_synced', current)
            self.audit.write(self.audit.outcome_path(prepare['op_id']), outcome, immutable=True, phase='outcome')
            outcome_durable = True
            self._finalize_references(prepare, outcome)
            self.audit.finish(prepare, outcome)
            return self._status(prepare['operation_id'], prepare['intent_digest'])
        except Exception as exc:
            # Even a pre-publication failure may have left durable pending evidence.
            # Only explicit recovery terminalizes it; it never re-executes change().
            code = 'transaction_pending' if outcome_durable or not entered_publication else 'commit_unknown'
            commit_state = ('committed' if entered_publication else 'not_committed') if outcome_durable else ('unknown' if entered_publication else 'not_committed')
            raise RegistryTransactionError(code, 'transaction requires explicit metadata recovery: ' + str(exc),
                commit_state=commit_state, retryable=False,
                operation_id=prepare['operation_id']) from exc

    def transact_current(self, operation_id, intent_digest, intent, expected_document, change, *,
                         action, actor, reason, authority, persist_noop=False):
        text(intent_digest, 'intent digest')
        encoded(intent)  # refuse non-JSON/NaN before any effect
        for label, value in [('action', action), ('actor', actor), ('reason', reason)]:
            text(value, label)
        require(authority is None or isinstance(authority, str), 'authority must be string or null')
        def operation():
            try:
                status = self._status(operation_id, intent_digest)
                if status['prepare'] is not None:
                    return status  # lookup precedes stale CAS and callback/domain conditions
            except RegistryTransactionError:
                raise
            except (RegistryError, OSError, ValueError, KeyError, TypeError) as exc:
                # Damaged lookup cannot attest whether this operation committed.
                raise RegistryTransactionError('audit_damaged', str(exc), commit_state='unknown', operation_id=operation_id) from exc
            try:
                state, envelope, raw, stat = self.read_document()
            except (RegistryError, OSError, UnicodeError) as exc:
                raise RegistryTransactionError('registry_invalid' if self.document.exists() else 'registry_unavailable', str(exc), operation_id=operation_id) from exc
            if envelope['storage_format'] != document_codec.SPLIT_FORMAT:
                raise RegistryTransactionError('storage_not_migrated', 'explicit migrate-storage required before current transactions', operation_id=operation_id)
            head = self._transaction_ready()
            if expected_document != envelope['document_token']:
                raise RegistryTransactionError('document_conflict', 'stale expected document; query and decide deliberately', retryable=True, operation_id=operation_id)
            before = state  # fresh owned read; callback receives only its independent copy
            changed = copy.deepcopy(state)
            result = change(changed, envelope['era'])
            require(isinstance(result, dict), 'transaction callback must return a JSON object')
            encoded(result)
            require(set(changed) == set(before) and changed['version'] == before['version'] and changed['revision'] == before['revision'],
                    'callback cannot change version/revision/current envelope')
            validate_current(changed)
            noop = changed == before
            if noop and not persist_noop:
                return dict(transaction_id=self._transaction_id(operation_id), prepare=None, outcome=None,
                            result=result, current=self._snapshot(), pending=False)
            era = str(uuid.uuid4()) if envelope['era'] == TEMPLATE_ERA and not noop else envelope['era']
            require(envelope['era'] != TEMPLATE_ERA or noop or action == 'register', 'blank template first mutation must be registration')
            event = None
            if not noop:
                changed['revision'] += 1
                event = changed_event(before, changed, action, actor, reason, authority,
                                      getattr(change, 'event_args', None), stamp=getattr(change, 'event_stamp', None))
                document_codec.validate_event(event, changed['revision'])
            op = self._transaction_id(operation_id)
            content = raw.decode('utf-8') if noop else document_codec.serialize_current_document(changed, era, op)
            if not noop:
                require(document_codec.parse_store_document(content)[:2] == (changed, era), 'current serialization mismatch')
            prepare = self._prepare_transaction(operation_id, intent_digest, intent, result, before, envelope, raw,
                changed, era, content, event, action, actor, reason, authority, noop=noop, head=head)
            return self._publish_transaction(prepare, content, raw, stat)
        return self.locked(operation)

    def recover_transaction(self, actor, reason, authority, expected_document=None):
        """Metadata-only explicit reconciliation. Never publishes prepared current."""
        self.switch_authority(actor, reason, authority)
        def operation():
            require(not self.switch_journal.exists(), 'legacy switch pending; recover-switch first')
            try:
                self.audit.directories()
                self.audit.head()  # Recovery never provisions a lost head or directory.
                pending = self.audit.pending()
                require(pending is not None, 'no pending transaction')
                prepare = pending['prepare']
                current = self._safe_snapshot()
                if expected_document is not None:
                    require(current is not None and expected_document == current['document_token'], 'stale expected document before recovery')
                existing, outcome = self.audit.lookup(prepare['op_id'])
                require(existing is None or existing == prepare, 'journal/final prepare conflict')
                # Journal-first payload preserves a missing final, never reconstructs current.
                self.audit.write(self.audit.prepare_path(prepare['op_id']), prepare, immutable=True, phase='prepare')
                if outcome is None:
                    observed = self._observed_raw()
                    exact = current is not None and observed is not None and sha(observed) == prepare['after']['document_sha256']
                    exact = exact and current['era'] == prepare['after']['era'] and current['state']['revision'] == prepare['after']['revision']
                    if prepare['disposition'] == 'noop' and exact:
                        status, basis = 'NOT_APPLICABLE', 'recovered_exact_noop_observation'
                    elif exact and current['audit_head'] == prepare['after']['audit_head']:
                        sync_directory(self.document.parent)
                        status, basis = 'COMMITTED', 'recovered_exact_after'
                    else:
                        status, basis = 'PUBLICATION_UNKNOWN', 'recovery_current_not_exact_after'
                    outcome = self._outcome(prepare, status, basis, current)
                self._finalize_references(prepare, outcome)
                self.audit.finish(prepare, outcome)
                return self._status(prepare['operation_id'], prepare['intent_digest'])
            except RegistryTransactionError:
                raise
            except (OSError, RegistryError, ValueError, TypeError, KeyError) as exc:
                raise RegistryTransactionError('audit_damaged', str(exc), commit_state='unknown') from exc
        return self.locked(operation)

    def _finalize_references(self, prepare, outcome):
        record = prepare['references'].get('backup_record')
        if record is None:
            return
        record = copy.deepcopy(record)
        status = outcome['status']
        record['status'] = 'completed' if status == 'COMMITTED' else ('aborted' if status == 'ABORTED' else 'superseded-manual')
        if status not in ('COMMITTED', 'ABORTED'):
            record['reconciliation'] = dict(observed_sha256=outcome['observed']['document_sha256'], journal_phase=status)
        records = self.read_backup_index()
        prior = next((value for value in records if value['id'] == record['id']), None)
        if prior is not None:
            require(all(prior[key] == value for key, value in record.items() if key not in ('status', 'reconciliation')), 'backup index/transaction mismatch')
            records[records.index(prior)] = record
        else:
            records.append(record)
        self.write_backup_index(records)

    def _read_legacy_archive(self, prepare, manifest=None):
        """Read exact v2 evidence bound to its known migration source anchor.

        Checkpoints supply their embedded manifest; it has the same source
        constraint as the standalone manifest, never a newly minted authority.
        """
        try:
            self.audit.validate_prepare(prepare)
            archive = identifier(prepare['references']['legacy_archive'])
            require(prepare['action'] == 'migrate-storage' and prepare['disposition'] == 'publication' and
                    archive == prepare['op_id'], 'legacy archive source is not its migration PREPARE')
            if manifest is None:
                manifest = self.audit.read(self.audit.legacy / (archive + '.manifest.json'))
            keys(manifest, ['schema', 'archive_id', 'sha256', 'bytes', 'era', 'revision', 'format', 'document'])
            base, after = prepare['base'], prepare['after']
            require(manifest['schema'] == 1 and manifest['archive_id'] == archive and manifest['format'] == FORMAT and
                    manifest['era'] == base['era'] == after['era'] and
                    type(manifest['revision']) is int and manifest['revision'] == base['revision'] == after['revision'],
                    'legacy manifest metadata contradicts migration source')
            raw = read_private(self.audit.legacy / (archive + '.md'))
            require(sha(raw) == manifest['sha256'] == base['document_sha256'] and
                    type(manifest['bytes']) is int and len(raw) == manifest['bytes'],
                    'legacy archive digest/bytes contradict migration source')
            legacy, era = parse_document(raw.decode('utf-8'))
            require(era == base['era'] and legacy['revision'] == base['revision'],
                    'legacy document metadata contradicts migration source')
            return legacy, manifest
        except (OSError, RegistryError, ValueError, TypeError, KeyError) as exc:
            raise RegistryTransactionError('audit_damaged', 'legacy archive source evidence damaged: ' + str(exc),
                                           commit_state='unknown') from exc

    def _history_at(self, op):
        """Hydrate the explicitly requested logical history, never current authority."""
        self.audit.directories()
        self.audit.head()  # Audit-Head none cannot conceal lost private attempts.
        events, seen = [], set()
        while op is not None:
            require(op not in seen, 'cyclic current audit ancestry')
            seen.add(op)
            prepare, outcome = self.audit.lookup(op)
            require(prepare is not None and outcome is not None, 'current history locator has incomplete evidence')
            if outcome['status'] != 'COMMITTED':
                # A manual edit may retain an UNKNOWN locator. Preserve the
                # attempt in explicit audit, never count its delta as committed.
                op = prepare['base']['audit_head']
                continue
            refs = prepare['references']
            if prepare['event'] is not None:
                events.append(prepare['event'])
            if 'history_checkpoint' in refs:
                checkpoint = self._read_checkpoint(refs['history_checkpoint'], refs['history_checkpoint_sha256'])
                return checkpoint['history'] + list(reversed(events))
            if refs.get('history_reset'):
                return list(reversed(events))
            if 'legacy_archive' in refs:
                legacy, _ = self._read_legacy_archive(prepare)
                return legacy['history'] + list(reversed(events))
            op = prepare['base']['audit_head']
        return list(reversed(events))

    def audit_history_at(self, transaction_uuid, *, include_history=True):
        """Select private historical evidence without opening or repairing current.

        Only a terminal COMMITTED publication identifies logical history. Other
        attempts can be inspected with include_history=False, never as authority.
        """
        try:
            identifier(transaction_uuid)
        except (RegistryError, ValueError, TypeError) as exc:
            raise RegistryTransactionError('invalid_transaction', 'transaction selector requires a canonical UUID') from exc
        def operation():
            try:
                self.audit.directories()
                self.audit.head()
                pending = self.audit.pending()
                prepare, outcome = self.audit.lookup(transaction_uuid)
                own_pending = pending is not None and pending['op_id'] == transaction_uuid
                if own_pending:
                    require(prepare is None or prepare == pending['prepare'], 'pending/final prepare mismatch')
                    prepare = pending['prepare']
                if prepare is None:
                    raise RegistryTransactionError('transaction_not_found', 'no evidence for selected transaction UUID')
                require(outcome is not None or own_pending, 'orphan selected prepare without journal; preserve evidence')
                committed = outcome is not None and outcome['status'] == 'COMMITTED' and prepare['disposition'] == 'publication'
                if include_history and not committed:
                    raise RegistryTransactionError('history_unavailable',
                        'selected transaction is not a terminal COMMITTED publication; inspect without --history',
                        commit_state='unknown', operation_id=prepare['operation_id'])
                result = dict(selected=dict(transaction_id=transaction_uuid, operation_id=prepare['operation_id'],
                                            status=outcome['status'] if outcome else None, disposition=prepare['disposition']),
                              source_era=prepare['after']['era'] if committed else None,
                              revision=prepare['after']['revision'] if committed else None,
                              history_loaded=include_history, current_authority=False,
                              prepare=prepare, outcome=outcome, pending=own_pending)
                if include_history:
                    result['history'] = self._history_at(transaction_uuid)
                return result
            except RegistryTransactionError:
                raise
            except (OSError, RegistryError, ValueError, TypeError, KeyError) as exc:
                raise RegistryTransactionError('audit_damaged', 'selected transaction evidence damaged: ' + str(exc),
                                               commit_state='unknown') from exc
        return self.locked(operation)

    def history_snapshot(self):
        def operation():
            state, envelope, _, _ = self.read_document()
            self.envelope = envelope
            if 'history' not in state:
                state = dict(state, history=self._history_at(envelope['audit_head']))
            return state
        return self.locked(operation)

    def query_current(self, args, include_history=False):
        def operation():
            state, envelope, _, _ = self.read_document()
            self.envelope = envelope
            if include_history:
                if 'history' not in state:
                    state = dict(state, history=self._history_at(envelope['audit_head']))
            else:
                state = current_fields(state)
            result = dict(query(state, args), **envelope)
            if include_history and self.audit.exists():
                result['audit_attempts'] = self.audit.history()
            return result
        return self.locked(operation)

    def mutate_cli(self, args, payload):
        snapshot = self.current_snapshot()
        if snapshot['format'] == FORMAT:
            return self.run(lambda state: mutate(state, args, payload), expected_document=args.expected_document)
        operation_id = getattr(args, 'operation_id', None) or str(uuid.uuid4())
        intent = dict(command=args.command, arguments={k: v for k, v in vars(args).items()
                      if k not in ('root', 'document', 'input', 'expected_document', 'operation_id')}, payload=payload)
        digest = sha(encoded(intent))
        def change(state, era):
            change.event_stamp = now()
            return mutate_records(state, args, payload, stamp=change.event_stamp)
        change.event_args = args
        reason = payload['appointment'] if args.command == 'register' else args.reason
        status = self.transact_current(operation_id, digest, intent, args.expected_document, change,
            action=args.command, actor=args.actor, reason=reason, authority=args.authority)
        if status['pending']:
            raise RegistryTransactionError('transaction_pending', 'operation pending; explicit recover-transaction required', commit_state='unknown', operation_id=operation_id)
        if status['outcome'] and status['outcome']['status'] not in ('COMMITTED', 'NOT_APPLICABLE'):
            raise RegistryTransactionError('commit_unknown', 'operation did not obtain a committed outcome', commit_state='unknown', operation_id=operation_id)
        result = dict(status['result'], operation_id=operation_id, transaction_id=status['transaction_id'])
        current = status['current']
        if current:
            result.update(era=current['era'], revision=current['state']['revision'], document_token=current['document_token'],
                          storage_format=current['format'], audit_head=current['audit_head'])
        return result

    def migrate_storage(self, expected_document, actor, reason, authority, operation_id=None):
        self.switch_authority(actor, reason, authority)
        operation_id = operation_id or str(uuid.uuid4())
        intent = dict(action='migrate-storage', store_id=self.store_id, actor=actor, reason=reason, authority=authority)
        digest = sha(encoded(intent))
        def operation():
            status = self._status(operation_id, digest)
            if status['prepare'] is not None:
                return status
            state, envelope, raw, stat = self.read_document()
            require(envelope['storage_format'] == FORMAT, 'migrate-storage requires legacy v2; no implicit remigration')
            if expected_document != envelope['document_token']:
                raise RegistryTransactionError('document_conflict', 'stale expected document before storage migration', retryable=True, operation_id=operation_id)
            # Only validated, explicitly authorized legacy migration may create a
            # new audit store. Existing audit damage is never re-provisioned.
            if not self.audit.exists():
                if self.switch_journal.exists():
                    raise RegistryTransactionError('transaction_pending', 'pending legacy switch; run recover-switch', commit_state='unknown')
                control = self.control()
                require(not control or control['status'] == 'completed', 'pending initialization/cutover')
                self.audit.provision()
            head = self._transaction_ready()
            op = self._transaction_id(operation_id)
            # Exact original bytes, not a reconstructed v2 serialization.
            manifest = dict(schema=1, archive_id=op, sha256=sha(raw), bytes=len(raw), era=envelope['era'],
                            revision=state['revision'], format=FORMAT, document=str(self.document))
            self.audit.archive(op, raw, manifest)
            desired = current_fields(state)
            content = document_codec.serialize_current_document(desired, envelope['era'],
                                                                None if envelope['era'] == TEMPLATE_ERA else op)
            prepare = self._prepare_transaction(operation_id, digest, intent, dict(migrated=True, legacy_archive=op),
                desired, envelope, raw, desired, envelope['era'], content, None, 'migrate-storage', actor, reason, authority,
                references=dict(legacy_archive=op), head=head)
            return self._publish_transaction(prepare, content, raw, stat)
        return self.locked(operation)

    def control(self):
        path = self.root / 'document-control.json'
        require(not path.is_symlink(), 'document control cannot be symlink')
        if not path.exists():
            return None
        control = parse_json(read_private(path).decode('utf-8'))
        keys(control, ['kind', 'status', 'document'],
             ['migration_id', 'source_sha256', 'backup', 'staged', 'staged_sha256', 'era', 'dashboard_backup', 'dashboard_sha256'])
        require(control['kind'] in ['initialization', 'migration'] and control['status'] in ['pending', 'completed'],
                'invalid document control marker; preserve and reconcile explicitly')
        require(control['document'] == str(self.document), 'document control path mismatch')
        if control['kind'] == 'migration':
            require(all(k in control for k in ['migration_id', 'source_sha256', 'backup', 'staged', 'staged_sha256', 'era', 'dashboard_backup', 'dashboard_sha256']),
                    'incomplete migration control marker')
        return control

    def run(self, fn, expected_document=None):
        def operation():
            state, envelope, raw, stat = self.read_document()
            self.envelope = envelope
            control = self.control()
            envelope['cutover_status'] = control['status'] if control else 'unmanaged'
            if expected_document is not None:
                require(not self.switch_journal.exists(), 'pending switch; run recover-switch before mutation')
                require(not control or control.get('status') == 'completed',
                        'pending initialization/cutover; explicit recovery required before mutation')
                require(expected_document == envelope['document_token'],
                        'stale expected document; query current Markdown and decide again deliberately')
            if expected_document is not None and self.audit.exists():
                self._transaction_ready()
            original = copy.deepcopy(state)
            result = fn(state)
            split = envelope['storage_format'] == 'responsibility-registry/v3'
            (validate_current if split else validate)(state)
            if state != original:
                require(not split, 'v3 managed mutations require transact_current, not legacy full-state run')
                require(expected_document is not None, 'every mutation requires --expected-document from current query')
                era = str(uuid.uuid4()) if envelope['era'] == TEMPLATE_ERA else envelope['era']
                require(envelope['era'] != TEMPLATE_ERA or
                        (state['history'] and state['history'][0]['action'] == 'register'),
                        'first mutation of blank template must be a genuine registration')
                content = serialize_document(state, era)
                require(parse_document(content) == (state, era), 'post-serialization state mismatch')
                # Cooperative CLI writers serialize on flock. Non-locking editors can still
                # race the final check-to-rename interval; POSIX has no conditional rename.
                atomic_write(self.document, content, before_replace=lambda: self.recheck(raw, stat))
                check, current, _, _ = self.read_document()
                require(check == state and current['era'] == era, 'post-write validation mismatch')
                self.envelope = current
            if isinstance(result, dict):
                result.update(self.envelope)
            return result
        return self.locked(operation)

    def recheck(self, raw, stat):
        require(not self.document.is_symlink(), 'document replaced before commit; refuse stale operation')
        try:
            fd = os.open(self.document, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(fd, 'rb') as source:
                require(stat_module.S_ISREG(os.fstat(source.fileno()).st_mode), 'live document must be regular')
                current = source.read()
                identity = fingerprint(os.fstat(source.fileno()))
            require(raw == current and stat == identity == fingerprint(self.document.stat()),
                    'document changed/replaced before commit; refuse stale operation')
        except FileNotFoundError as exc:
            raise RegistryError('document missing before commit; refuse stale operation') from exc

    def initialize(self):
        def operation():
            if self.document.exists() or self.document.is_symlink():
                # Existing bytes/inode are never rewritten, even if invalid.
                if self.control() is None:
                    atomic_write(self.root / 'document-control.json', json.dumps(dict(
                        kind='initialization', status='completed', document=str(self.document))) + '\n')
                return dict(initialized=False, note='existing live document preserved')
            require(self.control() is None, 'document missing after prior initialization/cutover; explicit recovery required')
            require(not any((self.root / f).exists() for f in ('registry.json', 'dashboard.md')),
                    'legacy evidence exists; explicit migrate required, no empty initialization')
            require(not self.audit.exists(), 'audit evidence exists; explicit reconciliation required, no empty initialization')
            self.document.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            control = dict(kind='initialization', status='pending', document=str(self.document))
            atomic_write(self.root / 'document-control.json', json.dumps(control) + '\n')
            self.audit.provision()
            exclusive_file(self.document, document_codec.serialize_current_document(current_fields(empty()), TEMPLATE_ERA))
            control['status'] = 'completed'
            atomic_write(self.root / 'document-control.json', json.dumps(control) + '\n')
            return dict(initialized=True, era=TEMPLATE_ERA, revision=0)
        return self.locked(operation, create=True)

    def recover_initialization(self):
        def operation():
            control = self.control()
            require(control and control.get('kind') == 'initialization' and control.get('status') == 'pending',
                    'no pending initialization')
            require(control['document'] == str(self.document), 'initialization document mismatch')
            content = document_codec.serialize_current_document(current_fields(empty()), TEMPLATE_ERA).encode()
            if self.document.exists():
                raw = self.document.read_bytes()
                require(raw in [content, serialize_document(empty(), TEMPLATE_ERA).encode()], 'pending initialization diverged; preserve and reconcile explicitly')
                if raw == content:
                    self.audit.provision()
            else:
                self.audit.provision()
                exclusive_file(self.document, content)
            control['status'] = 'completed'
            atomic_write(self.root / 'document-control.json', json.dumps(control) + '\n')
            return dict(initialized=True)
        return self.locked(operation)

    def migrate(self, resume=False):
        """Explicit two-phase cutover, never called from query/init/reinstall."""
        def operation():
            marker = self.root / 'document-control.json'
            legacy = self.root / 'registry.json'
            control = self.control()
            if control:
                require(resume and control.get('kind') == 'migration' and control.get('status') == 'pending',
                        'prior initialization/completed cutover exists; migration forbidden')
                require(control['document'] == str(self.document), 'cutover document path mismatch')
                raw = legacy.read_bytes()
                current = parse_json(raw.decode('utf-8'))
                fenced = current.get('migration_id') == control['migration_id'] and current.get('format') == FORMAT
                if not fenced:
                    # A crash before fence may have allowed old writers to advance. Keep
                    # old evidence, prepare a fresh locked snapshot rather than replay it.
                    validate(current)
                    require(not self.document.exists() and not self.document.is_symlink(), 'unexpected live document; preserve and reconcile explicitly')
                    control = None
            else:
                require(not resume, 'no pending cutover to resume')
                require(not self.document.exists() and not self.document.is_symlink(),
                        'live document exists; migration never overwrites it')
            if control is None:
                raw = legacy.read_bytes()
                state = parse_json(raw.decode('utf-8'))
                validate(state)
                normalized = copy.deepcopy(state)
                normalized.setdefault('coordination', {})
                readiness = {cid: agreement_view(state, a, legacy=True)['agreement_ready']
                             for cid, a in state.get('coordination', {}).items()}
                for a in normalized['coordination'].values():
                    for aid, boundary in a['scopes'].items():
                        assignment = state['assignments'][aid]
                        matching = (assignment['status'] == 'active' and assignment['owner'] == boundary['owner'] and
                                    assignment['updated_at'] == boundary['assignment_updated_at'] and assignment.get('repo') == a['repo'])
                        boundary['assignment_digest'] = assignment_digest(assignment) if matching else None
                for agreement in normalized['coordination'].values():
                    digest = terms_digest(agreement)
                    for ack in agreement['acknowledgements'].values():
                        ack['terms_digest'] = digest
                era = str(uuid.uuid4())
                staged = serialize_document(normalized, era)
                parsed, parsed_era = parse_document(staged)
                require(parsed == normalized and parsed_era == era, 'migration full-state roundtrip mismatch')
                require(readiness == {cid: agreement_view(parsed, a)['agreement_ready'] for cid, a in parsed['coordination'].items()},
                        'migration changed agreement readiness')
                migration_id = str(uuid.uuid4())
                backup = self.root / ('legacy-' + migration_id + '.json')
                exclusive_file(backup, raw)
                self.document.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                stage = self.document.parent / ('.responsibilities-' + migration_id + '.md')
                exclusive_file(stage, staged)
                require(backup.read_bytes() == raw and parse_document(stage.read_text(encoding='utf-8')) == (normalized, era),
                        'durable migration staging readback failed')
                control = dict(kind='migration', status='pending', migration_id=migration_id, document=str(self.document),
                               source_sha256=hashlib.sha256(raw).hexdigest(), backup=str(backup), staged=str(stage),
                               staged_sha256=hashlib.sha256(staged.encode()).hexdigest(), era=era,
                               dashboard_backup=str(self.root / ('legacy-' + migration_id + '.dashboard.md')),
                               dashboard_sha256=hashlib.sha256((self.root / 'dashboard.md').read_bytes()).hexdigest() if (self.root / 'dashboard.md').exists() else None)
                atomic_write(marker, json.dumps(control, indent=2) + '\n')
                self.migration_checkpoint('prepared')
                require(hashlib.sha256(legacy.read_bytes()).hexdigest() == control['source_sha256'],
                        'legacy changed outside lock; explicit resume from fresh state')
                tombstone = dict(format=FORMAT, migration_id=migration_id, backup=str(backup),
                                 note='Markdown cutover; deliberately invalid legacy state. Never restore as active JSON.')
                atomic_write(legacy, json.dumps(tombstone) + '\n')
                self.migration_checkpoint('fenced')
            self.retire_dashboard(control)
            if not self.backup_index.exists():
                self.write_backup_index([])
            backup = Path(control['backup'])
            require(hashlib.sha256(backup.read_bytes()).hexdigest() == control['source_sha256'], 'migration backup digest mismatch')
            validate(parse_json(backup.read_text(encoding='utf-8')))
            stage = Path(control['staged'])
            staged = stage.read_bytes()
            require(hashlib.sha256(staged).hexdigest() == control['staged_sha256'], 'migration staged digest mismatch; published document may have diverged; preserve and reconcile explicitly')
            parse_document(staged.decode('utf-8'))
            if self.document.exists() or self.document.is_symlink():
                # Only our hard-link publication is resumable, not a same-content user copy.
                require(not self.document.is_symlink() and os.path.samefile(stage, self.document) and
                        self.document.read_bytes() == staged, 'published Markdown diverged/unexpected document; preserve and reconcile explicitly')
            else:
                os.link(stage, self.document)  # exclusive publication; never overwrite human edits
                fsync_directory(self.document.parent)
            require(self.document.read_bytes() == staged, 'published readback mismatch')
            self.migration_checkpoint('published')
            control['status'] = 'completed'
            atomic_write(marker, json.dumps(control, indent=2) + '\n')
            stage.unlink()
            fsync_directory(stage.parent)
            return dict(migrated=True, era=control['era'], backup=control['backup'],
                        revision=parse_document(staged.decode('utf-8'))[0]['revision'])
        return self.locked(operation, create=True)

    def retire_dashboard(self, control):
        path = self.root / 'dashboard.md'
        tombstone = ('# Superseded responsibility report\n\n'
                     'This path contains no current authority. Reread ' + str(self.document) +
                     ' for current owners and relations. Never restore roles from old reports or memory.\n')
        expected = control.get('dashboard_sha256')
        backup = Path(control.get('dashboard_backup') or self.root / ('legacy-' + control['migration_id'] + '.dashboard.md'))
        if expected is not None:
            if not backup.exists():
                raw = path.read_bytes()
                require(hashlib.sha256(raw).hexdigest() == expected, 'legacy dashboard diverged; preserve and reconcile')
                exclusive_file(backup, raw)
            require(hashlib.sha256(backup.read_bytes()).hexdigest() == expected, 'legacy dashboard backup digest mismatch')
        if path.exists():
            raw = path.read_bytes()
            require(raw == tombstone.encode() or (expected is not None and hashlib.sha256(raw).hexdigest() == expected),
                    'legacy dashboard diverged; preserve and reconcile')
        atomic_write(path, tombstone)

    def migration_checkpoint(self, phase):
        """Deterministic crash-injection seam for isolated tests; no runtime switch."""

    @property
    def backup_index(self):
        return self.document.parent / 'BACKUPS.md'

    @property
    def switch_journal(self):
        return self.root / 'switch-pending.json'

    def read_backup_index(self):
        if not self.backup_index.exists():
            return []
        require(not self.backup_index.is_symlink(), 'backup index cannot be a symlink')
        content = self.backup_index.read_text(encoding='utf-8')
        marker = '## Switch records (audit JSON)\n```json\n'
        require(content.count(marker) == 1 and content.endswith('\n```\n'), 'invalid backup index; preserve and reconcile')
        records = parse_json(content.split(marker, 1)[1][:-5])
        require(isinstance(records, list), 'backup index records must be an array')
        seen = set()
        for record in records:
            keys(record, ['id', 'sequence', 'time', 'action', 'actor', 'reason', 'authority', 'snapshot', 'sha256',
                          'status', 'summary', 'before_token', 'after_sha256', 'after_era', 'target_id', 'readiness_changes'],
                 ['reconciliation', 'storage_format', 'audit_checkpoint', 'audit_checkpoint_sha256'])
            require(record['id'] not in seen and type(record['sequence']) is int and record['sequence'] > 0,
                    'duplicate/invalid backup ID/sequence')
            seen.add(record['id'])
            require(record['status'] in ['pending', 'completed', 'aborted', 'switched-index-incomplete', 'superseded-manual'], 'invalid backup status')
            require(record['action'] in ['clear', 'restore'] and record['actor'] == 'user', 'invalid switch action/actor')
            if 'reconciliation' in record:
                keys(record['reconciliation'], ['observed_sha256', 'journal_phase'])
                observed = record['reconciliation']['observed_sha256']
                require((observed is None and record.get('storage_format') == 'responsibility-registry/v3') or
                        (isinstance(observed, str) and re.fullmatch(r'[0-9a-f]{64}', observed)), 'invalid observed digest')
                text(record['reconciliation']['journal_phase'], 'reconciled journal phase')
            validate_time(dict(at=record['time'], precision='exact'))
            for field in ['id', 'reason', 'authority', 'snapshot', 'sha256', 'after_sha256', 'after_era']:
                text(record[field], 'backup.' + field)
        return records

    def write_backup_index(self, records):
        lines = ['# Responsibility relationship backups', '',
                 'Historical switch index, NOT current authority. Read RESPONSIBILITIES.md for current relations.',
                 'Only completed, hash-verified Markdown snapshots are restore candidates. Restoring does not restart agents/jobs.', '']
        for record in records:
            summary = record['summary']
            lines += ['## ' + record['time'] + ' · ' + record['action'] + ' · ' + record['status'],
                      '- ID: ' + record['id'] + ' · sequence: ' + str(record['sequence']),
                      '- Reason: ' + cell(record['reason']) + ' · user authority: ' + cell(record['authority']),
                      '- Prior era/revision: ' + summary['era'] + ' / ' + str(summary['revision']),
                      '- Restored from: ' + (record['target_id'] or '(none; clear)') + ' · new era: ' + record['after_era'],
                      '- Counts: owners=' + str(summary['owners']) + ', assignments=' + str(summary['assignments']) +
                      ', coordination=' + str(summary['coordination']) + ', history=' + str(summary['history']),
                      '- Roots: ' + (', '.join(cell(x) for x in summary['roots']) or '(none)'),
                      '- Relationships (parent → child / role): ' + ('; '.join(cell(x) for x in summary['relations']) or '(none)'),
                      '- Snapshot: [' + record['id'] + '](' + record['snapshot'] + ')',
                      '- SHA-256: `' + record['sha256'] + '`']
            if 'reconciliation' in record:
                evidence = record['reconciliation']
                lines.append('- Reconciled phase: ' + evidence['journal_phase'] +
                             ' · observed current SHA-256: `' + (evidence['observed_sha256'] or 'unavailable') + '`')
            lines.append('')
        lines += ['## Switch records (audit JSON)', '```json', json.dumps(records, ensure_ascii=False, indent=2, allow_nan=False), '```', '']
        require(not self.backup_index.is_symlink(), 'backup index cannot be a symlink')
        atomic_write(self.backup_index, '\n'.join(lines))

    def snapshot_bytes(self, record):
        name = record['snapshot']
        require(isinstance(name, str) and name.startswith('backups/') and
                Path(name).name.endswith('.md') and name == 'backups/' + Path(name).name and Path(name).name not in ['.', '..'],
                'restore target must be a known backups/*.md snapshot, never legacy JSON')
        directory = self.document.parent / 'backups'
        path = self.document.parent / name
        require(not directory.is_symlink() and not path.is_symlink() and path.is_file(), 'snapshot missing or symlink; preserve current')
        raw = path.read_bytes()
        require(hashlib.sha256(raw).hexdigest() == record['sha256'], 'snapshot hash mismatch; preserve current')
        if record.get('storage_format') == 'responsibility-registry/v3':
            state, era, _ = document_codec.parse_store_document(raw.decode('utf-8'))
            checkpoint = self._read_checkpoint(record['audit_checkpoint'], record['audit_checkpoint_sha256'])
            require(checkpoint['current_sha256'] == sha(raw), 'backup audit checkpoint mismatch')
            require(checkpoint['era'] == era and checkpoint['revision'] == state['revision'], 'backup checkpoint current mismatch')
        else:
            state, era = parse_document(raw.decode('utf-8'))
        return raw, state, era

    def backup_candidates(self):
        return [r for r in self.read_backup_index() if r['status'] == 'completed']

    def resolve_backup(self, target):
        history = self.read_backup_index()
        records = [r for r in history if r['status'] == 'completed']
        superseded = [r for r in history if r['status'] == 'superseded-manual']
        def candidates(values):
            return json.dumps([dict(id=r['id'], status=r['status'], utc=datetime.fromisoformat(r['time'].replace('Z', '+00:00')).astimezone(timezone.utc).isoformat(),
                                    local=datetime.fromisoformat(r['time'].replace('Z', '+00:00')).astimezone().isoformat(),
                                    snapshot=r['snapshot'], summary=r['summary']) for r in values], ensure_ascii=False)
        if target == 'previous':
            latest = max(records, key=lambda r: r['sequence'], default=None)
            newer = [r for r in superseded if r['sequence'] > (latest['sequence'] if latest else 0)]
            require(not newer, 'previous is ambiguous after a newer superseded switch; ask user to choose exact ID/filename: ' + candidates(([latest] if latest else []) + newer))
            matches = [latest] if latest else []  # resolved BEFORE the current backup exists
        else:
            matches = [r for r in records + superseded if target in [r['id'], r['snapshot'], Path(r['snapshot']).name]]
            if not matches:
                timestamp = re.fullmatch(r'\d{4}-\d{2}-\d{2}[T ](?P<clock>\d{2}:\d{2}(?::\d{2})?)(?:Z|[+-]\d{2}(?::?\d{2})?)?', target)
                require(timestamp, 'no completed Markdown snapshot matches; timestamp requires minute or second precision and explicit timezone; candidates: ' + candidates(records))
                try:
                    start = datetime.fromisoformat(target.replace('Z', '+00:00'))
                except ValueError as exc:
                    raise RegistryError('invalid ISO timestamp; candidates: ' + candidates(records)) from exc
                require(start.tzinfo is not None and start.utcoffset() is not None,
                        'timestamp requires explicit timezone; ask user, never assume UTC; candidates: ' + candidates(records))
                start = start.astimezone(timezone.utc)
                end = start + timedelta(seconds=1 if timestamp['clock'].count(':') == 2 else 60)
                matches = [r for r in records if start <= datetime.fromisoformat(r['time'].replace('Z', '+00:00')).astimezone(timezone.utc) < end]
        require(matches, 'no completed Markdown snapshot matches; legacy JSON/dashboard are never candidates')
        require(len(matches) == 1, 'ambiguous restore target; ask user to select: ' + candidates(matches))
        self.snapshot_bytes(matches[0])
        return matches[0]

    def switch_authority(self, actor, reason, authority):
        require(actor == 'user', 'global clear/restore/recovery requires actual direct user authority, not a role-bound peer')
        text(reason, 'user switch reason')
        text(authority, 'direct user authorization evidence')

    def _read_checkpoint(self, checkpoint_id, expected_sha256):
        checkpoint = self.audit.read(self.audit.checkpoints / (identifier(checkpoint_id) + '.json'))
        require(checkpoint.get('schema') == 1 and checkpoint.get('checkpoint_id') == checkpoint_id and
                sha(encoded(checkpoint)) == expected_sha256, 'immutable audit checkpoint hash/schema mismatch')
        require(isinstance(checkpoint.get('history'), list) and isinstance(checkpoint.get('attempts'), list), 'invalid checkpoint evidence containers')
        for event in checkpoint['history']:
            document_codec.validate_event(event, event['revision'])
        for attempt in checkpoint['attempts']:
            self.audit.validate_prepare(attempt['prepare'])
            self.audit.validate_outcome(attempt['outcome'], attempt['prepare'])
        archives = checkpoint['legacy_archives']
        require(isinstance(archives, dict), 'invalid checkpoint legacy source manifests')
        sources = [attempt['prepare'] for attempt in checkpoint['attempts']
                   if 'legacy_archive' in attempt['prepare']['references']]
        require(set(archives) == {source['references']['legacy_archive'] for source in sources},
                'checkpoint legacy source anchor coverage mismatch')
        for source in sources:
            self._read_legacy_archive(source, archives[source['references']['legacy_archive']])
        return checkpoint

    def _write_checkpoint(self, checkpoint_id, raw, state, envelope, history=None):
        self.audit.directories()
        attempts = self.audit.history()
        archives = {}
        for attempt in attempts:
            archive = attempt['prepare']['references'].get('legacy_archive')
            if archive:
                _, manifest = self._read_legacy_archive(attempt['prepare'])
                archives[archive] = manifest
        value = dict(schema=1, checkpoint_id=checkpoint_id, current_sha256=sha(raw), era=envelope['era'],
                     revision=state['revision'], audit_head=envelope.get('audit_head'), head=self.audit.head(),
                     history=self._history_at(envelope.get('audit_head')) if history is None else history,
                     attempts=attempts, legacy_archives=archives)
        self.audit.write(self.audit.checkpoints / (identifier(checkpoint_id) + '.json'), value,
                         immutable=True, phase='backup_checkpoint')
        return value

    def _switch_split(self, action, expected_document, actor, reason, authority, target, state, envelope, raw, stat):
        if expected_document != envelope['document_token']:
            raise RegistryTransactionError('document_conflict', 'stale expected document before switch', retryable=True)
        head = self._transaction_ready()
        records = self.read_backup_index()
        require(not any(r['status'] in ('pending', 'switched-index-incomplete') for r in records), 'pending backup index; recover explicitly')
        selected = self.resolve_backup(text(target, 'restore target')) if action == 'restore' else None
        selected_raw, desired, selected_era = self.snapshot_bytes(selected) if selected else (None, current_fields(empty()), None)
        desired = copy.deepcopy(current_fields(desired))
        new_era, operation_id = str(uuid.uuid4()), str(uuid.uuid4())
        op = self._transaction_id(operation_id)
        content = document_codec.serialize_current_document(desired, new_era, op)
        timestamp = datetime.now(timezone.utc).isoformat(timespec='seconds')
        sequence = max((record['sequence'] for record in records), default=0) + 1
        backup_id = f'{timestamp}-{sequence:06d}-{uuid.uuid4()}'
        checkpoint_id = str(uuid.uuid4())
        checkpoint = self._write_checkpoint(checkpoint_id, raw, state, envelope)
        owners = state['owners']
        def parent_name(parent):
            return 'user' if parent == 'user' else owners.get(parent[6:], {}).get('name', parent + ' (unresolved)')
        summary = dict(era=envelope['era'], revision=state['revision'], owners=len(owners),
            assignments=len(state['assignments']), coordination=len(state['coordination']), history=len(checkpoint['history']),
            roots=[o['name'] + ' / ' + o['role'] + ' (' + oid + ')' for oid, o in owners.items() if o['parent'] == 'user'],
            relations=[parent_name(o['parent']) + ' → ' + o['name'] + ' / ' + o['role'] + ' (' + oid + ')' for oid, o in owners.items()])
        readiness_changes = {}
        for cid, agreement in desired['coordination'].items():
            legacy_ready = agreement_view(desired, agreement, legacy=True)['agreement_ready']
            ready = agreement_view(desired, agreement)['agreement_ready']
            if legacy_ready != ready:
                readiness_changes[cid] = dict(legacy_ready=legacy_ready, restored_ready=ready,
                                              reasons=agreement_view(desired, agreement)['not_ready_reasons'])
        record = dict(id=backup_id, sequence=sequence, time=timestamp, action=action, actor=actor, reason=reason,
            authority=authority, snapshot='backups/' + backup_id + '.md', sha256=sha(raw), status='pending', summary=summary,
            before_token=envelope['document_token'], after_sha256=sha(content.encode()), after_era=new_era,
            target_id=selected['id'] if selected else None, readiness_changes=readiness_changes,
            storage_format='responsibility-registry/v3', audit_checkpoint=checkpoint_id, audit_checkpoint_sha256=sha(encoded(checkpoint)))
        directory = self.document.parent / 'backups'
        require(not directory.is_symlink(), 'backups directory cannot be symlink')
        directory.mkdir(mode=0o700, exist_ok=True)
        directory.chmod(0o700)
        exclusive_file(self.document.parent / record['snapshot'], raw)
        self.snapshot_bytes(record)
        references = dict(backup_record=record, before_backup=backup_id)
        if selected:
            if selected.get('storage_format') == 'responsibility-registry/v3':
                references['history_checkpoint'] = selected['audit_checkpoint']
                references['history_checkpoint_sha256'] = selected['audit_checkpoint_sha256']
            else:
                # Legacy backup remains untouched; its complete history is preserved
                # in a new immutable checkpoint for the newly published v3 era.
                full, old_era = parse_document(selected_raw.decode('utf-8'))
                target_checkpoint = str(uuid.uuid4())
                value = self._write_checkpoint(target_checkpoint, selected_raw, full,
                    dict(era=old_era, audit_head=None), history=full['history'])
                references['history_checkpoint'] = target_checkpoint
                references['history_checkpoint_sha256'] = sha(encoded(value))
            references['restore_target'] = selected['id']
        else:
            references['history_reset'] = True
        intent = dict(action=action, actor=actor, reason=reason, authority=authority, restore_target=record['target_id'])
        prepare = self._prepare_transaction(operation_id, sha(encoded(intent)), intent,
            dict(action=action, backup=record['snapshot'], restored_from=record['target_id'], readiness_changes=readiness_changes),
            state, envelope, raw, desired, new_era, content, None, action, actor, reason, authority, references=references, head=head)
        status = self._publish_transaction(prepare, content, raw, stat,
            before_publication=lambda: (self.write_backup_index(records + [record]), self.switch_checkpoint('prepared')))
        current = status['current']
        result = dict(status['result'], operation_id=operation_id, transaction_id=status['transaction_id'])
        if current:
            result.update(era=current['era'], revision=current['state']['revision'], document_token=current['document_token'])
        return result

    def switch_document(self, action, expected_document, actor, reason, authority, target=None):
        """Explicit backed-up clear/restore. No history replay or execution restart."""
        self.switch_authority(actor, reason, authority)
        require(action in ['clear', 'restore'], 'invalid switch action')
        def operation():
            state, envelope, raw, stat = self.read_document()
            if envelope['storage_format'] == 'responsibility-registry/v3':
                return self._switch_split(action, expected_document, actor, reason, authority, target, state, envelope, raw, stat)
            if self.audit.exists():
                self._transaction_ready()
            require(expected_document == envelope['document_token'], 'stale expected document; reread current Markdown before switch')
            control = self.control()
            require(not control or control['status'] == 'completed', 'pending cutover; recover explicitly before switch')
            require(not self.switch_journal.exists(), 'pending switch; run recover-switch before any further mutation')
            records = self.read_backup_index()
            require(not any(r['status'] in ['pending', 'switched-index-incomplete'] for r in records), 'pending backup index; recover explicitly')
            selected = self.resolve_backup(text(target, 'restore target')) if action == 'restore' else None
            # Resolve and hash/parse the target BEFORE saving current; repeated previous toggles.
            desired = self.snapshot_bytes(selected)[1] if selected else empty()
            era = str(uuid.uuid4())
            content = serialize_document(desired, era)
            require(parse_document(content) == (desired, era), 'switch serialization mismatch')
            readiness_changes = {}
            if selected:
                for cid, agreement in desired.get('coordination', {}).items():
                    legacy_ready = agreement_view(desired, agreement, legacy=True)['agreement_ready']
                    current_ready = agreement_view(desired, agreement)['agreement_ready']
                    if legacy_ready != current_ready:
                        readiness_changes[cid] = dict(legacy_ready=legacy_ready, restored_ready=current_ready,
                                                     reasons=agreement_view(desired, agreement)['not_ready_reasons'])
            timestamp = datetime.now(timezone.utc).isoformat(timespec='seconds')
            sequence = max((r['sequence'] for r in records), default=0) + 1
            identifier = f'{timestamp}-{sequence:06d}-{uuid.uuid4()}'
            owners = state['owners']
            def parent_name(parent):
                return 'user' if parent == 'user' else owners.get(parent[6:], {}).get('name', parent + ' (unresolved)')
            summary = dict(era=envelope['era'], revision=state['revision'], owners=len(owners),
                assignments=len(state['assignments']), coordination=len(state.get('coordination', {})), history=len(state['history']),
                roots=[o['name'] + ' / ' + o['role'] + ' (' + oid + ')' for oid, o in owners.items() if o['parent'] == 'user'],
                relations=[parent_name(o['parent']) + ' → ' + o['name'] + ' / ' + o['role'] + ' (' + oid + ')' for oid, o in owners.items()])
            record = dict(id=identifier, sequence=sequence, time=timestamp, action=action, actor=actor, reason=reason,
                authority=authority, snapshot='backups/' + identifier + '.md', sha256=hashlib.sha256(raw).hexdigest(),
                status='pending', summary=summary, before_token=envelope['document_token'], after_sha256=hashlib.sha256(content.encode()).hexdigest(),
                after_era=era, target_id=selected['id'] if selected else None, readiness_changes=readiness_changes)
            directory = self.document.parent / 'backups'
            require(not directory.is_symlink(), 'backups directory cannot be symlink')
            directory.mkdir(mode=0o700, exist_ok=True)
            directory.chmod(0o700)
            exclusive_file(self.document.parent / record['snapshot'], raw)  # EXACT bytes bound by final recheck
            require(self.snapshot_bytes(record)[0] == raw, 'pre-switch snapshot readback mismatch')
            journal = dict(record=record, phase='pending')
            atomic_write(self.switch_journal, json.dumps(journal, ensure_ascii=False, indent=2) + '\n')
            records.append(record)
            did_switch = False
            publication_started = False
            def before_replace():
                nonlocal publication_started
                self.recheck(raw, stat)
                publication_started = True
            try:
                self.write_backup_index(records)  # durable pending index before replacing live
                self.switch_checkpoint('prepared')
                atomic_write(self.document, content, before_replace=before_replace)
                did_switch = True
                self.switch_checkpoint('switched')
                check, current, _, _ = self.read_document()
                require(check == desired and current['era'] == era, 'switched document readback mismatch')
                record['status'] = 'completed'
                self.write_backup_index(records)
                self.switch_journal.unlink()
                fsync_directory(self.root)
                return dict(action=action, backup=record['snapshot'], restored_from=record['target_id'],
                            readiness_changes=readiness_changes, **current)
            except (OSError, RegistryError, ValueError) as exc:
                # The journal is audit evidence, not authority over later manual MD.
                # A rename can succeed even when its directory fsync/readback fails.
                observed = None
                unchanged = False
                try:
                    observed = hashlib.sha256(self.document.read_bytes()).hexdigest()
                    unchanged = observed == record['sha256'] and fingerprint(self.document.stat()) == stat
                except OSError:
                    pass
                switched = did_switch or observed == record['after_sha256']
                not_published = not switched and (not publication_started or unchanged)
                record['status'] = 'switched-index-incomplete' if switched else ('aborted' if not_published else 'pending')
                phase = record['status'] if switched or not_published else 'publishing'
                try:
                    atomic_write(self.switch_journal, json.dumps(dict(record=record, phase=phase), ensure_ascii=False, indent=2) + '\n')
                except OSError:
                    pass  # Original pending journal still binds both hashes for recovery.
                if not_published:
                    try:
                        self.write_backup_index(records)
                        self.switch_journal.unlink()
                        fsync_directory(self.root)
                    except (OSError, RegistryError, ValueError):
                        # If unlink succeeded but its directory sync failed, retain a
                        # recoverable journal too; never imply durable cleanup.
                        if not self.switch_journal.exists():
                            try:
                                atomic_write(self.switch_journal, json.dumps(dict(record=record, phase=phase), ensure_ascii=False, indent=2) + '\n')
                            except OSError:
                                pass  # The terminal index remains explicit audit evidence.
                    else:
                        raise RegistryError('switch not performed; aborted recorded: ' + str(exc)) from exc
                message = 'switched; index incomplete' if switched else ('switch not performed; recovery required' if not_published else 'switch outcome unresolved; recovery required')
                raise RegistryError(message + ': ' + str(exc)) from exc
        return self.locked(operation)

    def recover_switch(self, expected_document, actor, reason, authority):
        self.switch_authority(actor, reason, authority)
        def operation():
            state, envelope, raw, stat = self.read_document()
            require(envelope['document_token'] == expected_document, 'stale expected document before recovery')
            require(not self.audit.exists() or self.audit.pending() is None, 'audit transaction pending; run recover-transaction')
            require(self.switch_journal.exists(), 'no pending switch to recover')
            journal = parse_json(self.switch_journal.read_text(encoding='utf-8'))
            record = journal['record']
            self.snapshot_bytes(record)
            digest = hashlib.sha256(raw).hexdigest()
            phase = journal['phase']
            if digest == record['after_sha256']:
                record['status'] = 'completed'
            elif digest == record['sha256'] and phase != 'switched-index-incomplete':
                record['status'] = 'aborted'
            else:
                record['status'] = 'superseded-manual'
                record['reconciliation'] = dict(observed_sha256=digest, journal_phase=phase)
            records = self.read_backup_index()
            prior = next((r for r in records if r['id'] == record['id']), None)
            if prior:
                require(all(prior[k] == record[k] for k in record if k not in ['status', 'reconciliation']), 'backup index/journal mismatch; preserve evidence')
                records[records.index(prior)] = record
            else:
                records.append(record)
            self.recheck(raw, stat)
            self.write_backup_index(records)
            self.switch_journal.unlink()
            fsync_directory(self.root)
            return dict(recovered=record['status'], backup=record['snapshot'], **envelope)
        return self.locked(operation)

    def switch_checkpoint(self, phase):
        """Deterministic isolated interruption seam; no production fault knob."""



def normalize_scope(value, source=None):
    value = copy.deepcopy(value)
    if source is not None:
        value.setdefault('goal_source', copy.deepcopy(source))
    keys(value, ['id', 'project', 'repo', 'task', 'objective', 'acceptance', 'goal_source'], ['depends_on'])
    keys(value['repo'], ['path'], ['id'])
    value['repo']['path'] = str(Path(text(value['repo']['path'], 'repo.path')).expanduser().resolve())
    value['repo'].setdefault('id', 'repo:' + value['repo']['path'])
    keys(value['project'], ['name', 'path'], ['id'])
    project = value['project']
    project['path'] = str(Path(text(project['path'], 'project.path')).expanduser().resolve())
    project.setdefault('id', 'path:' + project['path'])
    value.setdefault('depends_on', [])
    scope(value)
    return value


def authorize(actor, oid, state=None):
    permitted = [oid, 'user']
    if state and state['owners'][oid]['parent'].startswith('owner:'):
        permitted.append(state['owners'][oid]['parent'][6:])
    require(actor in permitted, f'actor must be responsible owner {oid}, authorized immediate superior (handover), or explicitly authorized user')


def mutate_records(state, args, payload, *, stamp=None):
    """Shared business mutation. Does not own revision or history."""
    action, actor = args.command, args.actor
    text(actor, 'actor')
    state.setdefault('coordination', {})
    stamp = stamp or now()
    occurred = occurrence(args, stamp)
    if action == 'handover':
        prior = state['assignments'].get(args.assignment, {})
        if prior.get('status') == 'handed-over' and prior.get('handover_request') == handover_request(args):
            return {'unchanged': True, 'revision': state['revision'], 'successor': prior['successor'],
                    'note': 'original transfer already recorded; query --task --active for current owner'}
    if action == 'register':
        keys(payload, ['owner', 'appointment', 'capacity', 'goal_source'], ['assignment'])
        goal_source(payload['goal_source'])
        require(payload['capacity'] == 'accountable-owner', 'helpers must not register; capacity must be accountable-owner')
        owner(payload['owner'])
        o = copy.deepcopy(payload['owner'])
        superior = o['parent'][6:] if o['parent'].startswith('owner:') else None
        require(payload['goal_source']['issuer'] in ['user', o['parent']], 'superior appointment must name the immediate parent as issuer')
        require(actor in [o['id'], 'user'] or (actor == superior and payload['goal_source']['issuer'] == 'owner:' + actor),
                'actor must be appointee, user, or authorized immediate superior/goal issuer')
        a = normalize_scope(payload['assignment'], payload['goal_source']) if 'assignment' in payload else None
        text(payload['appointment'], 'explicit appointment evidence')
        old_o = state['owners'].get(o['id'])
        if old_o:
            require(all(old_o[k] == v for k, v in o.items()), 'owner exists with different facts; query then update-owner with expected revision')
        else:
            state['owners'][o['id']] = dict(o, appointment_source=copy.deepcopy(payload['goal_source']), created_at=stamp, updated_at=stamp)
        existing = state['assignments'].get(a['id']) if a else None
        if old_o and a is None:
            return {'unchanged': True, 'revision': state['revision'], 'owner': old_o}
        if existing:
            require(existing['status'] == 'active' and existing['owner'] == o['id'] and
                    all(existing[k] == v for k, v in a.items()) and existing['appointment'] == payload['appointment'],
                    'responsibility ID already used; query, then update or authorized handover (do not reuse ended IDs)')
            return {'unchanged': True, 'revision': state['revision'], 'assignment': existing}
        if a:
            state['assignments'][a['id']] = dict(a, owner=o['id'], status='active', appointment=payload['appointment'],
                                                 created_at=stamp, updated_at=stamp, start_time=occurred, end_time=None,
                                                 end_reason=None, successor=None)
        reason = payload['appointment']
    else:
        require(args.expected_revision == state['revision'],
                f'stale/missing expected revision; query current revision {state["revision"]} and retry deliberately')
        text(args.reason, 'reason')
        reason = args.reason
        if action.startswith('coord-'):
            coordinate(state, args, payload, stamp)
        elif action == 'update-owner':
            owner(payload)
            oid = payload['id']
            require(oid in state['owners'], 'owner not registered')
            authorize(actor, oid)
            if all(state['owners'][oid][k] == v for k, v in payload.items()):
                return {'unchanged': True, 'revision': state['revision']}
            state['owners'][oid].update(payload)
            state['owners'][oid]['updated_at'] = stamp
        else:
            aid = args.assignment
            require(aid in state['assignments'], 'responsibility ID not registered')
            a = state['assignments'][aid]
            authorize(actor, a['owner'], state if action == 'handover' else None)
            require(a['status'] == 'active', 'responsibility already ended; use a new ID for new appointment')
            if action == 'update':
                updated = normalize_scope(payload)
                require(updated['id'] == aid and updated['project'] == a['project'] and updated['task'] == a['task']
                        and ('repo' not in a or updated['repo'] == a['repo']),
                        'update cannot change task/project identity; close and register or handover explicitly')
                if all(a[k] == v for k, v in updated.items()):
                    return {'unchanged': True, 'revision': state['revision']}
                a.update(updated)
                a['updated_at'] = stamp
            elif action == 'close':
                a.update(status='ended', end_reason=reason, updated_at=stamp, end_time=occurred)
            elif action == 'handover':
                require(args.to_owner in state['owners'], 'successor owner must register its verified identity first')
                require(args.to_owner != a['owner'], 'successor must be a different owner')
                text(args.new_assignment, 'new assignment ID')
                require(args.new_assignment not in state['assignments'], 'successor assignment ID already used')
                text(args.pending_work, 'explicit active-child custody/reporting plan, or verified none')
                old_parent = state['owners'][a['owner']]['parent']
                new_parent = state['owners'][args.to_owner]['parent']
                require(old_parent == new_parent or args.reporting_change,
                        'successor reports to a different superior; explicit --reporting-change authorization/evidence required')
                affected = [cid for cid, c in state['coordination'].items() if args.assignment in c['scopes'] and c['status'] != 'released']
                inherited = a.get('handover_obligations', {})
                obligations = sorted(set(affected + inherited.get('coordination_ids', [])))
                require(not obligations or args.coordination_plan,
                        'active/inherited coordination requires --coordination-plan; old acknowledgements cannot transfer')
                for cid in affected:
                    state['coordination'][cid].update(status='released', updated_at=stamp)
                new = dict(copy.deepcopy(a), id=args.new_assignment, owner=args.to_owner, created_at=stamp,
                           updated_at=stamp, start_time=occurred, end_time=None, appointment=reason,
                           handover_obligations=dict(predecessor=args.assignment, pending_work=args.pending_work,
                               previous_pending_work=inherited.get('pending_work'), coordination_ids=obligations,
                               coordination_plan=args.coordination_plan or 'No active agreements found at recording time',
                               reporting_change=args.reporting_change or 'Superior unchanged: ' + old_parent,
                               predecessor_context=dict(owner=owner_fact(state['owners'], a['owner']),
                                   pi_session_id=state['owners'][a['owner']]['identity']['pi_session_id'],
                                   intercom_id=state['owners'][a['owner']]['identity']['intercom_id'],
                                   recovery=copy.deepcopy(state['owners'][a['owner']].get('recovery')),
                                   project=copy.deepcopy(a['project']), repo=copy.deepcopy(a.get('repo')),
                                   superior=old_parent, objective=a['objective'], acceptance=a['acceptance'],
                                   handover_summary=reason, pending_work=args.pending_work,
                                   recorded_at=datetime.fromisoformat(stamp).isoformat(timespec='seconds'))),
                           goal_source={'issuer': 'user' if actor == 'user' else 'owner:' + actor, 'source': reason})
                new.pop('handover_request', None)
                state['assignments'][args.new_assignment] = new
                a.update(status='handed-over', successor=args.new_assignment, end_reason=reason, updated_at=stamp,
                         end_time=occurred, handover_request=handover_request(args))
    validate_current(current_fields(state))
    return {'action': action}


def changed_event(before, state, action, actor, reason, authority, args=None, *, stamp=None):
    stamp = stamp or now()
    snapshots = {side: {kind: {key: copy.deepcopy(records[key]) for key in records
                              if before[kind].get(key) != state[kind].get(key)}
                        for kind, records in ((kind, value[kind]) for kind in ('owners', 'assignments', 'coordination'))}
                 for side, value in [('before', before), ('after', state)]}
    occurred = occurrence(args, stamp)
    return dict(revision=state['revision'], id=f'event-{state["revision"]:08d}',
                at=datetime.fromisoformat(stamp).isoformat(timespec='seconds'), occurred=occurred,
                actor=actor, authority=authority, action=action, reason=reason,
                changes=semantic_changes(before, state, action, args), **snapshots)


def mutate(state, args, payload):
    """Legacy full-state API retains its complete input/history behavior."""
    before = {kind: copy.deepcopy(state.get(kind, {})) for kind in ('owners', 'assignments', 'coordination')}
    stamp = now()
    result = mutate_records(state, args, payload, stamp=stamp)
    if result.get('unchanged'):
        return result
    validate(state)
    state['revision'] += 1
    reason = payload['appointment'] if args.command == 'register' else args.reason
    state['history'].append(changed_event(before, state, args.command, args.actor, reason,
                                           getattr(args, 'authority', None), args, stamp=stamp))
    return dict(result, revision=state['revision'])


def query(state, args):
    selected = {aid: a for aid, a in state['assignments'].items()
                if (not args.owner or a['owner'] == args.owner)
                and (not args.project or args.project in [a['project']['id'], a['project']['path']])
                and (not args.task or args.task in a['task'] or args.task in a['objective'])
                and (not args.repo or args.repo in [a.get('repo', {}).get('id'), a.get('repo', {}).get('path')])
                and (not args.active or a['status'] == 'active')}
    ids = {a['owner'] for a in selected.values()}
    if args.owner:
        ids.add(args.owner)
    if not any([args.owner, args.project, args.task, args.repo, args.active]):
        ids = set(state['owners'])
    agreements = {cid: agreement_view(state, value) for cid, value in state.get('coordination', {}).items()
                  if (not args.repo or args.repo in [value['repo']['id'], value['repo']['path']])
                  and (not args.owner or args.owner in {b['owner'] for b in value['scopes'].values()})
                  and (not (args.project or args.task) or set(value['scopes']) & set(selected))}
    current = {aid: dict(owner=owner_fact(state['owners'], a['owner']), project=a['project'], task=a['task'],
                         superior=state['owners'][a['owner']]['parent'], handover_obligations=a.get('handover_obligations'))
               for aid, a in selected.items() if a['status'] == 'active'}
    history = [event for event in state.get('history', []) if not any([args.owner, args.project, args.task, args.repo]) or
               any(d.get('from_assignment') in selected or d.get('to_assignment') in selected
                   or (args.owner and args.owner in [(d.get('old_owner') or {}).get('id'), (d.get('new_owner') or {}).get('id')])
                   for d in event.get('changes', []))]
    result = dict(revision=state['revision'], assignments=selected, current_owners=current, coordination=agreements,
                history_loaded='history' in state,
                owners={oid: o for oid, o in state['owners'].items() if oid in ids},
                hierarchy=[dict(owner=oid, parent=o['parent'],
                                parent_resolved=o['parent'] == 'user' or o['parent'][6:] in state['owners'],
                                children=[k for k, v in state['owners'].items() if v['parent'] == 'owner:' + oid])
                           for oid, o in state['owners'].items() if oid in ids],
                relations=[dict(source=aid, status=a['status'], target=d) for aid, a in selected.items() for d in a['depends_on']])
    if 'history' in state:
        result['history'] = history
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--document', type=Path, help='explicit live Markdown path; mandatory with alternate --root')
    parser.add_argument('--root', type=Path, default=DEFAULT_ROOT, help='private store; override only for explicit alternate store/tests')
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ['init', 'migrate', 'resume-migration', 'recover-initialization']:
        commands.add_parser(name)
    report = commands.add_parser('rebuild', help='non-authoritative report to stdout or explicit --output, never dashboard.md')
    report.add_argument('--output', type=Path)
    commands.add_parser('backup-list', help='historical completed snapshot candidates; never current authority')
    for command in ['clear', 'restore', 'recover-switch', 'migrate-storage', 'recover-transaction']:
        p = commands.add_parser(command)
        p.add_argument('--expected-document', required=command != 'recover-transaction')
        if command == 'migrate-storage':
            p.add_argument('--operation-id')
        p.add_argument('--actor', required=True)
        p.add_argument('--reason', required=True)
        p.add_argument('--authority', required=True, help='actual direct user authorization evidence')
        if command == 'restore':
            p.add_argument('--target', required=True, help='previous, exact backup ID/name, or aware ISO minute/second (for example 2026-10-24T16:13+08:00)')
    q = commands.add_parser('query', help='JSON including hierarchy and dependency relations')
    for name in ['owner', 'project', 'task', 'repo']:
        q.add_argument('--' + name)
    q.add_argument('--active', action='store_true')
    q.add_argument('--history', action='store_true', help='explicitly hydrate legacy and committed audit history')
    audit = commands.add_parser('audit', help='explicit private audit evidence; never current authorization')
    selectors = audit.add_mutually_exclusive_group()
    selectors.add_argument('--operation-id')
    selectors.add_argument('--transaction', help='canonical transaction UUID from retained private audit evidence')
    audit.add_argument('--intent-digest')
    audit.add_argument('--history', action='store_true', help='load logical history at --transaction, never current authority')
    for name in ['register', 'update-owner', 'update', 'close', 'handover', 'coord-propose', 'coord-ack', 'coord-release']:
        p = commands.add_parser(name)
        p.add_argument('--expected-document', required=True, help='opaque token from a deliberate current query')
        p.add_argument('--operation-id', help='stable retry ID for split-storage transaction')
        p.add_argument('--actor', required=True, help='owner ID or user; authorization attestation, not authentication')
        p.add_argument('--authority', help='known authorization/decision evidence; absent remains unknown')
        p.add_argument('--occurred-at', help='known ISO seconds+timezone, or approximate human description')
        p.add_argument('--occurrence-precision', choices=['exact', 'approximate', 'unknown'])
        if name in ['register', 'update-owner', 'update', 'coord-propose']:
            p.add_argument('--input', default='-', help='JSON file or - for stdin')
        if name in ['coord-ack', 'coord-release']:
            p.add_argument('--coordination', required=True)
        if name != 'register':
            p.add_argument('--expected-revision', required=True, type=int)
            p.add_argument('--reason', required=True)
        if name in ['update', 'close', 'handover']:
            p.add_argument('--assignment', required=True)
        if name == 'handover':
            p.add_argument('--to-owner', required=True)
            p.add_argument('--new-assignment', required=True)
            p.add_argument('--pending-work', required=True, help='active child custody/reporting plan, or explicitly verified none')
            p.add_argument('--coordination-plan', help='renegotiation/integration plan for active/inherited agreements')
            p.add_argument('--reporting-change', help='explicit evidence if successor superior differs')
    args = parser.parse_args(argv)
    try:
        payload = None
        if hasattr(args, 'input'):
            if args.input == '-':
                payload = parse_json(sys.stdin.read())
            else:
                payload = parse_json(Path(args.input).read_text(encoding='utf-8'))
        store = Store(args.root, args.document)
        if args.command == 'init':
            result = store.initialize()
        elif args.command == 'recover-initialization':
            result = store.recover_initialization()
        elif args.command in ['migrate', 'resume-migration']:
            result = store.migrate(resume=args.command == 'resume-migration')
        elif args.command == 'migrate-storage':
            result = store.migrate_storage(args.expected_document, args.actor, args.reason, args.authority, args.operation_id)
        elif args.command == 'recover-transaction':
            result = store.recover_transaction(args.actor, args.reason, args.authority, args.expected_document)
        elif args.command == 'audit':
            require(args.intent_digest is None or args.operation_id is not None,
                    '--intent-digest requires --operation-id and cannot accompany --transaction')
            require(not args.history or args.transaction is not None, 'audit --history requires --transaction')
            if args.transaction is not None:
                result = store.audit_history_at(args.transaction, include_history=args.history)
            elif args.operation_id is not None:
                require(args.intent_digest is not None, 'operation audit requires --intent-digest')
                result = store.operation_status(args.operation_id, args.intent_digest)
            else:
                def audit_all():
                    current = store._safe_snapshot()
                    split = current is not None and current['format'] == document_codec.SPLIT_FORMAT
                    return dict(attempts=store.audit.history() if split or store.audit.exists() else [])
                result = store.locked(audit_all)
        elif args.command == 'backup-list':
            result = store.locked(lambda: dict(backups=store.backup_candidates()))
        elif args.command in ['clear', 'restore']:
            result = store.switch_document(args.command, args.expected_document, args.actor, args.reason, args.authority, getattr(args, 'target', None))
        elif args.command == 'recover-switch':
            result = store.recover_switch(args.expected_document, args.actor, args.reason, args.authority)
        elif args.command == 'rebuild':
            state = store.history_snapshot()
            result = dict(report=dashboard(state, store.envelope['era']))
            if args.output is None:
                print(result['report'])
                return 0
            output = args.output.expanduser().resolve()
            require(output not in [store.document.resolve(), (store.root / 'dashboard.md').resolve(),
                                   (store.root / 'registry.json').resolve(), (store.root / 'registry.lock').resolve(),
                                   (store.root / 'document-control.json').resolve(), (store.root / 'switch-pending.json').resolve(),
                                   (store.document.parent / 'BACKUPS.md').resolve()] and
                    (store.document.parent / 'backups').resolve() not in [output, *output.parents] and
                    store.audit.path.resolve() not in [output, *output.parents] and
                    not (output.parent == store.root.resolve() and output.name.startswith('legacy-')),
                    'report output cannot replace authority, legacy tombstone or backups')
            atomic_write(output, result.pop('report'))
            result['output'] = str(output)
        elif args.command == 'query':
            result = store.query_current(args, include_history=args.history)
        else:
            result = store.mutate_cli(args, payload)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (RegistryError, OSError, ValueError, TypeError, KeyError) as exc:
        if isinstance(exc, RegistryTransactionError):
            print(json.dumps(dict(error=exc.code, message=str(exc), commit_state=exc.commit_state,
                                  retryable=exc.retryable, operation_id=exc.operation_id)), file=sys.stderr)
        else:
            print(f'ERROR: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())

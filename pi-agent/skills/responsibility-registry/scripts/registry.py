#!/usr/bin/env python3
"""Private accountability directory. POSIX, Python stdlib; JSON is authoritative."""
import argparse
import copy
from datetime import datetime, timezone
import fcntl
import html
import json
import os
from pathlib import Path
import sys
import tempfile

DEFAULT_ROOT = Path.home() / '.pi/agent/.memory/local/responsibility-registry'


class RegistryError(Exception):
    pass


def require(condition, message):
    if not condition:
        raise RegistryError(message)


def parse_json(content):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, f'duplicate JSON key: {key}')
            result[key] = value
        return result

    def invalid(value):
        raise RegistryError(f'invalid JSON constant: {value}')

    return json.loads(content, object_pairs_hook=pairs, parse_constant=invalid)


def text(value, label):
    require(isinstance(value, str) and bool(value.strip()), f'{label}: nonempty string required')
    return value


def keys(value, required, optional=()):
    require(isinstance(value, dict), 'object required')
    require(set(required) <= set(value) <= set(required) | set(optional),
            f'fields required={sorted(required)}, optional={sorted(optional)}; got={sorted(value)}')


def identity(value):
    keys(value, ['pi_session_id', 'intercom_id', 'source', 'unknown_reason'])
    require(value['source'] in ['current-session', 'intercom-status', 'unavailable'], 'invalid identity source')
    for field in ['pi_session_id', 'intercom_id']:
        if value[field] is not None:
            text(value[field], field)
    require(isinstance(value['unknown_reason'], str), 'unknown_reason must be string')
    if value['pi_session_id'] is None or value['intercom_id'] is None:
        text(value['unknown_reason'], 'unknown_reason')
    if value['source'] == 'unavailable':
        require(value['pi_session_id'] is None and value['intercom_id'] is None,
                'unavailable identity cannot contain guessed IDs')
    if value['source'] == 'intercom-status':
        require(value['pi_session_id'] is None and value['intercom_id'] is not None,
                'intercom status proves only the intercom ID, not the Pi session ID')
    if value['source'] == 'current-session':
        text(value['pi_session_id'], 'verified Pi session ID')


def goal_source(value):
    keys(value, ['issuer', 'source'])
    text(value['source'], 'goal source evidence')
    issuer = text(value['issuer'], 'goal issuer')
    require(issuer == 'user' or (issuer.startswith('owner:') and issuer[6:].strip()),
            'goal issuer must be user or owner:<stable-id>')


def owner(value):
    keys(value, ['id', 'name', 'role', 'parent', 'identity'])
    for field in ['id', 'name', 'role', 'parent']:
        text(value[field], field)
    require(value['id'] != 'user' and not value['id'].startswith('owner:'), 'owner ID must be bare and not user')
    require(value['parent'] == 'user' or
            (value['parent'].startswith('owner:') and value['parent'][6:].strip()),
            'parent must be user or owner:<stable-id> (unregistered allowed)')
    identity(value['identity'])


def scope(value, legacy=False):
    base = ['id', 'project', 'task', 'objective', 'acceptance', 'depends_on']
    keys(value, base if legacy else base + ['repo', 'goal_source'], ['repo', 'goal_source'] if legacy else [])
    if 'goal_source' in value:
        goal_source(value['goal_source'])
    if 'repo' in value:
        keys(value['repo'], ['id', 'path'])
        text(value['repo']['id'], 'repo.id')
        require(Path(text(value['repo']['path'], 'repo.path')).is_absolute(), 'repo.path must be canonical absolute Git common dir')
    for field in ['id', 'task', 'objective', 'acceptance']:
        text(value[field], field)
    keys(value['project'], ['id', 'name', 'path'])
    for field in ['id', 'name', 'path']:
        text(value['project'][field], 'project.' + field)
    require(Path(value['project']['path']).is_absolute(), 'project.path must be absolute')
    require(isinstance(value['depends_on'], list), 'depends_on must be array')
    for dep in value['depends_on']:
        require(isinstance(dep, dict) and dep.get('kind') in ['project', 'task'], 'dependency kind must be project/task')
        keys(dep, ['kind', 'project'] + (['task'] if dep['kind'] == 'task' else []))
        for field in dep:
            text(dep[field], 'dependency.' + field)


def check_graph(owners):
    for start in owners:
        seen, here = set(), start
        while here in owners:
            require(here not in seen, f'hierarchy cycle involving {here}; change parent explicitly')
            seen.add(here)
            parent = owners[here]['parent']
            here = parent[6:] if parent.startswith('owner:') else None


def validate(state):
    keys(state, ['version', 'revision', 'owners', 'assignments', 'history'], ['coordination'])
    require(isinstance(state.get('coordination', {}), dict), 'invalid coordination container')
    require(type(state['version']) is int and state['version'] == 1, 'unsupported canonical version')
    require(type(state['revision']) is int and state['revision'] >= 0, 'invalid revision')
    require(isinstance(state['owners'], dict) and isinstance(state['assignments'], dict)
            and isinstance(state['history'], list), 'invalid canonical containers')
    for oid, record in state['owners'].items():
        keys(record, ['id', 'name', 'role', 'parent', 'identity', 'created_at', 'updated_at'], ['appointment_source'])
        owner({k: v for k, v in record.items() if k not in ['created_at', 'updated_at', 'appointment_source']})
        if 'appointment_source' in record:
            goal_source(record['appointment_source'])
        require(record['id'] == oid, 'owner key/id mismatch')
        for field in ['created_at', 'updated_at']:
            text(record[field], field)
    check_graph(state['owners'])
    leads, projects = {}, {}
    for aid, record in state['assignments'].items():
        base = ['id', 'project', 'task', 'objective', 'acceptance', 'depends_on']
        keys(record, base + ['owner', 'status', 'appointment', 'created_at', 'updated_at', 'end_reason', 'successor'], ['repo', 'goal_source'])
        scope({k: record[k] for k in base + ['repo', 'goal_source'] if k in record}, legacy=True)
        require(aid == record['id'] and record['owner'] in state['owners'], 'invalid assignment owner/id')
        require(record['status'] in ['active', 'ended', 'handed-over'], 'invalid responsibility lifecycle')
        for field in ['appointment', 'created_at', 'updated_at']:
            text(record[field], field)
        require(record['end_reason'] is None or isinstance(record['end_reason'], str), 'invalid end reason')
        require(record['successor'] is None or record['successor'] in state['assignments'], 'missing handover successor')
        if record['status'] != 'active':
            text(record['end_reason'], 'ended responsibility reason')
        require((record['status'] == 'handed-over') == (record['successor'] is not None), 'invalid handover lifecycle')
        project = record['project']
        require(project['id'] not in projects or projects[project['id']] == project,
                f'project identity mismatch for {project["id"]}; reuse its canonical name/path')
        projects[project['id']] = project
        if record['status'] == 'active':
            key = (project['id'], record['task'])
            require(key not in leads,
                    f'lead conflict for {key}: existing {leads.get(key)}, attempted {aid}; query and use authorized handover')
            leads[key] = aid
    for cid, agreement in state.get('coordination', {}).items():
        validate_agreement(state, agreement)
        require(cid == agreement['id'], 'coordination key/id mismatch')
    require(len(state['history']) == state['revision'], 'history/revision mismatch')
    for index, event in enumerate(state['history'], 1):
        keys(event, ['revision', 'at', 'actor', 'action', 'reason', 'before', 'after'])
        require(event['revision'] == index, 'invalid history revision')
        for field in ['at', 'actor', 'action', 'reason']:
            text(event[field], 'history.' + field)
        require(isinstance(event['before'], dict) and isinstance(event['after'], dict), 'invalid history snapshots')


def validate_agreement(state, value):
    keys(value, ['id', 'repo', 'scopes', 'shared_interfaces', 'workspace', 'sequencing', 'integration_owner',
                 'acceptance', 'status', 'acknowledgements', 'created_at', 'updated_at'])
    for field in ['id', 'shared_interfaces', 'sequencing', 'acceptance', 'created_at', 'updated_at']:
        text(value[field], 'coordination.' + field)
    keys(value['repo'], ['id', 'path'])
    require(Path(text(value['repo']['path'], 'repo.path')).is_absolute(), 'canonical repo path required')
    text(value['repo']['id'], 'repo.id')
    keys(value['workspace'], ['mode', 'plan'])
    require(value['workspace']['mode'] in ['shared-cwd', 'isolated-worktrees'], 'invalid workspace mode')
    text(value['workspace']['plan'], 'branch/worktree plan')
    require(isinstance(value['scopes'], dict) and value['scopes'], 'affected direction scopes required')
    participants = set()
    for aid, boundary in value['scopes'].items():
        keys(boundary, ['owner', 'write_scope', 'assignment_updated_at'])
        require(aid in state['assignments'] and boundary['owner'] in state['owners'], 'unknown scope/owner in agreement')
        require(isinstance(boundary['write_scope'], list) and boundary['write_scope'], 'explicit file/module write boundaries required')
        for path in boundary['write_scope']:
            text(path, 'write scope')
        text(boundary['assignment_updated_at'], 'scope version')
        participants.add(boundary['owner'])
    require(value['integration_owner'] in participants, 'integration owner must be participant')
    require(value['status'] in ['proposed', 'confirmed', 'released'], 'invalid coordination status')
    require(isinstance(value['acknowledgements'], dict), 'invalid acknowledgements')
    require(set(value['acknowledgements']) <= participants, 'acknowledgement from nonparticipant')
    for ack in value['acknowledgements'].values():
        keys(ack, ['at', 'evidence'])
        text(ack['at'], 'ack timestamp')
        text(ack['evidence'], 'explicit acknowledgement evidence, not sent-message receipt')
    require(value['status'] != 'confirmed' or set(value['acknowledgements']) == participants,
            'unacknowledged agreement cannot be confirmed')


def agreement_view(state, value):
    reasons = []
    participants = sorted({b['owner'] for b in value['scopes'].values()})
    if value['status'] != 'confirmed':
        reasons.append('agreement is ' + value['status'])
    missing = sorted(set(participants) - set(value['acknowledgements']))
    if missing:
        reasons.append('explicit acknowledgement missing: ' + ', '.join(missing))
    for aid, b in value['scopes'].items():
        a = state['assignments'].get(aid, {})
        if (a.get('status') != 'active' or a.get('owner') != b['owner'] or
                a.get('updated_at') != b['assignment_updated_at'] or a.get('repo') != value['repo']):
            reasons.append('scope changed/ended; renegotiate: ' + aid)
    return dict(value, participants=participants, agreement_ready=not reasons, not_ready_reasons=reasons)


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
            scopes[aid] = dict(owner=a['owner'], write_scope=boundary, assignment_updated_at=a['updated_at'])
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
            require(not any('scope changed' in r for r in agreement_view(state, value)['not_ready_reasons']),
                    'scope changed; release and renegotiate before acknowledgement')
            require(args.actor not in value['acknowledgements'], 'owner already acknowledged; query existing evidence')
            value['acknowledgements'][args.actor] = dict(at=stamp, evidence=args.reason)
            if set(value['acknowledgements']) == participants:
                value['status'] = 'confirmed'
        else:
            value['status'] = 'released'
        value['updated_at'] = stamp


def empty():
    return dict(version=1, revision=0, owners={}, assignments={}, coordination={}, history=[])


def now():
    return datetime.now(timezone.utc).isoformat(timespec='microseconds')


def cell(value):
    if value is None:
        return '未知'
    # Escape HTML before adding our own break marker; no raw pipes or newlines in cells.
    return html.escape(str(value), quote=True).replace('|', '&#124;').replace('\r\n', '\n').replace('\r', '\n').replace('\n', '<br>').replace('`', '&#96;')


def table(headers, rows):
    return '\n'.join(['| ' + ' | '.join(headers) + ' |', '| ' + ' | '.join(['---'] * len(headers)) + ' |'] +
                     ['| ' + ' | '.join(cell(v) for v in row) + ' |' for row in rows])


def source_label(value):
    return value['issuer'] + ': ' + value['source'] if value else '历史未录入，待确认'


def parent_label(state, parent):
    if parent == 'user':
        return 'user（用户，最高授权）'
    oid = parent[6:]
    return f'owner:{oid}' + (f'（{state["owners"][oid]["name"]}）' if oid in state['owners'] else '（未登记，待解析）')


def dashboard(state):
    owners, assignments = state['owners'], state['assignments']
    lines = ['# 全局责任登记簿', '', f'版本 1 · 修订 {state["revision"]}', '',
             '登记表示责任归属，不表示在线；联系前重新核验 intercom status/list。用户是最高授权者。', '',
             '仅 parent:user 的顶层负责人向用户汇报；其他负责人逐级上报，目标/验收逐级下达。未解析上级须澄清，不自动升为顶层。', '',
             'JSON 为唯一事实源；本页由 rebuild 重建，不要手改。历史快照见 registry.json 的 history。', '', '## 负责人 / 上下级 / 联系', '']
    rows = []
    for oid, o in sorted(owners.items()):
        i = o['identity']
        children = ', '.join(k for k, v in sorted(owners.items()) if v['parent'] == 'owner:' + oid) or '无已登记下级'
        rows.append([oid, o['name'], o['role'], parent_label(state, o['parent']), children,
                     i['pi_session_id'], i['intercom_id'], i['source'] + ('；' + i['unknown_reason'] if i['unknown_reason'] else ''),
                     source_label(o.get('appointment_source')), o['updated_at']])
    lines += [table(['owner ID', '名称', '角色', '上级', '下级（推导）', 'Pi session ID', 'Intercom 联系 ID', '身份依据 / 缺失原因', '任命签发 / 来源', '更新于'], rows), '', '## 项目 / 任务 / 责任范围', '']
    rows = []
    for aid, a in sorted(assignments.items()):
        p = a['project']
        rows.append([aid, p['id'], p['name'], p['path'], a['task'], a['owner'], a['status'], a['objective'],
                     a['acceptance'], source_label(a.get('goal_source')), a.get('repo', {}).get('id', '历史未录入，待核验'),
                     a['appointment'], a['end_reason'] or '—', a['successor'] or '—', a['created_at'], a['updated_at']])
    lines += [table(['责任 ID', '项目 ID', '项目名', '项目路径', '任务 ID', 'owner ID', '状态', '目标 / 范围', '验收目标', '目标签发 / 来源', '规范 repo ID', '任命依据', '结束原因', '接任责任 ID', '建立于', '更新于'], rows), '', '## 依赖关系（与汇报关系独立）', '']
    rows = []
    for aid, a in sorted(assignments.items()):
        for d in a['depends_on']:
            resolved = (any(x['project']['id'] == d['project'] and
                            (d['kind'] == 'project' or x['task'] == d['task']) and x['status'] == 'active'
                            for x in assignments.values()))
            rows.append([aid, a['status'], d['kind'], d['project'], d.get('task', '整项目'), '已登记' if resolved else '未登记 / 无活动责任'])
    lines += [table(['来源责任 ID', '来源状态', '目标类型', '目标项目 ID', '目标任务 ID', '目标解析'], rows), '',
              '## 同仓库写入协调（协议可用不代表取得 writer 锁）', '',
              '发送提议不等于同意；所有受影响方明确确认后才可用。仅覆盖所列范围；共享 cwd 仍仅一个 writer，隔离 worktree 仍按重叠/接口约定集成。', '']
    rows = []
    for cid, value in sorted(state.get('coordination', {}).items()):
        view = agreement_view(state, value)
        bounds = '; '.join(f'{aid} / {b["owner"]}: {", ".join(b["write_scope"])}' for aid, b in value['scopes'].items())
        acks = '; '.join(f'{oid}: {a["evidence"]} @ {a["at"]}' for oid, a in value['acknowledgements'].items()) or '无明确确认'
        rows.append([cid, value['repo']['id'], value['repo']['path'], bounds, value['status'],
                     '是（仅已确认范围）' if view['agreement_ready'] else '否：' + '; '.join(view['not_ready_reasons']),
                     ', '.join(view['participants']), acks, value['shared_interfaces'],
                     value['workspace']['mode'] + ': ' + value['workspace']['plan'], value['sequencing'],
                     value['integration_owner'], value['acceptance']])
    lines += [table(['协议 ID', 'repo ID', '规范 repo 路径', '方向责任 / owner / 写入范围', '状态', '写入协议可用', '参与者', '明确确认依据', '共享文件 / 接口', '分支 / worktree', '顺序', '集成 owner', '验收约定'], rows), '']
    return '\n'.join(lines)


def atomic_write(path, content):
    fd, temp = tempfile.mkstemp(prefix='.' + path.name + '-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as out:
            out.write(content)
            out.flush()
            os.fsync(out.fileno())
        os.replace(temp, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


class Store:
    def __init__(self, root):
        self.root = Path(root).expanduser().resolve()

    def run(self, fn, initialize=False):
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        lock_fd = os.open(self.root / 'registry.lock', os.O_CREAT | os.O_RDWR, 0o600)
        with os.fdopen(lock_fd, 'a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            path = self.root / 'registry.json'
            if not path.exists():
                require(initialize, 'registry missing; run init (never reconstruct JSON from dashboard)')
                require(not (self.root / 'dashboard.md').exists(),
                        'dashboard exists but canonical JSON missing; restore JSON backup, do not reset')
                state = empty()
            else:
                try:
                    state = parse_json(path.read_text(encoding='utf-8'))
                    validate(state)
                except (ValueError, RegistryError, TypeError, KeyError) as exc:
                    raise RegistryError(f'canonical data corrupt; preserved unchanged: {exc}') from exc
            original = copy.deepcopy(state)
            result = fn(state)
            validate(state)
            changed = state != original or not path.exists()
            if changed:
                atomic_write(path, json.dumps(state, ensure_ascii=False, indent=2) + '\n')
            # Every successful read also repairs a stale/missing projection under the same lock.
            try:
                atomic_write(self.root / 'dashboard.md', dashboard(state))
            except OSError as exc:
                raise RegistryError(f'canonical revision {state["revision"]} retained; dashboard failed: {exc}; run rebuild') from exc
            return result


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


def authorize(actor, oid):
    require(actor in [oid, 'user'], f'actor must be responsible owner {oid} or explicitly authorized user')


def mutate(state, args, payload):
    action, actor = args.command, args.actor
    text(actor, 'actor')
    state.setdefault('coordination', {})
    before = {kind: copy.deepcopy(state[kind]) for kind in ['owners', 'assignments', 'coordination']}
    stamp = now()
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
                                                 created_at=stamp, updated_at=stamp, end_reason=None, successor=None)
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
            authorize(actor, a['owner'])
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
                a.update(status='ended', end_reason=reason, updated_at=stamp)
            elif action == 'handover':
                require(args.to_owner in state['owners'], 'successor owner must register its verified identity first')
                require(args.to_owner != a['owner'], 'successor must be a different owner')
                text(args.new_assignment, 'new assignment ID')
                require(args.new_assignment not in state['assignments'], 'successor assignment ID already used')
                new = dict(copy.deepcopy(a), id=args.new_assignment, owner=args.to_owner, created_at=stamp,
                           updated_at=stamp, appointment=reason,
                           goal_source={'issuer': 'user' if actor == 'user' else 'owner:' + actor, 'source': reason})
                state['assignments'][args.new_assignment] = new
                a.update(status='handed-over', successor=args.new_assignment, end_reason=reason, updated_at=stamp)
    validate({**state, 'history': state['history'], 'revision': state['revision']})
    state['revision'] += 1
    after = {kind: state[kind] for kind in ['owners', 'assignments', 'coordination']}
    # Store only changed records, with full before/after snapshots for meaningful audit.
    changes = {side: {kind: {key: copy.deepcopy(records[key]) for key in records
                            if before[kind].get(key) != after[kind].get(key)}
                      for kind, records in snapshot.items()}
               for side, snapshot in [('before', before), ('after', after)]}
    state['history'].append(dict(revision=state['revision'], at=stamp, actor=actor, action=action,
                                 reason=reason, before=changes['before'], after=changes['after']))
    return {'revision': state['revision'], 'action': action}


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
    return dict(revision=state['revision'], assignments=selected, coordination=agreements,
                owners={oid: o for oid, o in state['owners'].items() if oid in ids},
                hierarchy=[dict(owner=oid, parent=o['parent'],
                                parent_resolved=o['parent'] == 'user' or o['parent'][6:] in state['owners'],
                                children=[k for k, v in state['owners'].items() if v['parent'] == 'owner:' + oid])
                           for oid, o in state['owners'].items() if oid in ids],
                relations=[dict(source=aid, status=a['status'], target=d) for aid, a in selected.items() for d in a['depends_on']])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=DEFAULT_ROOT, help='private store; override only for explicit alternate store/tests')
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ['init', 'rebuild']:
        commands.add_parser(name)
    q = commands.add_parser('query', help='JSON including hierarchy and dependency relations')
    for name in ['owner', 'project', 'task', 'repo']:
        q.add_argument('--' + name)
    q.add_argument('--active', action='store_true')
    for name in ['register', 'update-owner', 'update', 'close', 'handover', 'coord-propose', 'coord-ack', 'coord-release']:
        p = commands.add_parser(name)
        p.add_argument('--actor', required=True, help='owner ID or user; authorization attestation, not authentication')
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
    args = parser.parse_args(argv)
    try:
        payload = None
        if hasattr(args, 'input'):
            if args.input == '-':
                payload = parse_json(sys.stdin.read())
            else:
                payload = parse_json(Path(args.input).read_text(encoding='utf-8'))
        store = Store(args.root)
        if args.command in ['init', 'rebuild']:
            result = store.run(lambda s: dict(revision=s['revision'], owners=len(s['owners']), assignments=len(s['assignments']),
                                             dashboard=str(store.root / 'dashboard.md')), initialize=args.command == 'init')
        elif args.command == 'query':
            result = store.run(lambda s: query(s, args))
        else:
            result = store.run(lambda s: mutate(s, args, payload))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (RegistryError, OSError, ValueError, TypeError, KeyError) as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())

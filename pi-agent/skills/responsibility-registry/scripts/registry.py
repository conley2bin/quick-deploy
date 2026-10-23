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
import re
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


def validate_time(value):
    keys(value, ['at', 'precision'])
    require(value['precision'] in ['exact', 'unknown', 'approximate'], 'invalid time precision')
    if value['precision'] == 'unknown':
        require(value['at'] is None, 'unknown time must not fabricate seconds')
    elif value['precision'] == 'approximate':
        text(value['at'], 'approximate time description')
    else:
        text(value['at'], 'exact time')
        require(re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(Z|[+-]\d{2}:\d{2})', value['at']),
                'exact time requires ISO8601 seconds with explicit timezone')
        datetime.fromisoformat(value['at'].replace('Z', '+00:00'))


def goal_source(value):
    keys(value, ['issuer', 'source'])
    text(value['source'], 'goal source evidence')
    issuer = text(value['issuer'], 'goal issuer')
    require(issuer == 'user' or (issuer.startswith('owner:') and issuer[6:].strip()),
            'goal issuer must be user or owner:<stable-id>')


def recovery_context(value):
    keys(value, ['session_file', 'cwd', 'repo', 'worktree', 'summary', 'decisions', 'pending_issues',
                 'context_refs', 'source', 'unknown_reason'])
    for field in ['session_file', 'cwd', 'repo', 'worktree']:
        if value[field] is not None:
            require(Path(text(value[field], 'recovery.' + field)).is_absolute(), 'recovery locator must be absolute: ' + field)
    for field in ['summary', 'decisions', 'pending_issues']:
        if value[field] is not None:
            text(value[field], 'recovery.' + field)
    require(isinstance(value['context_refs'], list), 'context_refs must be explicit known pointers, not a session search')
    for ref in value['context_refs']:
        text(ref, 'context reference')
    text(value['source'], 'recovery verification evidence')
    require(isinstance(value['unknown_reason'], str), 'recovery unknown_reason must be string')
    if any(value[field] is None for field in ['session_file', 'cwd', 'repo', 'worktree', 'summary', 'decisions', 'pending_issues']):
        text(value['unknown_reason'], 'missing recovery context reason')


def owner(value):
    keys(value, ['id', 'name', 'role', 'parent', 'identity'], ['recovery'])
    if value.get('recovery') is not None:
        recovery_context(value['recovery'])
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
        keys(record, ['id', 'name', 'role', 'parent', 'identity', 'created_at', 'updated_at'], ['appointment_source', 'recovery'])
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
        keys(record, base + ['owner', 'status', 'appointment', 'created_at', 'updated_at', 'end_reason', 'successor'],
             ['repo', 'goal_source', 'start_time', 'end_time', 'handover_obligations', 'handover_request'])
        for field in ['start_time', 'end_time']:
            if record.get(field) is not None:
                validate_time(record[field])
        if 'handover_request' in record:
            require(isinstance(record['handover_request'], dict), 'invalid handover receipt')
        if 'handover_obligations' in record:
            keys(record['handover_obligations'], ['predecessor', 'pending_work', 'previous_pending_work', 'coordination_ids', 'coordination_plan', 'reporting_change'], ['predecessor_context'])
            require(record['handover_obligations']['predecessor'] in state['assignments'], 'missing handover predecessor')
            require(isinstance(record['handover_obligations']['coordination_ids'], list), 'invalid inherited coordination')
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
        keys(event, ['revision', 'at', 'actor', 'action', 'reason', 'before', 'after'], ['id', 'occurred', 'authority', 'changes'])
        if 'id' in event:
            require(event['id'] == f'event-{index:08d}', 'invalid stable event ID/order')
            validate_time(event['occurred'])
            validate_time({'at': event['at'], 'precision': 'exact'})
            require(event['authority'] is None or isinstance(event['authority'], str), 'invalid authority evidence')
            require(isinstance(event['changes'], list), 'invalid semantic changes')
            for change in event['changes']:
                keys(change, ['project', 'task', 'old_owner', 'new_owner', 'from_assignment', 'to_assignment', 'from_parent', 'to_parent', 'summary'])
                text(change['summary'], 'semantic change summary')
                for field in ['old_owner', 'new_owner']:
                    if change[field] is not None:
                        keys(change[field], ['id', 'name', 'role'])
                        text(change[field]['id'], 'historical owner ID')
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
    if action == 'handover':
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
        return '未知'
    # Escape HTML before adding our own break marker; no raw pipes or newlines in cells.
    return html.escape(str(value), quote=True).replace('|', '&#124;').replace('\r\n', '\n').replace('\r', '\n').replace('\n', '<br>').replace('`', '&#96;')


def table(headers, rows):
    return '\n'.join(['| ' + ' | '.join(headers) + ' |', '| ' + ' | '.join(['---'] * len(headers)) + ' |'] +
                     ['| ' + ' | '.join(cell(v) for v in row) + ' |' for row in rows])


def time_label(value):
    if not value:
        return '历史未记录，发生时间未知'
    return (value['at'] or '未知') + ('（约略，不保证秒）' if value['precision'] == 'approximate' else
                                     '（发生时间未知）' if value['precision'] == 'unknown' else '')


def recovery_label(value):
    if not value:
        return '恢复定位未登记，不能猜会话路径'
    return ('session file: ' + (value['session_file'] or '未知') + '; cwd: ' + (value['cwd'] or '未知') +
            '; repo: ' + (value['repo'] or '未知') + '; worktree: ' + (value['worktree'] or '未知') +
            '; 摘要: ' + (value['summary'] or '未知') + '; 决策: ' + (value['decisions'] or '未知') +
            '; 待解决: ' + (value['pending_issues'] or '未知') + '; 指针: ' + ', '.join(value['context_refs']) +
            '; 依据: ' + value['source'] + '; 缺失: ' + value['unknown_reason'])


def obligation_label(value):
    if not value:
        return '—'
    return ('来自 ' + value['predecessor'] + '；child: ' + value['pending_work'] +
            ('；前次安排: ' + value['previous_pending_work'] if value['previous_pending_work'] else '') +
            '；协调: ' + ', '.join(value['coordination_ids']) + '；计划: ' + value['coordination_plan'] +
            '；汇报: ' + value['reporting_change'] +
            ('；旧 owner context: ' + recovery_label(value['predecessor_context'].get('recovery'))
             if value.get('predecessor_context') else '；旧会话定位未登记'))


def owner_label(value):
    return '—' if value is None else f'{value["id"]} / {value.get("name") or "名称未知"} / {value.get("role") or "角色未知"}'


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
                     source_label(o.get('appointment_source')), recovery_label(o.get('recovery')), o['updated_at']])
    lines += [table(['owner ID', '名称', '角色', '上级', '下级（推导）', 'Pi session ID', 'Intercom 联系 ID', '身份依据 / 缺失原因', '任命签发 / 来源', '旧上下文恢复定位 / 交接决策', '更新于'], rows), '', '## 项目 / 任务 / 责任范围', '']
    rows = []
    for aid, a in sorted(assignments.items()):
        p = a['project']
        rows.append([aid, p['id'], p['name'], p['path'], a['task'], a['owner'], a['status'], a['objective'],
                     a['acceptance'], source_label(a.get('goal_source')), a.get('repo', {}).get('id', '历史未录入，待核验'),
                     a['appointment'], time_label(a.get('start_time')), time_label(a.get('end_time')) if a['status'] != 'active' else '进行中',
                     a['end_reason'] or '—', a['successor'] or '—',
                     obligation_label(a.get('handover_obligations')), a['created_at'], a['updated_at']])
    lines += [table(['责任 ID', '项目 ID', '项目名', '项目路径', '任务 ID', 'owner ID', '状态', '目标 / 范围', '验收目标', '目标签发 / 来源', '规范 repo ID', '任命依据', '责任开始（发生）', '责任结束（发生）', '结束原因', '接任责任 ID', '继承的子任务 / 协调 / 汇报安排', '建立记录于', '更新记录于'], rows), '', '## 依赖关系（与汇报关系独立）', '']
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
    lines += [table(['协议 ID', 'repo ID', '规范 repo 路径', '方向责任 / owner / 写入范围', '状态', '写入协议可用', '参与者', '明确确认依据', '共享文件 / 接口', '分支 / worktree', '顺序', '集成 owner', '验收约定'], rows), '',
              '## 责任变更历史（追加保留，事件顺序区分同秒变更）', '',
              '所有历史负责人和任命长期保留；会话离线不触发交接。发生时间与登记时间分列；未知或约略时间不补造秒。姓名/角色为事件登记时保存的事实。', '']
    rows = []
    for event in state['history']:
        details = event.get('changes') or [dict(project=None, task=None, old_owner=None, new_owner=None,
                                               from_parent=None, to_parent=None, from_assignment=None, to_assignment=None,
                                               summary=event['action'] + '（旧事件无语义字段，见 canonical snapshots）')]
        for detail in details:
            p = detail.get('project') or {}
            rows.append([event.get('id', f'legacy-{event["revision"]:08d}'), event['revision'],
                         time_label(event.get('occurred')), event['at'], event['action'], p.get('id', '—') + (' / ' + p['name'] if p.get('name') else ''), detail.get('task') or '—',
                         owner_label(detail.get('old_owner')), owner_label(detail.get('new_owner')),
                         (detail.get('from_assignment') or '—') + ' → ' + (detail.get('to_assignment') or '—'),
                         (detail.get('from_parent') or '—') + ' → ' + (detail.get('to_parent') or '—'),
                         event['actor'], event.get('authority') or '未单列（见原因/任命依据）', event['reason'], detail['summary']])
    lines += [table(['事件 ID', '顺序', '发生时间 / 精度', '登记时间（含时区）', '动作', '项目 ID', '任务 / 方向', '从谁（ID / 名称 / 角色）', '交给谁（ID / 名称 / 角色）', '责任链', '上级变化', '操作者', '授权依据', '原因', '变更说明'], rows), '']
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


def authorize(actor, oid, state=None):
    permitted = [oid, 'user']
    if state and state['owners'][oid]['parent'].startswith('owner:'):
        permitted.append(state['owners'][oid]['parent'][6:])
    require(actor in permitted, f'actor must be responsible owner {oid}, authorized immediate superior (handover), or explicitly authorized user')


def mutate(state, args, payload):
    action, actor = args.command, args.actor
    text(actor, 'actor')
    state.setdefault('coordination', {})
    before = {kind: copy.deepcopy(state[kind]) for kind in ['owners', 'assignments', 'coordination']}
    stamp = now()
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
    validate({**state, 'history': state['history'], 'revision': state['revision']})
    state['revision'] += 1
    after = {kind: state[kind] for kind in ['owners', 'assignments', 'coordination']}
    # Store only changed records, with full before/after snapshots for meaningful audit.
    changes = {side: {kind: {key: copy.deepcopy(records[key]) for key in records
                            if before[kind].get(key) != after[kind].get(key)}
                      for kind, records in snapshot.items()}
               for side, snapshot in [('before', before), ('after', after)]}
    state['history'].append(dict(revision=state['revision'], id=f'event-{state["revision"]:08d}',
                                 at=datetime.fromisoformat(stamp).isoformat(timespec='seconds'), occurred=occurred,
                                 actor=actor, authority=getattr(args, 'authority', None), action=action,
                                 reason=reason, changes=semantic_changes(before, state, action, args),
                                 before=changes['before'], after=changes['after']))
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
    current = {aid: dict(owner=owner_fact(state['owners'], a['owner']), project=a['project'], task=a['task'],
                         superior=state['owners'][a['owner']]['parent'], handover_obligations=a.get('handover_obligations'))
               for aid, a in selected.items() if a['status'] == 'active'}
    history = [event for event in state['history'] if not any([args.owner, args.project, args.task, args.repo]) or
               any(d.get('from_assignment') in selected or d.get('to_assignment') in selected
                   or (args.owner and args.owner in [(d.get('old_owner') or {}).get('id'), (d.get('new_owner') or {}).get('id')])
                   for d in event.get('changes', []))]
    return dict(revision=state['revision'], assignments=selected, current_owners=current, history=history, coordination=agreements,
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

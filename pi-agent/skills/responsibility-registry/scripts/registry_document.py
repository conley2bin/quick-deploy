#!/usr/bin/env python3
"""Pure fixed-format Markdown codec and domain validation; no I/O or discovery."""
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import re
import uuid

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

    def finite(value):
        number = float(value)
        require(math.isfinite(number), 'nonfinite JSON number: ' + value)
        return number

    return json.loads(content, object_pairs_hook=pairs, parse_constant=invalid, parse_float=finite)


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
        keys(boundary, ['owner', 'write_scope', 'assignment_updated_at'], ['assignment_digest'])
        digest = boundary.get('assignment_digest')
        require(digest is None or (isinstance(digest, str) and re.fullmatch(r'[0-9a-f]{64}', digest)), 'invalid assignment digest')
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
        keys(ack, ['at', 'evidence'], ['terms_digest'])
        digest = ack.get('terms_digest')
        require(digest is None or (isinstance(digest, str) and re.fullmatch(r'[0-9a-f]{64}', digest)), 'invalid terms digest')
        text(ack['at'], 'ack timestamp')
        text(ack['evidence'], 'explicit acknowledgement evidence, not sent-message receipt')
    require(value['status'] != 'confirmed' or set(value['acknowledgements']) == participants,
            'unacknowledged agreement cannot be confirmed')


def assignment_digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()


def terms_digest(value):
    terms = {k: v for k, v in value.items() if k not in ('status', 'acknowledgements', 'created_at', 'updated_at')}
    return assignment_digest(terms)


def agreement_view(state, value, legacy=False):
    reasons = []
    participants = sorted({b['owner'] for b in value['scopes'].values()})
    if value['status'] != 'confirmed':
        reasons.append('agreement is ' + value['status'])
    missing = sorted(set(participants) - set(value['acknowledgements']))
    if missing:
        reasons.append('explicit acknowledgement missing: ' + ', '.join(missing))
    if not legacy and any(ack.get('terms_digest') != terms_digest(value) for ack in value['acknowledgements'].values()):
        reasons.append('terms changed; renegotiate')
    for aid, b in value['scopes'].items():
        a = state['assignments'].get(aid, {})
        if (a.get('status') != 'active' or a.get('owner') != b['owner'] or
                a.get('updated_at') != b['assignment_updated_at'] or a.get('repo') != value['repo'] or
                (not legacy and b.get('assignment_digest') != assignment_digest(a))):
            reasons.append('scope changed/ended; renegotiate: ' + aid)
    return dict(value, participants=participants, agreement_ready=not reasons, not_ready_reasons=reasons)


FORMAT = 'responsibility-registry/v2'
TEMPLATE_ERA = 'template'
TITLE = '# Responsibility registry'
INSTRUCTIONS = (
    'Edit the visible rows. Details are additional fields, never duplicate columns.',
    'To start over: delete this live file, then copy RESPONSIBILITIES.template.md here.',
    'Reset changes registered authority; it does not stop running agents.',
    'Parent values: user or owner:ID (bare stable ID). Use the CLI to appoint after a template reset.',
    'Manual deletion loses this file and its history. Prefer agent-managed clear when a complete backup matters.',
)
SECTIONS = (
    ('Owners', 'owners', ('ID', 'Name', 'Role', 'Parent'), ('id', 'name', 'role', 'parent')),
    ('Assignments', 'assignments', ('ID', 'Owner', 'Status', 'Project ID', 'Task', 'Objective', 'Acceptance'),
     ('id', 'owner', 'status', 'project.id', 'task', 'objective', 'acceptance')),
    ('Coordination', 'coordination', ('ID', 'Status', 'Integration owner'), ('id', 'status', 'integration_owner')),
)
HISTORY = '## History (audit only; never restores current rows)'


def encode_cell(value):
    require(isinstance(value, str), 'cell must be text')
    # Escape ampersands before introducing any entities. No CommonMark interpretation.
    escaped = []
    edge_start = len(value) - len(value.lstrip(' '))
    edge_end = len(value.rstrip(' '))
    mapping = {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
               '|': '&#124;', '`': '&#96;', '\n': '<br>', '\r': '&#13;', '\t': '&#9;'}
    for i, char in enumerate(value):
        require(ord(char) >= 32 or char in '\r\n\t', 'unsupported control in cell')
        if char == ' ' and (i < edge_start or i >= edge_end):
            escaped.append('&#32;')
        elif char in mapping:
            escaped.append(mapping[char])
        elif char in '*_[]()!~\\#':
            escaped.append(f'&#{ord(char)};')
        else:
            escaped.append(char)
    return ''.join(escaped)


def decode_cell(value):
    value = value.strip(' ').replace('<br>', '\n')
    require('<' not in value and '>' not in value, 'raw HTML/tag forbidden; entity-escape literal text')
    # Accept only exact named forms or valid Unicode scalar numeric references.
    pattern = re.compile(r'&(?:amp|lt|gt|quot|apos|#[0-9]+|#x[0-9a-fA-F]+);')
    pos = 0
    while pos < len(value):
        if value[pos] != '&':
            pos += 1
            continue
        match = pattern.match(value, pos)
        require(match is not None, 'unknown/malformed entity; escape literal ampersand')
        token = match.group()
        if token.startswith('&#'):
            number = int(token[3:-1], 16) if token.startswith('&#x') else int(token[2:-1])
            require(0 < number <= 0x10ffff and not 0xd800 <= number <= 0xdfff,
                    'invalid Unicode entity')
        pos = match.end()
    # Custom numeric decoding preserves CR/tab instead of HTML5 control remapping.
    def decode(match):
        token = match.group()
        if token.startswith('&#'):
            return chr(int(token[3:-1], 16) if token.startswith('&#x') else int(token[2:-1]))
        return {'&amp;': '&', '&lt;': '<', '&gt;': '>', '&quot;': '"', '&apos;': "'"}[token]
    return pattern.sub(decode, value)


def strict_json(value):
    try:
        return parse_json(value)
    except (ValueError, RecursionError) as exc:
        raise RegistryError('invalid strict JSON: ' + str(exc)) from exc


def validate_envelope(state, era):
    validate(state)
    if era == TEMPLATE_ERA:
        require(state['revision'] == 0 and not any(state[k] for k in ('owners', 'assignments', 'coordination', 'history')),
                'template era must be wholly blank at Revision 0')
    else:
        try:
            require(str(uuid.UUID(era)) == era, 'era must be canonical UUID or template')
        except (ValueError, AttributeError) as exc:
            raise RegistryError('era must be canonical UUID or template') from exc


def serialize_document(state, era):
    validate_envelope(state, era)
    lines = [TITLE, '', 'Format: ' + FORMAT, 'Era: ' + era, 'Revision: ' + str(state['revision']), '', *INSTRUCTIONS, '']
    for heading, kind, headers, fields in SECTIONS:
        lines += ['## ' + heading, '| ' + ' | '.join((*headers, 'Details (JSON)')) + ' |',
                  '| ' + ' | '.join(['---'] * (len(headers) + 1)) + ' |']
        for record in state.get(kind, {}).values():
            details = dict(record)
            cells = []
            for field in fields:
                if field == 'project.id':
                    details['project'] = dict(details['project'])
                    cells.append(details['project'].pop('id'))
                else:
                    cells.append(details.pop(field))
            cells.append(json.dumps(details, ensure_ascii=False, separators=(',', ':'), allow_nan=False))
            lines.append('| ' + ' | '.join(encode_cell(c) for c in cells) + ' |')
        lines.append('')
    lines += [HISTORY, '```json', json.dumps(state['history'], ensure_ascii=False, indent=2, allow_nan=False), '```', '']
    return '\n'.join(lines)


def parse_document(content):
    require(isinstance(content, str), 'UTF-8 document text required')
    # Accept editor CRLF structural lines; literal data CR remains entity encoded.
    lines = content.replace('\r\n', '\n').split('\n')
    pos = 0
    section = 'preamble'
    def take(expected=None):
        nonlocal pos
        while pos < len(lines) and lines[pos] == '':
            pos += 1
        require(pos < len(lines), f'{section} line {pos + 1}: unexpected end')
        value = lines[pos]
        pos += 1
        require(expected is None or value == expected,
                f'{section} line {pos}: expected {expected!r}; got {value!r}')
        return value
    def table_cells(line):
        line = line.rstrip(' \t')
        require(line.startswith('|') and line.endswith('|'), f'{section} line {pos}: required outer delimiters')
        return [cell.strip(' ') for cell in line[1:-1].split('|')]
    def metadata(label):
        value = take()
        require(value.startswith(label + ': '), f'{section} line {pos}: expected unique {label}')
        return value[len(label) + 2:]
    take(TITLE)
    require(metadata('Format') == FORMAT, 'unsupported document format')
    era = metadata('Era')
    revision = metadata('Revision')
    require(re.fullmatch(r'0|[1-9][0-9]*', revision), f'line {pos}: invalid revision')
    # Instructions may be omitted, but no unrecognized prose is silently ignored.
    for instruction in INSTRUCTIONS:
        while pos < len(lines) and lines[pos] == '':
            pos += 1
        if pos < len(lines) and lines[pos] == instruction:
            pos += 1
    state = dict(version=1, revision=int(revision), owners={}, assignments={}, coordination={}, history=[])
    for heading, kind, headers, fields in SECTIONS:
        section = heading
        take('## ' + heading)
        require(table_cells(take()) == list((*headers, 'Details (JSON)')), f'{section} line {pos}: wrong column headers')
        separators = table_cells(take())
        require(len(separators) == len(headers) + 1 and all(re.fullmatch(r':?-{3,}:?', cell) for cell in separators),
                f'{section} line {pos}: invalid table separator')
        while pos < len(lines):
            if lines[pos] == '':
                pos += 1
                continue
            if not lines[pos].startswith('|'):
                break
            line_no = pos + 1
            line = lines[pos].rstrip(' \t')
            pos += 1
            try:
                require(line.endswith('|'), 'required outer table delimiters')
                raw_cells = line[1:-1].split('|')
                require(len(raw_cells) == len(fields) + 1, 'wrong cell count; encode data pipe as &#124; (not backslash pipe)')
                cells = [decode_cell(c) for c in raw_cells]
                record = strict_json(cells[-1])
                require(isinstance(record, dict), 'Details must be JSON object')
                for field, value in zip(fields, cells):
                    if field == 'project.id':
                        require(isinstance(record.get('project'), dict), 'project Details required')
                        require('id' not in record['project'], 'shadow field project.id')
                        record['project']['id'] = value
                    else:
                        require(field not in record, 'shadow field ' + field)
                        record[field] = value
                require(record['id'] not in state[kind], 'duplicate row ID ' + record['id'])
                state[kind][record['id']] = record
            except (RegistryError, ValueError, TypeError, KeyError) as exc:
                raise RegistryError(f'{section} line {line_no}: {exc}') from exc
    section = 'History'
    take(HISTORY)
    take('```json')
    start = pos
    while pos < len(lines) and lines[pos] != '```':
        pos += 1
    require(pos < len(lines), f'History line {start + 1}: missing closing fence')
    state['history'] = strict_json('\n'.join(lines[start:pos]))
    pos += 1
    require(all(line == '' for line in lines[pos:]), f'History line {pos + 1}: unexpected nonblank structure')
    validate_envelope(state, era)
    return state, era

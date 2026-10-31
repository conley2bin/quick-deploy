"""Persistent loopback signed-API bridge; native transactions remain in vps.py."""
from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import secrets
import signal
import stat
import shutil
import socket
import subprocess
import sys
import threading
import time
import manage_auth
import vps as vps_module

MAX_BODY = 8192
JOB_SECONDS = 1200
STOP_GRACE = 20  # foreground stop: seconds an active native job may finish its receipt before the group is terminated
CLEANUP_MARGIN = 30
HEX32 = re.compile(r'[0-9a-f]{32}\Z')
HEX64 = re.compile(r'[0-9a-f]{64}\Z')
NAME = re.compile(r'[a-z][a-z0-9-]{0,31}\Z')
ACTIONS = {'add', 'rename', 'reset', 'remove'}
CSP = ("default-src 'self'; connect-src 'self'; img-src 'self' blob:; script-src 'self'; "
       "style-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")


MARKER = b'<!-- quick-deploy managed launcher -->'
STOPPED_ENTRY = ('<!doctype html><!-- quick-deploy managed launcher -->'
                 '<meta charset="utf-8"><title>管理入口已停用</title>'
                 '<p>本管理入口已停用；重新安装并启动管理服务后会自动重新生成</p>\n').encode()
CORS_HEADERS = 'Content-Type, X-QD-Session, X-QD-Nonce, X-QD-MAC'


class Refusal(Exception):
    def __init__(self, code, status=409):
        self.code, self.status = code, status
        super().__init__(code)


def error(code):
    # Never reflect exception messages: native errors may contain credential URLs.
    return {'code': code, 'message': code.replace('_', ' ')}


def strict_json(data):
    def pairs(items):
        obj = {}
        for key, value in items:
            if key in obj:
                raise ValueError('duplicate key')
            obj[key] = value
        return obj
    return json.loads(data, object_pairs_hook=pairs,
                      parse_constant=lambda value: (_ for _ in ()).throw(ValueError('constant')))


def _matches(pattern, value):
    return isinstance(value, str) and pattern.fullmatch(value) is not None


def validate_request(body, recover=False):
    if not isinstance(body, dict):
        raise Refusal('invalid_schema', 400)
    required = {'request_id', 'expected_operation_id'} if recover else {
        'request_id', 'action', 'names', 'expected_generation'}
    if not required <= body.keys() or set(body) - required - (set() if recover else {'confirm'}):
        raise Refusal('invalid_schema', 400)
    if not _matches(HEX32, body['request_id']):
        raise Refusal('invalid_request_id', 400)
    if recover:
        if not _matches(HEX32, body['expected_operation_id']):
            raise Refusal('invalid_operation_id', 400)
        return dict(body)
    action, names = body['action'], body['names']
    if (not isinstance(action, str) or action not in ACTIONS or not isinstance(names, list)
            or len(names) != (2 if action == 'rename' else 1)
            or any(not _matches(NAME, name) for name in names) or len(set(names)) != len(names)
            or not _matches(HEX64, body['expected_generation'])):
        raise Refusal('invalid_action_names_generation', 400)
    if action in {'reset', 'remove'}:
        if body.get('confirm') != {'action': action, 'name': names[0]}:
            raise Refusal('confirmation_required', 400)
    elif 'confirm' in body:
        raise Refusal('unexpected_confirmation', 400)
    return dict(body)


def pending_info(vps, state):
    """Describe the exact pending bytes, including a stable recovery identity."""
    path = state / 'pending.json'
    if not path.exists() and not path.is_symlink():
        return {'present': False}
    raw = vps.read_private(path)
    value = strict_json(raw)
    if not isinstance(value, dict):
        raise ValueError('invalid pending')
    result = {'present': True}
    if isinstance(value.get('mode'), str) and value['mode'] in {
            'devices', 'backup', 'transport-upgrade', 'deploy', 'replace', 'restore'}:
        result['mode'] = value['mode']
    operation = value.get('request', {}).get('operation_id')
    # Native operation ids remain authoritative. A backup-only pending has none,
    # so bind recovery to the exact observed private bytes instead.
    result['operation_id'] = (operation if _matches(HEX32, operation)
                              else hashlib.sha256(raw).hexdigest()[:32])
    return result


def _source(vps, state):
    document = vps.state_document(state, argparse.Namespace())
    generation = document.get('native_generation')
    if not _matches(HEX64, generation):
        generation = None
    pending = pending_info(vps, state)
    model, manifest = None, None
    if not pending['present']:
        manifest, digest, files = vps.committed_dashboard_source(state)
        model, _, _ = vps.dashboard_render(manifest, digest, files)
        generation = digest
    return document, generation, pending, model, manifest


def machine_execute(vps, state, envelope):
    """One existing state flock; inject ONLY vps.remote for native integration tests.

    Internal envelope: action state/add/rename/reset/remove/recover, startup-only
    ssh_proxy and mihomo, and the browser request fields except request_id/confirm.
    No transaction, native receipt, export or generation validation is replaced.
    """
    state = Path(state)
    result = {'schema': 1, 'outcome': 'rejected', 'pending': {'present': False},
              'generation_before': None, 'generation_after': None, 'model': None}
    if not isinstance(envelope, dict):
        return dict(result, error=error('invalid_envelope'))
    action = envelope.get('action')
    common = {'action', 'ssh_proxy', 'mihomo'}
    required = {'expected_operation_id'} if action == 'recover' else (
        {'names', 'expected_generation'} if isinstance(action, str) and action in ACTIONS else set())
    if (not isinstance(action, str) or action not in ACTIONS | {'state', 'recover'}
            or set(envelope) - common - required or not required <= envelope.keys()):
        return dict(result, error=error('invalid_envelope'))
    if action in ACTIONS:
        try:
            validate_request(dict(request_id='0' * 32, action=action, names=envelope['names'],
                                  expected_generation=envelope['expected_generation'],
                                  **({'confirm': {'action': action, 'name': envelope['names'][0]}}
                                     if action in {'reset', 'remove'} and envelope['names'] else {})))
        except (Refusal, TypeError, IndexError):
            return dict(result, error=error('invalid_envelope'))
    if action == 'recover' and not _matches(HEX32, envelope['expected_operation_id']):
        return dict(result, error=error('invalid_envelope'))
    try:
        proxy = envelope.get('ssh_proxy')
        if proxy is not None:
            proxy = vps.ssh_proxy.parse_proxy(proxy)
        with vps.locked(state):
            document = vps.state_document(state, argparse.Namespace())
            before = document.get('native_generation')
            if not _matches(HEX64, before):
                before = None
            result['generation_before'] = result['generation_after'] = before
            pending = pending_info(vps, state)
            result['pending'] = pending
            if action == 'recover':
                result['recovered_mode'] = pending.get('mode')
            if action in ACTIONS and pending['present']:
                return dict(result, error=error('pending'))
            if action in ACTIONS and before != envelope['expected_generation']:
                return dict(result, error=error('stale_generation'))
            if action == 'recover' and (not pending['present'] or
                    pending.get('operation_id') != envelope['expected_operation_id']):
                return dict(result, error=error('pending_mismatch'))
            # Verify source and tested roster before any remote action, under flock.
            if not pending['present']:
                manifest, digest, files = vps.committed_dashboard_source(state)
                if digest != before:
                    return dict(result, error=error('invalid_source'))
                if action in ACTIONS:
                    names, accounts = envelope['names'], manifest['accounts']
                    if action == 'remove' and len(accounts) <= 1:
                        return dict(result, error=error('last_device_scope'))
                    if ((action == 'add' and names[0] in accounts) or
                            (action != 'add' and names[0] not in accounts) or
                            (action == 'rename' and names[1] in accounts)):
                        return dict(result, error=error('device_conflict'))
            failed = False
            carrier = vps.SSH_PROXY.set(proxy)
            try:
                if action in ACTIONS:
                    vps.devices(state, document, argparse.Namespace(
                        device_action=action, names=envelope['names'], mihomo=envelope.get('mihomo')))
                elif action == 'recover':
                    vps.recover_local(state, document, envelope.get('mihomo'))
            except Exception:
                failed = True
                # Full diagnostic stays in private child stderr, never HTTP.
                import traceback
                traceback.print_exc(file=sys.stderr)
            finally:
                vps.SSH_PROXY.reset(carrier)
            after_doc = vps.state_document(state, argparse.Namespace())
            after = after_doc.get('native_generation')
            result['generation_after'] = after if _matches(HEX64, after) else None
            result['pending'] = pending_info(vps, state)
            if result['pending']['present']:
                result['outcome'] = 'recovery_required'
                result['error'] = error('reconciliation_required')
                return result
            changed = result['generation_after'] != before
            # Source is re-verified even when final convenience export raised.
            try:
                _, digest, _, model, _ = _source(vps, state)
                result.update(generation_after=digest, model=model)
            except Exception:
                failed = True
                result['error'] = error('presentation_failed')
            if action == 'recover' and pending.get('mode') == 'backup':
                # A refreshed safety snapshot is not evidence that the interrupted
                # add/reset/remove ever began, even when its tar digest changes.
                result['outcome'] = 'unchanged'
                result['recovery_effect'] = 'snapshot_refreshed' if changed else 'rolled_back'
            elif changed:
                result['outcome'] = 'committed_export_failed' if failed else 'committed'
            elif action == 'recover' and not failed:
                result['outcome'] = 'rolled_back'
            else:
                result['outcome'] = 'rejected' if failed else 'unchanged'
            if failed and 'error' not in result:
                result['error'] = error('operation_failed')
            return result
    except Exception as exc:
        code = 'busy' if 'Another VPS command owns' in str(exc) else 'invalid_source'
        return dict(result, error=error(code))


def machine_main(vps, state):
    """Bounded JSON stdin, single JSON stdout; legacy printing goes to stderr."""
    try:
        data = sys.stdin.buffer.read(MAX_BODY + 1)
        if len(data) > MAX_BODY:
            raise ValueError('oversized')
        envelope = strict_json(data)
    except (ValueError, UnicodeError, RecursionError):
        print(json.dumps({'schema': 1, 'outcome': 'rejected', 'error': error('invalid_envelope')}))
        return 1
    with contextlib.redirect_stdout(sys.stderr):
        result = machine_execute(vps, state, envelope)
    print(json.dumps(result, separators=(',', ':')))
    return 0


class ManagementServer:
    """Persistent signed API, one worker, no queue. executor is an in-process test seam."""
    def __init__(self, vps, state, port=None, executor=None, runtime_carrier=None, runtime_mihomo=None):
        self.runtime_mihomo = runtime_mihomo  # explicit native core path frozen into every job envelope; None = normal resolver
        # runtime_carrier: foreground-only {'ssh_proxy': url|None, 'independent': bool}; None reads carrier.json.
        self.runtime_carrier = runtime_carrier
        self.stop_grace = 0 if runtime_carrier is None else STOP_GRACE
        self.vps, self.state = vps, Path(state).expanduser().absolute()
        vps.private_dir(self.state)
        self.jobs_dir = vps.private_dir(self.state / 'manage-jobs')
        config = manage_auth.ensure_state(self.state, vps.MODULE.parent)
        _, master = manage_auth.load_state(self.state)
        self.bootstrap = manage_auth.bootstrap_key(master)
        self.epoch = secrets.token_hex(16)
        self.sessions = manage_auth.Sessions()
        self.executor = executor
        self.mutex = threading.RLock()
        self.stopping = threading.Event()
        self.worker = None
        self.process = None
        self.jobs, self.requests = {}, {}
        self.last_job = None
        self.unknown = False
        self.launcher = None
        self.target = None  # public startup binding {target, endpoint, ssh_port}; frozen by bind_target(), None when unknown
        self.session_lock = os.open(self.jobs_dir / '.session.lock',
                                    os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            try:
                fcntl.flock(self.session_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise Refusal('manager_active') from None
            self._load_jobs()
        except BaseException:
            os.close(self.session_lock)
            self.session_lock = None
            raise
        manager = self

        class Handler(BaseHTTPRequestHandler):
            server_version = 'LocalManagement'
            sys_version = ''
            cors = False
            sign = None

            def setup(self):
                super().setup()
                self.connection.settimeout(5)

            def log_message(self, *_):
                pass

            def _send(self, status, value, content_type='application/json', extra=()):
                payload = (json.dumps(value, separators=(',', ':')).encode()
                           if content_type == 'application/json' else value)
                self.send_response(status)
                headers = [('Content-Type', content_type), ('Content-Length', str(len(payload))),
                           ('Cache-Control', 'no-store'), ('Referrer-Policy', 'no-referrer'),
                           ('X-Content-Type-Options', 'nosniff'), ('X-Frame-Options', 'DENY'),
                           ('Content-Security-Policy', CSP), ('Connection', 'close')]
                if self.cors:
                    headers += [('Access-Control-Allow-Origin', 'null'), ('Vary', 'Origin'),
                                ('Access-Control-Expose-Headers', 'X-QD-MAC')]
                if self.sign:
                    ks, nonce = self.sign
                    headers.append(('X-QD-MAC', manage_auth.response_mac(ks, manager.epoch, nonce, status, payload)))
                for name, val in list(headers) + list(extra):
                    self.send_header(name, val)
                self.end_headers()
                self.close_connection = True
                try:
                    self.wfile.write(payload)
                except (OSError, TimeoutError):
                    pass

            def _guard(self, post=False):
                if (self.headers.get_all('Host') != [manager.authority] or
                        any(key.lower() in {'forwarded', 'x-forwarded-host', 'x-forwarded-port',
                                            'x-forwarded-proto'} for key in self.headers)):
                    raise Refusal('invalid_host', 421)
                origins = self.headers.get_all('Origin') or []
                api = self.path.startswith('/api/')
                if origins not in ([], ['null']) or (api and origins != ['null']):
                    raise Refusal('invalid_origin', 403)
                self.cors = origins == ['null']
                if manager.stopping.is_set():
                    raise Refusal('stopping', 503)
                if post and self.headers.get_all('Content-Type') != ['application/json']:
                    raise Refusal('json_required', 415)

            def _raw_body(self, post):
                if not post:
                    return b''
                lengths = self.headers.get_all('Content-Length') or []
                if (len(lengths) != 1 or not re.fullmatch(r'[0-9]+', lengths[0]) or
                        self.headers.get_all('Transfer-Encoding')):
                    raise Refusal('invalid_length', 400)
                length = int(lengths[0])
                if length > MAX_BODY:
                    raise Refusal('body_too_large', 413)
                try:
                    data = self.rfile.read(length)
                except OSError:
                    raise Refusal('invalid_json', 400) from None
                if len(data) != length:
                    raise Refusal('invalid_json', 400)
                return data

            @staticmethod
            def _json(data):
                try:
                    return strict_json(data)
                except (ValueError, UnicodeError, RecursionError):
                    raise Refusal('invalid_json', 400) from None

            def _authenticate(self, method, raw):
                values = []
                for name in ('X-QD-Session', 'X-QD-Nonce', 'X-QD-MAC'):
                    got = self.headers.get_all(name) or []
                    if len(got) != 1:
                        raise Refusal('unauthorized', 401)
                    values.append(got[0])
                session, nonce, mac = values
                if not (_matches(HEX32, session) and _matches(HEX32, nonce) and _matches(HEX64, mac)):
                    raise Refusal('unauthorized', 401)
                with manager.mutex:
                    record = manager.sessions.get(session)
                    if record is None:
                        raise Refusal('unknown_session', 401)
                    ks = record['ks']
                    expected = manage_auth.request_mac(ks, manager.epoch, nonce, method, self.path, raw)
                    if not secrets.compare_digest(mac, expected):
                        raise Refusal('unauthorized', 401)
                    if manager.sessions.consume(session, nonce) != 'ok':
                        raise Refusal('replay', 401)
                self.sign = (ks, nonce)

            def _hello(self, raw):
                body = self._json(raw)
                if (not isinstance(body, dict) or set(body) != {'version', 'client_nonce'}
                        or type(body['version']) is not int or body['version'] != manage_auth.VERSION
                        or not _matches(HEX32, body['client_nonce'])):
                    raise Refusal('invalid_schema', 400)
                session, server_nonce = secrets.token_hex(16), secrets.token_hex(16)
                ctx = manage_auth.context(manager.origin, manager.epoch, session,
                                          body['client_nonce'], server_nonce)
                with manager.mutex:
                    manager.sessions.add(session, manage_auth.session_key(manager.bootstrap, ctx))
                self._send(200, {'version': manage_auth.VERSION, 'origin': manager.origin,
                                 'epoch': manager.epoch, 'session_id': session,
                                 'client_nonce': body['client_nonce'], 'server_nonce': server_nonce,
                                 'proof': manage_auth.server_proof(manager.bootstrap, ctx)})

            def _handle(self, method):
                post = method == 'POST'
                try:
                    self._guard(post)
                    if not self.path.startswith('/api/'):
                        if method == 'GET' and self.path == '/':
                            return self._send(200, b'quick-deploy VPS management service; open the private '
                                              b'vps/index.html entry file\n', 'text/plain; charset=utf-8')
                        raise Refusal('not_found', 404)
                    raw = self._raw_body(post)
                    if post and self.path == '/api/hello':
                        return self._hello(raw)
                    self._authenticate(method, raw)
                    if method == 'GET' and self.path == '/api/state':
                        self._send(200, manager.snapshot())
                    elif method == 'GET' and re.fullmatch(r'/api/jobs/[0-9a-f]{32}', self.path):
                        with manager.mutex:
                            job = manager.jobs.get(self.path.rsplit('/', 1)[1])
                            if job is None:
                                raise Refusal('unknown_job', 404)
                            self._send(200, manager.public_job(job))
                    elif post and self.path in {'/api/jobs', '/api/recover'}:
                        job = manager.admit(self._json(raw), recover=self.path == '/api/recover')
                        self._send(202, {'job_id': job['job_id'], 'state': job['state']})
                    else:
                        raise Refusal('not_found', 404)
                except Refusal as exc:
                    self._send(exc.status, {'error': error(exc.code)})
                except Exception:
                    self._send(500, {'error': error('admission_failed' if post else 'read_failed')})

            def do_GET(self):
                self._handle('GET')

            def do_POST(self):
                self._handle('POST')

            def do_OPTIONS(self):
                try:
                    self._guard()
                    if not self.path.startswith('/api/'):
                        raise Refusal('not_found', 404)
                    self._send(204, b'', 'text/plain; charset=utf-8', extra=[
                        ('Access-Control-Allow-Methods', 'GET, POST, OPTIONS'),
                        ('Access-Control-Allow-Headers', CORS_HEADERS), ('Access-Control-Max-Age', '600')])
                except Refusal as exc:
                    self._send(exc.status, {'error': error(exc.code)})

        self.httpd = ThreadingHTTPServer(('127.0.0.1', config['port'] if port is None else port), Handler)
        self.httpd.daemon_threads = True
        self.authority = '127.0.0.1:' + str(self.httpd.server_port)
        self.origin = 'http://' + self.authority

    def _load_jobs(self):
        records = []
        for path in self.jobs_dir.glob('*.json'):
            if not _matches(HEX32, path.stem):
                raise Refusal('unknown_job_file')
            records.append((path, self.vps.load(path)))
        for path, record in sorted(records, key=lambda item: item[1]['job'].get('started_at', 0)):
            job = record['job']
            if job['state'] == 'running':
                job.update(state='finished', outcome='recovery_required',
                           error=error('interrupted_unknown'), elapsed_s=JOB_SECONDS)
                record['unknown'] = True
                self.vps.host.atomic(path, json.dumps(record))
            self.jobs[job['job_id']] = job
            self.requests[job['request_id']] = (record['fingerprint'], job['job_id'])
            self.unknown |= record.get('unknown', False)
            self.last_job = job['job_id']

    def bind_target(self):
        """Freeze the committed node this manager was started for (after the startup connect). Public fields only:
        never machine_id/owner/keys, and later state changes cannot move it. A truly absent state.json (old/test paths)
        leaves target None; an unreadable or recordless one is an error, not a silent 'legacy' manager."""
        path = self.state / 'state.json'
        if not os.path.lexists(path):
            return
        current = self.vps.load(path).get('current')
        if not (isinstance(current, dict) and isinstance(current.get('target'), str) and isinstance(current.get('endpoint'), str)
                and isinstance(current.get('user'), str) and type(current.get('port')) is int):
            raise self.vps.host.Error('state.json has no valid committed node record; refusing to start a manager without a target binding')
        self.target = {'target': current['target'], 'endpoint': current['endpoint'], 'ssh_user': current['user'],
                       'ssh_port': current['port']}

    def timing(self):
        return {'job_limit_s': JOB_SECONDS, 'cleanup_margin_s': CLEANUP_MARGIN}

    def carrier(self):
        """The invocation carrier, else the operator-authored carrier.json, at each snapshot/admission: (reason, ssh_proxy)."""
        path = self.state / 'manage-service' / 'carrier.json'
        if self.runtime_carrier is None and not os.path.lexists(path):
            return 'carrier_not_configured', None
        try:
            if self.runtime_carrier is not None:
                value = self.runtime_carrier
                if value['independent'] is not True:
                    return 'independent_carrier_required', None
            else:
                value = strict_json(self.vps.read_private(path))
                if (not isinstance(value, dict) or set(value) != {'schema', 'ssh_proxy', 'independent'}
                        or value['schema'] != 1 or value['independent'] is not True):
                    raise ValueError('carrier schema')
            # null is an explicitly acknowledged DIRECT route, never an absence fallback.
            proxy = (None if value['ssh_proxy'] is None
                     else self.vps.ssh_proxy.parse_proxy(value['ssh_proxy']))
        except (ValueError, UnicodeError, RecursionError, self.vps.host.Error, self.vps.ssh_proxy.Error):
            return 'carrier_invalid', None
        if proxy is None:
            return None, None
        try:
            socket.create_connection(('127.0.0.1', proxy.port), timeout=0.25).close()
        except OSError:
            return 'carrier_listener_down', proxy.url
        return None, proxy.url

    def public_job(self, job):
        public = {key: value for key, value in job.items() if not key.startswith('_')}
        if job['state'] == 'running':
            public['elapsed_s'] = max(0, round(time.monotonic() - job['_started'], 3))
        return public

    def _persist(self, job, fingerprint, unknown=False):
        self.vps.host.atomic(self.jobs_dir / (job['job_id'] + '.json'), json.dumps({
            'schema': 1, 'fingerprint': fingerprint, 'unknown': unknown,
            'job': self.public_job(job)}))

    def snapshot(self):
        with self.mutex:
            busy = self.worker is not None and self.worker.is_alive()
            if busy:
                source = {'generation_after': self.jobs[self.last_job]['generation_before'],
                          'pending': {'present': True}, 'model': None}
            else:
                source = machine_execute(self.vps, self.state, {'action': 'state'})
            pending = source.get('pending', {'present': False})
            carrier, route = self.carrier()
            reason = (carrier or 'busy' if carrier or busy else 'pending' if pending['present'] else
                      source.get('error', {}).get('code'))
            return {'schema': 1, 'service_mode': 'persistent' if self.runtime_carrier is None else 'foreground', 'epoch': self.epoch,
                    'carrier': {'configured': carrier != 'carrier_not_configured',
                                'listening': carrier is None,
                                'mode': 'direct' if carrier is None and route is None else
                                'proxy' if route else None},
                    'target': self.target,
                    'generation': source.get('generation_after'), 'pending': pending,
                    **self.timing(),
                    'busy': busy or source.get('error', {}).get('code') == 'busy',
                    'writable': reason is None, 'writable_reason': reason,
                    'last_job': self.public_job(self.jobs[self.last_job]) if self.last_job else None,
                    'model': None if pending['present'] else source.get('model')}

    def admit(self, body, recover=False):
        body = validate_request(body, recover)
        fingerprint = hashlib.sha256(json.dumps(body, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        with self.mutex:
            previous = self.requests.get(body['request_id'])
            if previous:
                if previous[0] != fingerprint:
                    raise Refusal('request_id_conflict')
                return self.jobs[previous[1]]
            if self.stopping.is_set():
                raise Refusal('stopping', 503)
            carrier, route = self.carrier()
            if carrier:
                raise Refusal(carrier)
            if self.worker is not None and self.worker.is_alive():
                raise Refusal('busy')
            snapshot = self.snapshot()
            if snapshot['busy']:
                raise Refusal('busy')
            if recover:
                if not snapshot['pending']['present'] or snapshot['pending'].get('operation_id') != body['expected_operation_id']:
                    raise Refusal('pending_mismatch')
                envelope = {'action': 'recover', 'expected_operation_id': body['expected_operation_id']}
            else:
                if snapshot['pending']['present']:
                    raise Refusal('pending')
                if not snapshot['writable']:
                    raise Refusal(snapshot['writable_reason'] or 'invalid_source')
                if body['expected_generation'] != snapshot['generation']:
                    raise Refusal('stale_generation')
                accounts = snapshot['model']['devices']
                names = {device['name'] for device in accounts}
                if body['action'] == 'remove' and len(names) <= 1:
                    raise Refusal('last_device_scope')
                if ((body['action'] == 'add' and body['names'][0] in names) or
                        (body['action'] != 'add' and body['names'][0] not in names) or
                        (body['action'] == 'rename' and body['names'][1] in names)):
                    raise Refusal('device_conflict')
                envelope = {key: body[key] for key in ('action', 'names', 'expected_generation')}
            envelope.update(ssh_proxy=route, mihomo=self.runtime_mihomo)
            job_id = secrets.token_hex(16)
            job = {'job_id': job_id, 'request_id': body['request_id'], 'state': 'running',
                   'started_at': time.time(), '_started': time.monotonic(), 'elapsed_s': 0,
                   'deadline_s': JOB_SECONDS, 'generation_before': snapshot['generation']}
            job.update(action='recover' if recover else body['action'],
                       names=[] if recover else list(body['names']))
            self._persist(job, fingerprint)
            self.jobs[job_id] = job
            self.requests[body['request_id']] = (fingerprint, job_id)
            self.last_job = job_id
            self.worker = threading.Thread(target=self._run, args=(job, fingerprint, envelope),
                                           name='vps-manage-job', daemon=False)
            self.worker.start()
            return job

    @staticmethod
    def _terminate(process):
        # A process group is owned from Popen(start_new_session=True) to wait().
        for sig, delay in ((signal.SIGTERM, 5), (signal.SIGKILL, 5)):
            try:
                os.killpg(process.pid, sig)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=delay)
                # Descendants may outlive a parent which exited on TERM.
                if sig == signal.SIGTERM:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                return
            except subprocess.TimeoutExpired:
                pass
        process.wait()

    def _execute(self, job, envelope):
        if self.executor is not None:
            return self.executor(envelope)
        stdout = self.jobs_dir / (job['job_id'] + '.result')
        stderr = self.jobs_dir / (job['job_id'] + '.diagnostic')
        with open(stdout, 'xb') as output, open(stderr, 'xb') as diagnostic:
            os.chmod(stdout, 0o600)
            os.chmod(stderr, 0o600)
            process = subprocess.Popen([sys.executable, '-B', str(self.vps.MODULE / 'lib/manage.py'),
                                        '--state-dir', str(self.state), '--job'],
                                       stdin=subprocess.PIPE, stdout=output, stderr=diagnostic,
                                       start_new_session=True, shell=False)
            with self.mutex:
                self.process = process
            process.stdin.write(json.dumps(envelope).encode())
            process.stdin.close()
            while process.poll() is None:
                if self.stopping.is_set() and self.stop_grace:
                    try:
                        process.wait(self.stop_grace)
                    except subprocess.TimeoutExpired:
                        pass
                    if process.poll() is not None:
                        break
                if self.stopping.wait(0.1) or time.monotonic() - job['_started'] >= JOB_SECONDS:
                    self._terminate(process)
                    return {'outcome': 'recovery_required', 'error': error('interrupted_unknown'),
                            '_unknown': True}
            process.wait()
        with self.mutex:
            self.process = None
        try:
            if stdout.stat().st_size > 32 * 1024 * 1024:
                raise ValueError('machine result too large')
            result = strict_json(self.vps.read_private(stdout))
            if (not isinstance(result, dict) or result.get('schema') != 1 or result.get('outcome') not in {
                    'committed', 'rolled_back', 'recovery_required', 'committed_export_failed', 'rejected', 'unchanged'}):
                raise ValueError('invalid machine result')
            return result
        finally:
            # The result may transport a verified model to this parent, but job
            # metadata deliberately retains only safe outcomes/digests/names.
            try:
                stdout.unlink()
            except FileNotFoundError:
                pass

    def _run(self, job, fingerprint, envelope):
        unknown = False
        try:
            result = self._execute(job, envelope)
            unknown = result.get('_unknown', False)
        except BaseException:
            result = {'outcome': 'recovery_required', 'error': error('worker_unknown')}
            unknown = True
        finally:
            with self.mutex:
                if self.process is not None:
                    if self.process.poll() is None:
                        self._terminate(self.process)
                    self.process = None
                for key in ('outcome', 'pending', 'generation_before', 'generation_after', 'recovered_mode',
                            'recovery_effect', 'error'):
                    if key in result:
                        job[key] = result[key]
                job.update(state='finished', elapsed_s=round(time.monotonic() - job['_started'], 3))
                self.unknown |= unknown
                self._persist(job, fingerprint, unknown)

    def write_launcher(self):
        module = self.vps.MODULE
        destination = module / 'index.html'
        temporary = '.index-' + secrets.token_hex(12) + '.tmp'
        root = self.vps.host.run(['git', '-C', module, 'rev-parse', '--show-toplevel']).stdout.decode().strip()
        if Path(root) != module.parent:
            raise Refusal('launcher_requires_worktree')
        if self.vps.host.run(['git', '-C', root, 'ls-files', '--', 'vps/index.html', 'vps/.index-*.tmp']).stdout:
            raise Refusal('tracked_launcher_conflict')
        for name in (destination.name, temporary):
            self.vps.host.run(['git', '-C', root, 'check-ignore', '-q', '--', 'vps/' + name])
        try:
            import manage_entry
        except ImportError:
            raise Refusal('entry_renderer_unavailable') from None
        assets = {name: (module / 'assets' / name).read_text()
                  for name in ('manage.html', 'manage.js', 'manage.css', 'dashboard.css')}
        data = manage_entry.render_entry(assets, self.origin, self.bootstrap.hex())
        if not isinstance(data, bytes) or not data.lower().startswith(b'<!doctype html>'):
            raise Refusal('entry_invalid')
        data = b'<!doctype html>' + MARKER + data[len(b'<!doctype html>'):]
        with self.vps.directory_fd(module, private=False) as directory:
            if os.fstat(directory).st_uid != os.getuid():
                raise Refusal('launcher_owner')
            # No overwrite of an unrelated user file, even if it is mode0600.
            try:
                existing = self.vps.read_at(directory, destination.name)
            except FileNotFoundError:
                pass
            else:
                if not existing.startswith(b'<!doctype html>' + MARKER):
                    raise Refusal('launcher_conflict')
            self._replace_entry(directory, temporary, data)
        self.launcher = destination
        return destination

    @staticmethod
    def _replace_entry(directory, temporary, data):
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
        try:
            with os.fdopen(fd, 'wb') as out:
                out.write(data)
                out.flush()
                os.fsync(out.fileno())
            os.replace(temporary, 'index.html', src_dir_fd=directory, dst_dir_fd=directory)
            os.fsync(directory)
        finally:
            try:
                os.unlink(temporary, dir_fd=directory)
            except FileNotFoundError:
                pass

    def serve_forever(self):
        self._serving = True
        try:
            self.httpd.serve_forever(poll_interval=0.1)
        finally:
            self._serving = False

    def close(self):
        self.stopping.set()
        # Called by a different thread than serve_forever (see run_service()).
        if getattr(self, '_serving', False):
            self.httpd.shutdown()
        self.httpd.server_close()
        if self.worker is not None:
            self.worker.join(JOB_SECONDS + 15 if self.executor else self.stop_grace + 15)
            if self.worker.is_alive():
                raise RuntimeError('test executor did not stop')
        if self.session_lock is not None:
            os.close(self.session_lock)
            self.session_lock = None
        self.sessions.items.clear()


def find_opener():
    """Fixed platform openers only; never BROWSER or a shell string."""
    if sys.platform == 'darwin':
        return shutil.which('open')
    if not (os.environ.get('DISPLAY') or os.environ.get('WAYLAND_DISPLAY')):
        raise OSError('no DISPLAY/WAYLAND_DISPLAY desktop session')
    return shutil.which('xdg-open')


def open_launcher(launcher):
    """Hand ONLY the private file URI to the opener. Returns None or a failure reason."""
    try:
        opener = find_opener()
        if opener is None:
            return 'no xdg-open/open on PATH'
        result = subprocess.run([opener, Path(launcher).as_uri()], shell=False, stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10,
                                env={k: v for k, v in os.environ.items() if k != 'BROWSER'})
    except subprocess.TimeoutExpired:
        return 'opener timed out'
    except OSError as exc:
        return str(exc)
    return None if result.returncode == 0 else 'opener exited with status %d' % result.returncode


def sd_notify(message):
    """Stdlib systemd readiness; silent when not under systemd."""
    address = os.environ.get('NOTIFY_SOCKET')
    if not address or address[0] not in '/@':
        return
    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sock:
        sock.sendto(message.encode(), '\0' + address[1:] if address[0] == '@' else address)


def run_service(vps, state):
    """Block until SIGTERM/SIGINT. Never opens a browser."""
    manager = ManagementServer(vps, state)
    old = {}
    try:
        manager.write_launcher()
        for sig in (signal.SIGINT, signal.SIGTERM):
            old[sig] = signal.signal(sig, lambda *_: manager.stopping.set())
        manager._serving = True
        thread = threading.Thread(target=manager.serve_forever, name='vps-manage-http')
        thread.start()
        sd_notify('READY=1')
        manager.stopping.wait()
        manager.close()
        thread.join()
        return 0
    finally:
        for sig, handler in old.items():
            signal.signal(sig, handler)
        if not manager.stopping.is_set():
            manager.close()


def run_foreground(vps, state, runtime_carrier, ready, prepare=None, mihomo=None):
    """Serve for this call's lifetime; SIGINT/SIGTERM stop it. prepare() runs once this process holds the session lock and port,
    before anything is written or announced; ready(launcher_path, origin) runs once bound and written.

    Everything is released in finally. An active native job gets STOP_GRACE to write its receipt, then its
    process group is terminated; the retained job record is recovery_required and nothing is replayed.
    """
    manager = ManagementServer(vps, state, runtime_carrier=runtime_carrier, runtime_mihomo=mihomo)
    old, thread = {}, None
    try:
        if prepare is not None:
            prepare()
        manager.bind_target()
        launcher = manager.write_launcher()
        for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):  # HUP: terminal closed
            old[sig] = signal.signal(sig, lambda *_: manager.stopping.set())
        manager._serving = True
        thread = threading.Thread(target=manager.serve_forever, name='vps-manage-http')
        thread.start()
        ready(launcher, manager.origin)
        manager.stopping.wait()
        return 0
    finally:
        for sig, handler in old.items():
            signal.signal(sig, handler)
        manager.close()
        if thread is not None:
            thread.join()


def parser():
    p = argparse.ArgumentParser(prog='manage.py', description='internal persistent manager; use manage.sh')
    p.add_argument('--state-dir', type=Path, default=vps_module.DEFAULT_STATE)
    p.add_argument('--job', action='store_true', help=argparse.SUPPRESS)
    p.add_argument('--serve', action='store_true', help=argparse.SUPPRESS)
    return p


def main(argv=None):
    # The long-lived HTTP service must never own the native state flock;
    # each bounded --job request takes that lock exactly once.
    args = parser().parse_args(argv)
    if args.job == args.serve:
        print('Use manage.sh; manage.py accepts exactly one of the internal --job or --serve.', file=sys.stderr)
        return 2
    os.umask(0o077)
    state = args.state_dir.expanduser().absolute()
    try:
        return machine_main(vps_module, state) if args.job else run_service(vps_module, state)
    except (vps_module.host.Error, manage_auth.AuthError, Refusal, OSError) as exc:
        print('Error: %s' % exc, file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())

"""Session-scoped loopback browser bridge; native transactions remain in vps.py."""
from __future__ import annotations

import argparse
import contextlib
import datetime
import fcntl
import hashlib
import html
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import secrets
import signal
import stat
import shutil
import subprocess
import sys
import threading
import time
import vps as vps_module

MAX_BODY = 8192
JOB_SECONDS = 1200
CLEANUP_MARGIN = 30
SESSION_SECONDS = 45 * 60
HEX32 = re.compile(r'[0-9a-f]{32}\Z')
HEX64 = re.compile(r'[0-9a-f]{64}\Z')
NAME = re.compile(r'[a-z][a-z0-9-]{0,31}\Z')
ACTIONS = {'add', 'rename', 'reset', 'remove'}
ASSETS = {'/': ('manage.html', 'text/html; charset=utf-8'),
          '/manage.html': ('manage.html', 'text/html; charset=utf-8'),
          '/manage.js': ('manage.js', 'text/javascript; charset=utf-8'),
          '/manage.css': ('manage.css', 'text/css; charset=utf-8'),
          '/dashboard.css': ('dashboard.css', 'text/css; charset=utf-8')}
CSP = ("default-src 'self'; connect-src 'self'; img-src 'self' blob:; script-src 'self'; "
       "style-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")


STOPPED_ENTRY = ('<!doctype html><!-- quick-deploy managed launcher -->'
                 '<meta charset="utf-8"><title>管理会话已结束</title>'
                 '<p>管理会话已结束；在本目录运行 bash manage.sh，然后重新打开本页</p>\n').encode()


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
    """One session, one worker, no queue. executor is an in-process test seam."""
    def __init__(self, vps, state, *, independent_carrier=False, ssh_proxy=None,
                 mihomo=None, executor=None):
        self.vps, self.state = vps, Path(state).expanduser().absolute()
        vps.private_dir(self.state)
        self.jobs_dir = vps.private_dir(self.state / 'manage-jobs')
        self.session_lock = None
        self.session_id = secrets.token_hex(16)
        self.launch_token, self.bearer = secrets.token_hex(32), secrets.token_hex(32)
        self.expires = time.monotonic() + SESSION_SECONDS
        self.ssh_proxy = (vps.ssh_proxy.parse_proxy(getattr(ssh_proxy, 'url', ssh_proxy)).url
                          if ssh_proxy else None)
        self.mihomo = vps.resolve_mihomo(mihomo) if independent_carrier else None
        self.independent_carrier = bool(independent_carrier)
        self.executor = executor
        self.mutex = threading.RLock()
        self.stopping = threading.Event()
        self.worker = None
        self.process = None
        self.jobs, self.requests = {}, {}
        self.last_job = None
        self.unknown = False
        self.launcher = None
        self._launcher_bytes = None
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

            def setup(self):
                super().setup()
                self.connection.settimeout(5)

            def log_message(self, *_):
                pass

            def _send(self, status, value, content_type='application/json'):
                payload = (json.dumps(value, separators=(',', ':')).encode()
                           if content_type == 'application/json' else value)
                self.send_response(status)
                for name, val in [('Content-Type', content_type), ('Content-Length', str(len(payload))),
                                  ('Cache-Control', 'no-store'), ('Referrer-Policy', 'no-referrer'),
                                  ('X-Content-Type-Options', 'nosniff'), ('X-Frame-Options', 'DENY'),
                                  ('Content-Security-Policy', CSP), ('Connection', 'close')]:
                    self.send_header(name, val)
                self.end_headers()
                self.close_connection = True
                try:
                    self.wfile.write(payload)
                except (OSError, TimeoutError):
                    pass

            def _guard(self, post=False, session=False):
                if (self.headers.get_all('Host') != [manager.authority] or
                        any(key.lower() in {'forwarded', 'x-forwarded-host', 'x-forwarded-port',
                                            'x-forwarded-proto'} for key in self.headers)):
                    raise Refusal('invalid_host', 421)
                origins = self.headers.get_all('Origin') or []
                if (post and origins != [manager.origin]) or (not post and origins not in ([], [manager.origin])):
                    raise Refusal('invalid_origin', 403)
                if time.monotonic() >= manager.expires or manager.stopping.is_set():
                    raise Refusal('session_expired', 401)
                if post and self.headers.get_all('Content-Type') != ['application/json']:
                    raise Refusal('json_required', 415)
                if not session and self.path.startswith('/api/'):
                    auth = self.headers.get_all('Authorization') or []
                    if len(auth) != 1 or not secrets.compare_digest(auth[0], 'Bearer ' + manager.bearer):
                        raise Refusal('unauthorized', 401)

            def _body(self):
                lengths = self.headers.get_all('Content-Length') or []
                if (len(lengths) != 1 or not re.fullmatch(r'[0-9]+', lengths[0]) or
                        self.headers.get_all('Transfer-Encoding')):
                    raise Refusal('invalid_length', 400)
                length = int(lengths[0])
                if length > MAX_BODY:
                    raise Refusal('body_too_large', 413)
                try:
                    data = self.rfile.read(length)
                    if len(data) != length:
                        raise ValueError()
                    return strict_json(data)
                except (ValueError, UnicodeError, RecursionError, OSError):
                    raise Refusal('invalid_json', 400) from None

            def do_GET(self):
                try:
                    self._guard()
                    if self.path in ASSETS:
                        name, kind = ASSETS[self.path]
                        self._send(200, (vps.MODULE / 'assets' / name).read_bytes(), kind)
                    elif self.path == '/api/state':
                        self._send(200, manager.snapshot())
                    elif re.fullmatch(r'/api/jobs/[0-9a-f]{32}', self.path):
                        with manager.mutex:
                            job = manager.jobs.get(self.path.rsplit('/', 1)[1])
                            if job is None:
                                raise Refusal('unknown_job', 404)
                            self._send(200, manager.public_job(job))
                    else:
                        raise Refusal('not_found', 404)
                except Refusal as exc:
                    self._send(exc.status, {'error': error(exc.code)})
                except Exception:
                    self._send(500, {'error': error('read_failed')})

            def do_POST(self):
                try:
                    session = self.path == '/api/session'
                    self._guard(post=True, session=session)
                    body = self._body()
                    if session:
                        with manager.mutex:
                            if (not isinstance(body, dict) or set(body) != {'launch_token'} or
                                    not isinstance(body['launch_token'], str) or manager.launch_token is None or
                                    not secrets.compare_digest(body['launch_token'], manager.launch_token)):
                                raise Refusal('invalid_launch_token', 401)
                            self._send(200, dict({'token': manager.bearer, 'session_id': manager.session_id,
                                                  'expires_in': max(0, int(manager.expires - time.monotonic()))},
                                                 **manager.timing()))
                    elif self.path in {'/api/jobs', '/api/recover'}:
                        job = manager.admit(body, recover=self.path == '/api/recover')
                        self._send(202, {'job_id': job['job_id'], 'state': job['state']})
                    else:
                        raise Refusal('not_found', 404)
                except Refusal as exc:
                    self._send(exc.status, {'error': error(exc.code)})
                except Exception:
                    self._send(500, {'error': error('admission_failed')})

            def do_OPTIONS(self):
                try:
                    self._guard()
                    raise Refusal('method_not_allowed', 405)
                except Refusal as exc:
                    self._send(exc.status, {'error': error(exc.code)})

        self.httpd = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
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

    def timing(self):
        """Optional absolute deadlines from the monotonic session clock; wall time is display only."""
        remaining = self.expires - time.monotonic()
        iso = lambda seconds: datetime.datetime.fromtimestamp(
            time.time() + seconds, datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
        return {'session_ends_at': iso(remaining),
                'new_jobs_until': iso(remaining - JOB_SECONDS - CLEANUP_MARGIN),
                'job_limit_s': JOB_SECONDS, 'cleanup_margin_s': CLEANUP_MARGIN}

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
            reason = ('session_expired' if self.expires <= time.monotonic() else
                      'independent_carrier_required' if not self.independent_carrier else
                      'busy' if busy else 'pending' if pending['present'] else
                      source.get('error', {}).get('code'))
            return {'schema': 1, 'session_id': self.session_id,
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
            if self.stopping.is_set() or self.expires - time.monotonic() < JOB_SECONDS + CLEANUP_MARGIN:
                raise Refusal('insufficient_session_time')
            if not self.independent_carrier:
                raise Refusal('independent_carrier_required')
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
            envelope.update(ssh_proxy=self.ssh_proxy, mihomo=self.mihomo)
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
        template = (module / 'assets' / 'manager-launcher.html').read_text()
        manage_url = self.origin + '/#' + self.launch_token
        data = (template.replace('{{MANAGE_URL}}', html.escape(manage_url, quote=True))
                .replace('{{MANAGE_ORIGIN}}', html.escape(self.origin, quote=True))
                .replace('{{MANAGE_EXPIRES}}', '45 minutes')).encode()
        with self.vps.directory_fd(module, private=False) as directory:
            info = os.fstat(directory)
            if info.st_uid != os.getuid():
                raise Refusal('launcher_owner')
            # No overwrite of an unrelated user file, even if it is mode0600.
            try:
                existing = self.vps.read_at(directory, destination.name)
            except FileNotFoundError:
                pass
            else:
                if not existing.startswith(b'<!doctype html><!-- quick-deploy managed launcher -->'):
                    raise Refusal('launcher_conflict')
            data = data.replace(b'<!doctype html>', b'<!doctype html><!-- quick-deploy managed launcher -->', 1)
            self._replace_entry(directory, temporary, data)
        self.launcher, self._launcher_bytes = destination, data
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

    def _stop_launcher(self):
        # Replace only our own unchanged entry; the secret-bearing bytes never persist.
        with self.vps.directory_fd(self.launcher.parent, private=False) as directory:
            if self.vps.read_at(directory, self.launcher.name) == self._launcher_bytes:
                self._replace_entry(directory, '.index-' + secrets.token_hex(12) + '.tmp', STOPPED_ENTRY)

    def serve_forever(self):
        self._serving = True
        try:
            self.httpd.serve_forever(poll_interval=0.1)
        finally:
            self._serving = False

    def close(self):
        self.stopping.set()
        self.launch_token, self.bearer = None, secrets.token_hex(32)
        # Called by a different thread than serve_forever (see run()).
        if getattr(self, '_serving', False):
            self.httpd.shutdown()
        self.httpd.server_close()
        if self.worker is not None:
            self.worker.join(JOB_SECONDS + 15 if self.executor else 15)
            if self.worker.is_alive():
                raise RuntimeError('test executor did not stop')
        if self.launcher is not None:
            try:
                self._stop_launcher()
            except (OSError, self.vps.host.Error):
                pass
        if self.session_lock is not None:
            os.close(self.session_lock)
            self.session_lock = None


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


def run(vps, state, args):
    manager = ManagementServer(vps, state, independent_carrier=args.independent_carrier,
                               ssh_proxy=args.ssh_proxy, mihomo=args.mihomo)
    old = {}
    try:
        launcher = manager.write_launcher()
        print('Local management launcher: ' + str(launcher))
        print('Session origin: ' + manager.origin)
        print('Writes enabled with independent-carrier acknowledgement.' if manager.independent_carrier else
              'Read-only: restart with --independent-carrier and an explicitly independent SSH route to enable writes.')
        for sig in (signal.SIGINT, signal.SIGTERM):
            old[sig] = signal.signal(sig, lambda *_: manager.stopping.set())
        manager._serving = True
        thread = threading.Thread(target=manager.serve_forever, name='vps-manage-http')
        thread.start()
        if getattr(args, 'no_open', False):
            print('Automatic open disabled (--no-open); open the launcher above manually.')
        else:
            reason = open_launcher(launcher)
            if reason is None:
                print('Requested the system opener for the launcher; if no page appears, open it manually.')
            else:
                print('Could not open a browser automatically (%s). Open this file manually; the local server keeps running: %s'
                      % (reason, launcher))
        while not manager.stopping.wait(0.2):
            if time.monotonic() >= manager.expires:
                break
        manager.close()
        thread.join()
        return 0
    finally:
        for sig, handler in old.items():
            signal.signal(sig, handler)
        if not manager.stopping.is_set():
            manager.close()


def parser():
    p = argparse.ArgumentParser(prog='manage.sh', description='session-scoped local browser credential management')
    p.add_argument('--state-dir', type=Path, default=vps_module.DEFAULT_STATE)
    p.add_argument('--ssh-proxy', type=vps_module.proxy_argument, help='startup-only independent loopback SSH carrier; default direct')
    p.add_argument('--mihomo', help='native core executable, fixed for this session')
    p.add_argument('--independent-carrier', action='store_true', help='acknowledge SSH route is independent of EVERY managed VPS account/entry')
    p.add_argument('--no-open', action='store_true', help='do not open a browser; print the launcher path only')
    p.add_argument('--job', action='store_true', help=argparse.SUPPRESS)
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    vps_module.SSH_PROXY.set(args.ssh_proxy)
    os.umask(0o077)
    state = args.state_dir.expanduser().absolute()
    try:
        return machine_main(vps_module, state) if args.job else run(vps_module, state, args)
    except (vps_module.host.Error, Refusal, OSError) as exc:
        print('Error: %s' % exc, file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())

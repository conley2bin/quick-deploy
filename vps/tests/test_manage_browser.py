"""Real headless Chrome drives the real rendered file:// manager entry against the real
ManagementServer. Only machine_execute('state') is faked (3 nodes, 2 subscriptions, fake links).
Chrome is controlled over --remote-debugging-pipe (no TCP debug port); no screenshots."""
import fcntl
import json
import os
from pathlib import Path
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'lib'))
sys.path.insert(0, str(ROOT / 'tests'))
import manage
import manage_auth
import manage_entry
import vps
from test_dashboard_labels import fixture, THREE_CASES, MALICIOUS_LABEL

CHROME = shutil.which('google-chrome') or shutil.which('chromium')


def git(*args, cwd):
    subprocess.run(['git', *args], cwd=cwd, check=True, capture_output=True, timeout=30)


class Chrome:
    def __init__(self, tmp):
        self.events, self.responses, self.lock = [], {}, threading.Condition()
        profile = tempfile.mkdtemp(prefix='c', dir=tmp)
        os.chmod(profile, 0o700)
        r1, w1 = os.pipe()   # browser reads commands
        r2, w2 = os.pipe()   # browser writes replies
        keep = [fcntl.fcntl(fd, fcntl.F_DUPFD, 100) for fd in (r1, w2)]
        self.argv = [CHROME, '--headless=new', '--no-sandbox', '--no-first-run', '--user-data-dir=' + profile,
                     '--remote-debugging-pipe', 'about:blank']
        env = dict(os.environ, TMPDIR=tmp)
        self.proc = subprocess.Popen(
            self.argv, env=env, close_fds=False, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=open(os.path.join(tmp, 'chrome.err'), 'wb'),
            preexec_fn=lambda: (os.dup2(keep[0], 3), os.dup2(keep[1], 4)))
        os.close(r1), os.close(w2), os.close(keep[0]), os.close(keep[1])
        self.out, self.inp, self.next = w1, r2, 0
        threading.Thread(target=self._read, daemon=True).start()
        self.session = None

    def _read(self):
        buf = b''
        while True:
            try:
                chunk = os.read(self.inp, 65536)
            except OSError:
                return
            if not chunk:
                return
            buf += chunk
            while b'\0' in buf:
                raw, buf = buf.split(b'\0', 1)
                message = json.loads(raw)
                with self.lock:
                    if 'id' in message:
                        self.responses[message['id']] = message
                    else:
                        self.events.append(message)
                    self.lock.notify_all()

    def call(self, method, params=None, session=True, timeout=10):
        self.next += 1
        message = {'id': self.next, 'method': method, 'params': params or {}}
        if session and self.session:
            message['sessionId'] = self.session
        os.write(self.out, json.dumps(message).encode() + b'\0')
        end = time.monotonic() + timeout
        with self.lock:
            while self.next not in self.responses:
                if not self.lock.wait(max(0.01, end - time.monotonic())) and time.monotonic() > end:
                    raise TimeoutError(method)
            reply = self.responses.pop(self.next)
        if 'error' in reply:
            raise RuntimeError(method + ': ' + json.dumps(reply['error']))
        return reply['result']

    def open(self):
        target = self.call('Target.createTarget', {'url': 'about:blank'}, session=False)['targetId']
        self.session = self.call('Target.attachToTarget', {'targetId': target, 'flatten': True}, session=False)['sessionId']
        for domain in ('Page', 'Runtime', 'Network', 'Log'):
            self.call(domain + '.enable')

    def js(self, expression):
        result = self.call('Runtime.evaluate', {'expression': expression, 'returnByValue': True, 'awaitPromise': True})
        if 'exceptionDetails' in result:
            raise RuntimeError(json.dumps(result['exceptionDetails'])[:300])
        return result['result'].get('value')

    def wait(self, expression, seconds=10):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            try:
                if self.js(expression):
                    return True
            except RuntimeError:
                pass
            time.sleep(0.1)
        return False

    def close(self):
        try:
            self.call('Browser.close', session=False, timeout=3)
        except Exception:
            pass
        try:
            self.proc.wait(5)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait()


CARDS = "document.getElementById('subscription-links').children.length"
READY = f"{CARDS} === 2"


def page_facts(chrome):
    return chrome.js("""({href: location.href, cards: document.getElementById('subscription-links').children.length,
        name: document.getElementById('device-name').textContent, text: document.body.innerText,
        status: document.getElementById('session-status').textContent,
        sessionStorage: sessionStorage.length, localStorage: localStorage.length, cookie: document.cookie,
        devices: document.getElementById('device-list').innerText,
        onerror: document.querySelectorAll('[onerror]').length, scripts: [...document.scripts].filter(s => s.src).length, links: document.querySelectorAll('link[href]').length})""")


class ManageBrowser(unittest.TestCase):
    @unittest.skipUnless(CHROME, 'Chrome required')
    def test_real_file_ui_reload_restart_and_impostor(self):
        tmp = tempfile.mkdtemp(prefix='qb', dir='/tmp')
        os.chmod(tmp, 0o700)
        trace, bodies, servers, chrome = [], [], [], None
        try:
            # Private full-git module copy so the real write_launcher publishes the real entry.
            repo = Path(tmp) / 'repo'
            shutil.copytree(ROOT, repo / 'vps', ignore=shutil.ignore_patterns('__pycache__', 'index.html'))
            (repo / '.gitignore').write_text('/vps/index.html\n/vps/.index-*.tmp\n__pycache__/\n')
            git('init', '-q', cwd=repo)
            git('add', '.gitignore', 'vps', cwd=repo)
            git('-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-qm', 'copy', cwd=repo)
            module = vps.MODULE
            data, _, _, _ = fixture(THREE_CASES)
            data['model']['devices'][0]['links'][1]['label'] = MALICIOUS_LABEL
            model = data['model']
            self.assertEqual(len(model['devices'][0]['links']), 5)

            def fake_state(model):
                return lambda _v, _s, _e: {'schema': 1, 'outcome': 'ok', 'pending': {'present': False},
                                           'generation_before': None, 'generation_after': 'a' * 64, 'model': model}

            def start(state_name, port, model):
                state = Path(tmp) / state_name
                with mock.patch.object(vps, 'MODULE', repo / 'vps'), \
                        mock.patch.object(manage, 'machine_execute', fake_state(model)):
                    server = manage.ManagementServer(vps, state, port=port)
                handler = server.httpd.RequestHandlerClass
                if not hasattr(handler, '_traced'):
                    handler._traced = True
                    original, raw_body = handler._handle, handler._raw_body

                    def traced(self, method):
                        seen = len(bodies)
                        original(self, method)
                        trace.append((method, self.path, self.headers.get('Origin'), bodies[seen:] and bodies[-1]))

                    def keep(self, post):
                        value = raw_body(self, post)
                        bodies.append(value)
                        return value
                    handler._handle, handler._raw_body = traced, keep
                    options = handler.do_OPTIONS

                    def traced_options(self):
                        options(self)
                        trace.append(('OPTIONS', self.path, self.headers.get('Origin'), None))
                    handler.do_OPTIONS = traced_options
                # machine_execute stays patched for the server's lifetime (snapshot looks it up per call).
                patcher = mock.patch.object(manage, 'machine_execute', fake_state(model))
                patcher.start()
                self.addCleanup(patcher.stop)
                threading.Thread(target=server.serve_forever, daemon=True).start()
                servers.append(server)
                return server

            a = start('stateA', 0, model)
            port = a.httpd.server_port
            with mock.patch.object(vps, 'MODULE', repo / 'vps'):
                entry = a.write_launcher()
            entry = Path(entry)
            self.assertEqual(entry.stat().st_mode & 0o777, 0o600)
            key = a.bootstrap.hex()
            html = entry.read_text()
            self.assertIn(key, html)
            url = 'file://' + str(entry)
            self.assertNotIn(key, url)
            epoch1 = a.epoch

            chrome = Chrome(tmp)
            chrome.open()
            chrome.call('Page.navigate', {'url': url})
            self.assertTrue(chrome.wait(READY), page_facts(chrome))
            first = page_facts(chrome)
            # F5: same file again.
            chrome.call('Page.reload')
            time.sleep(0.3)
            self.assertTrue(chrome.wait(READY), 'F5 render')
            second = page_facts(chrome)
            hellos_a = [t for t in trace if t[1] == '/api/hello']
            self.assertGreaterEqual(len(hellos_a), 2)
            self.assertTrue(all(t[2] == 'null' for t in trace if t[1].startswith('/api/')))
            self.assertTrue(any(m == 'OPTIONS' for m, *_ in trace))
            self.assertTrue(any(m == 'GET' and p == '/api/state' for m, p, *_ in trace))

            # Normal close, reopen on the SAME port: new epoch, same private key material.
            a.close()
            n_before = len(trace)
            b = start('stateA', port, model)
            self.assertEqual(b.httpd.server_port, port)
            self.assertNotEqual(b.epoch, epoch1)
            chrome.js("document.getElementById('refresh-state').click(), 1")
            self.assertTrue(chrome.wait(f"{READY} && document.getElementById('session-status').textContent.includes('已连接')"))
            after_restart = page_facts(chrome)
            self.assertTrue(any(t[1] == '/api/hello' for t in trace[n_before:]))
            chrome.call('Page.reload')
            time.sleep(0.3)
            self.assertTrue(chrome.wait(READY), 'F5 after restart')
            third = page_facts(chrome)

            # No carrier configured: material visible, writes disabled with a stated reason.
            writes_off = chrome.js("[...document.querySelectorAll('#add-device,#rename-device,#reset-device,#remove-device')].every(e => e.disabled)")
            reason = chrome.js("document.getElementById('write-reason').textContent")

            # Impostor: other master on the same port; the page's bootstrap key must reject it.
            b.close()
            impostor_model = json.loads(json.dumps(model))
            impostor_model['devices'][0]['name'] = 'impostor'
            c = start('stateB', port, impostor_model)
            chrome.call('Page.reload')
            time.sleep(1.5)
            impostor = page_facts(chrome)
            c.close()

            with chrome.lock:
                events = list(chrome.events)
            requests = [e['params']['request']['url'] for e in events if e['method'] == 'Network.requestWillBeSent']
            problems = [e for e in events if e['method'] == 'Runtime.exceptionThrown' or
                        (e['method'] == 'Log.entryAdded' and e['params']['entry']['level'] == 'error' and
                         'ERR_CONNECTION_REFUSED' not in json.dumps(e)) or
                        (e['method'] == 'Runtime.consoleAPICalled' and e['params']['type'] == 'error')]
            err = Path(tmp, 'chrome.err').read_text(errors='replace')
            leaks = {'url': any(key in r for r in requests), 'bodies': any(key.encode() in x for x in bodies),
                     'argv': any(key in x for x in chrome.argv), 'stderr': key in err,
                     'trace': key in json.dumps(trace, default=str)}
            report = {
                'first': {k: first[k] for k in ('cards', 'name', 'status', 'sessionStorage', 'localStorage', 'cookie', 'devices', 'scripts', 'links')},
                'second_cards': second['cards'], 'after_restart': {k: after_restart[k] for k in ('cards', 'status')},
                'third_cards': third['cards'], 'writes_disabled': writes_off, 'write_reason': reason,
                'impostor': {k: impostor[k] for k in ('cards', 'name', 'status', 'devices')},
                'impostor_text_has_fake': 'impostor' in impostor['text'],
                'requests': sorted({r.split('?')[0] for r in requests}), 'trace_len': len(trace),
                'trace_paths': sorted({f'{m} {p}' for m, p, *_ in trace}),
                'problems': [json.dumps(p)[:300] for p in problems], 'leaks': leaks,
                'malicious_label_in_text': MALICIOUS_LABEL in first['text'],
                'onerror_elements': first['onerror']}
            Path(os.environ.get('QD_BROWSER_REPORT', os.devnull)).write_text(json.dumps(report, ensure_ascii=False, indent=1))
            print(json.dumps(report, ensure_ascii=False, indent=1))

            self.assertEqual((first['cards'], second['cards'], third['cards']), (2, 2, 2))
            self.assertEqual(first['name'], 'alpha')
            self.assertEqual((first['sessionStorage'], first['localStorage'], first['cookie']), (0, 0, ''))
            self.assertEqual((first['scripts'], first['links'], first['onerror']), (0, 0, 0))
            self.assertTrue(all(r.startswith(('file://', 'http://127.0.0.1:%d/api/' % port)) or r == 'about:blank' for r in requests), requests)
            self.assertFalse(any('/manage.' in r or '.css' in r or '.js' in r for r in requests))
            self.assertNotRegex(first['text'], r'45|令牌|已丢失')
            self.assertTrue(writes_off)
            self.assertEqual(impostor['cards'], 0)
            self.assertNotIn('impostor', impostor['text'])
            self.assertFalse(any(leaks.values()), leaks)
            self.assertEqual(problems, [])
        finally:
            if chrome:
                chrome.close()
            for server in servers:
                try:
                    server.close()
                except Exception:
                    pass
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == '__main__':
    unittest.main()

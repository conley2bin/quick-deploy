"""Render the private, self-contained manager entry (vps/index.html).

The entry embeds the trusted manager code, so no script or style is ever fetched
from the loopback HTTP service. Its CSP names the exact hashes of the embedded
bytes and the one API origin it may contact.
"""
import base64
import hashlib
import json
import re

_ORIGIN = re.compile(r'http://127\.0\.0\.1:[0-9]{1,5}\Z')
_HEX64 = re.compile(r'[0-9a-f]{64}\Z')
_STYLESHEET = '<link rel="stylesheet" href="manage.css">'
_SCRIPT = '<script src="manage.js" defer></script>'
_IMPORT = '@import url("/dashboard.css");'


def _hash(text):
    return "'sha256-" + base64.b64encode(hashlib.sha256(text.encode()).digest()).decode() + "'"


def _replace_once(text, old, new):
    if text.count(old) != 1:
        raise ValueError(f'manager page must contain exactly one {old!r}')
    return text.replace(old, new)


def render_entry(assets, origin, bootstrap_hex):
    if not _ORIGIN.match(origin) or not 0 < int(origin.rsplit(':', 1)[1]) < 65536:
        raise ValueError('origin must be http://127.0.0.1:<port>')
    if not _HEX64.match(bootstrap_hex):
        raise ValueError('bootstrap key must be 64 lowercase hex characters')
    css = _replace_once(assets['manage.css'], _IMPORT, assets['dashboard.css'])
    script = assets['manage.js']
    if '@import' in css or '</style' in css.lower():
        raise ValueError('manager CSS must be self-contained')
    if '</script' in script.lower() or '<!--' in script:
        raise ValueError('manager script cannot be embedded safely')
    bootstrap = json.dumps({'version': 1, 'origin': origin, 'bootstrap_key': bootstrap_hex},
                           separators=(',', ':')).replace('<', '\\u003c')
    csp = ("default-src 'none'; script-src {}; style-src {}; connect-src {}; img-src data: blob:; "
           "base-uri 'none'; object-src 'none'; form-action 'none'").format(_hash(script), _hash(css), origin)
    page = assets['manage.html']
    page = _replace_once(page, _STYLESHEET, f'<meta http-equiv="Content-Security-Policy" content="{csp}">\n<style>{css}</style>')
    page = _replace_once(page, _SCRIPT + '\n', '')
    page = _replace_once(page, '</body>', f'<script type="application/json" id="manage-bootstrap">{bootstrap}</script>\n<script>{script}</script>\n</body>')
    return page.encode('utf-8')

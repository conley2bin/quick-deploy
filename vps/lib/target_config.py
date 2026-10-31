"""Plain, editable VPS target + base domain file (MODULE/config).

Two ASCII `KEY=value` lines (VPS_SSH, DOMAIN). Nothing is sourced, expanded, resolved or connected to.
Blank lines and whole-line `#` comments are tolerated. No secrets belong in this public file.
"""
import re

import host

FILE_NAME = 'config'
KEYS = ('VPS_SSH', 'DOMAIN')
MAX_BYTES = 4096
# user@host or a verified OpenSSH alias / public IPv4 literal; never a command, port, URL or option.
SSH_TARGET = re.compile(r'(?:[A-Za-z_][A-Za-z0-9_-]*@)?[A-Za-z0-9][A-Za-z0-9_.-]*')


def _fail(detail):
    return host.Error(f'{FILE_NAME}: {detail}; expected exactly VPS_SSH=user@public-IPv4-or-alias and DOMAIN=example.com')


def ssh_target(value):
    if not SSH_TARGET.fullmatch(value):
        raise _fail('VPS_SSH must be user@host or an SSH alias (no command, port, URL or spaces)')
    return value


def domain_value(value):
    if re.fullmatch(r'[0-9.]+', value) or value.rsplit('.', 1)[-1].isdigit():
        raise _fail('DOMAIN must be a DNS base domain, not an IP address')
    try:
        return host.hostname(value)
    except host.Error:
        raise _fail('DOMAIN must be a plain DNS base domain such as example.com') from None


def _parse(module):
    path = module / FILE_NAME
    try:
        raw = path.read_bytes()
        if len(raw) > MAX_BYTES:
            raise _fail('file is too large')
        text = raw.decode('ascii')
    except (OSError, UnicodeDecodeError) as exc:
        raise host.Error(f'{FILE_NAME} is missing or unreadable ASCII; edit it or pass the explicit option') from exc
    if '\r' in text.replace('\r\n', ''):
        raise _fail('stray carriage return')
    values = {}
    for number, line in enumerate(text.replace('\r\n', '\n').split('\n'), 1):
        if not line or line.startswith('#'):
            continue
        key, sep, value = line.partition('=')
        if not sep or key not in KEYS:
            raise _fail(f'line {number} is not a known KEY=value')
        if key in values:
            raise _fail(f'duplicate {key}')
        if not value or re.search(r'[^\x21-\x7e]', value):
            raise _fail(f'{key} must be a non-empty value without spaces or control characters')
        values[key] = value
    missing = [key for key in KEYS if key not in values]
    if missing:
        raise _fail('missing ' + ', '.join(missing))
    return values


def read_config(module):
    """Both fields fully validated."""
    values = _parse(module)
    return {'VPS_SSH': ssh_target(values['VPS_SSH']), 'DOMAIN': domain_value(values['DOMAIN'])}


def default_ssh(module):
    """Connect path: both keys must be present and well-formed lines, but only VPS_SSH's value is judged."""
    return ssh_target(_parse(module)['VPS_SSH'])


def base_domain(module):
    return domain_value(_parse(module)['DOMAIN'])

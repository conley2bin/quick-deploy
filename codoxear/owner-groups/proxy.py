#!/usr/bin/env python3
"""Legacy entry/import facade; transport and process ownership live in web-host.

Import callers keep the owner-only create_app/managed_app fixture interface.
The CLI, like run_server.sh, explicitly enables both production features.
"""
import importlib.util
from pathlib import Path
import sys

_HOST_PATH = Path(__file__).resolve().parent.parent / 'web-host/host.py'
if 'codoxear_web_host' not in sys.modules:
    _spec = importlib.util.spec_from_file_location('codoxear_web_host', _HOST_PATH)
    _host = importlib.util.module_from_spec(_spec)
    sys.modules[_spec.name] = _host
    _spec.loader.exec_module(_host)
else:
    _host = sys.modules['codoxear_web_host']
_owner = _host.load_feature('owner-groups')

# Preserve previously imported helpers, AppKeys and test/demo entrypoints.
CONFIG = _host.CONFIG
CLIENT = _host.CLIENT
HOST = _host.HOST
CIMultiDict = _host.CIMultiDict
asyncio = _host.asyncio
socket = _host.socket
NAMESPACE = _owner.NAMESPACE
asset = _owner.asset
read_registry = _owner.read_registry
session_linkage = _owner.session_linkage
snapshot = _owner.snapshot
feature = _owner.feature
proxy = _host.proxy
headers_without_hops = _host.headers_without_hops
allows_injection = _host.allows_injection
same_origin = _host.same_origin
json_response = _host.json_response
limited_read = _host.limited_read
front_socket = _host.front_socket
wrapper = _host.wrapper
main = _host.main


def inject(body, headers, prefix):
    return _host.inject(body, headers, prefix, {NAMESPACE: asset()})


def create_app(upstream, prefix='', projector=read_registry):
    app = _host.create_app(upstream, prefix, features=(_owner,))
    app[_owner.STATE]['projector'] = projector
    app[_owner.STATE]['demo'] = lambda: app[CONFIG].get('demo', False)
    return app


def managed_app():
    return _host.managed_app(features=(_owner,), source=wrapper())


if __name__ == '__main__':
    main()

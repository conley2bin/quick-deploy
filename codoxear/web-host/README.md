# Codoxear shared Web host

`host.py` owns the single front HTTP listener, the private loopback native server,
streaming proxy and extension assets. The upstream checkout/package is unchanged.
Production explicitly installs `owner-groups/feature.py` and
`session-handoff/feature.py`; a missing module or asset is a startup error, never a
silently disabled feature. There is no plugin discovery or nested proxy.

## Feature contract

Each sibling exports `install(app, host)`. Install registers its namespace routes
and calls `host.add_script(namespace, body_bytes)`. Feature routes are installed
before the generic proxy. Feature-local background work belongs in
`app.cleanup_ctx`; request disconnects must not own durable workflow tasks.
The loader isolates sibling packages and does not modify `sys.path`.

Host context exposes:

- `prefix`: configured URL prefix, no trailing slash.
- `html_status`: `not-observed`, `injected`, or `unsupported-*`.
- `add_script(namespace, body)`: accepts bytes during installation only.
- `same_origin(request)` and `json_response(data, status=200)`: shared origin
  checks and no-store, no-CORS, same-origin response headers. Each feature must
  explicitly check origin and freshly authenticate its own private endpoints.
- `await upstream_json(method, path, *, cookie, body=None, timeout=30.0,
  limit=8*1024*1024)`: fixed relative `/api/...` path **without** the configured
  prefix. The host adds prefix and fixed loopback origin. Returns native status
  and decoded JSON, including non-2xx JSON; invalid/oversized/non-JSON responses
  raise `ValueError`, network/timeouts propagate transport exceptions. Redirects
  are never followed. The caller cookie is sent only on that call; a
  `DummyCookieJar` prevents upstream cookies from crossing browser callers.

Each script is a separate same-origin `defer` element, so `document.currentScript`
keeps its own namespace. Bytes and SHA256-derived versions freeze when the app is
constructed. Matching `app.js?v=<hash>` serves those exact immutable bytes;
unknown versions return 404. Bare `app.js` remains available with `no-store` for
legacy clients. Editing source files cannot change an already running host's
script bytes; an approved Web restart is required to load new assets.

Only recognized bounded native entry HTML is buffered. Unsupported HTML/CSP is
passed through unchanged; the host never relaxes CSP. Uploads, downloads and SSE
stream. Duplicate Set-Cookie, opaque encoded routes, native prefix/auth and
IPv6 dual-stack front behavior are preserved. WebSocket upgrades fail explicitly
with 501 because this host only supports the observed HTTP/SSE native protocol.

`owner-groups/proxy.py` keeps legacy imports and owner-only `create_app` /
`managed_app` fixtures. Its CLI delegates to the shared host and enables both
production features. Owner namespace, snapshot projection, status schema and
`X-Owner-Groups` response header remain compatible. Shared HTML status also uses
`X-Codoxear-Extensions`.

## Isolated tests

From `codoxear/`, after provisioning the shared venv with `./setup.sh`:

```bash
web-host/.venv/bin/python -m unittest discover -s web-host/tests -v
web-host/.venv/bin/python -m unittest discover -s owner-groups/tests -p 'test_*.py'
python3 -m unittest discover -s tests -v
node --test owner-groups/tests/core.test.cjs
# Optional installed upstream: read-only package, temporary HOME and private port.
CODOXEAR_TEST_PYTHON="$PWD/.runtime/venv/bin/python" \
  web-host/.venv/bin/python -m unittest discover -s owner-groups/tests -p test_real_upstream.py
```

Host tests inject explicit synthetic features; they require no session-handoff
module, live registry, user cookies or agents. Production defaults never install
those fakes. The optional native test starts only a temporary Web child, never a
broker/agent. Full handoff/browser integration belongs to the integrated feature
tests, not this isolated transport suite.

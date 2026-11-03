"""Fixed same-origin HTTP surface; the host owns proxy/auth transport and scripts."""
from __future__ import annotations

import hashlib
import importlib
import json
from pathlib import Path
import sys
import types

from aiohttp import web

# Host loads feature.py by path. Keep sibling imports private, including in tests
# where several isolated installations may coexist in one Python interpreter.
DIRECTORY = Path(__file__).resolve().parent
PACKAGE = "_codoxear_handoff_" + hashlib.sha256(str(DIRECTORY).encode()).hexdigest()[:16]
if PACKAGE not in sys.modules:
    package = types.ModuleType(PACKAGE)
    package.__path__ = [str(DIRECTORY)]
    sys.modules[PACKAGE] = package
engine_module = importlib.import_module(PACKAGE + ".engine")
Engine, HandoffError = engine_module.Engine, engine_module.HandoffError
ENGINE_KEY = web.AppKey("session_handoff_engine", Engine)
NAMESPACE = "__session_handoff__"
MAX_BODY = 8192


def install(app, host, *, state_root=None, timeouts=None, succession_service=None):
    """Only Python callers/tests can inject a root/deadline; HTTP cannot."""
    engine = Engine(host.upstream_json, state_root=state_root, timeouts=timeouts,
                    succession_service=succession_service)
    app[ENGINE_KEY] = engine

    async def lifecycle(_app):
        engine.open()
        try:
            yield
        finally:
            await engine.close()

    app.cleanup_ctx.append(lifecycle)
    scripts = b"\n;\n".join((DIRECTORY / "web" / name).read_bytes()
                            for name in ("core.js", "panel.js", "bootstrap.js"))
    host.add_script(NAMESPACE, scripts)

    async def handle(request):
        try:
            if not host.same_origin(request):
                raise HandoffError("cross_origin", "仅允许从当前原生页面操作", 403)
            # Auth is freshly checked on every state-bearing request, including
            # status, malformed input and deduplicated start. Cookie is never saved.
            cookie = request.headers.get("Cookie", "")
            rows = await engine.sessions(cookie)
            if request.content_type != "application/json":
                raise HandoffError("invalid_input", "请求必须使用 JSON", 400)
            if request.content_length is not None and request.content_length > MAX_BODY:
                raise HandoffError("invalid_input", "请求内容过长", 400)
            raw = bytearray()
            async for chunk in request.content.iter_chunked(MAX_BODY + 1):
                raw.extend(chunk)
                if len(raw) > MAX_BODY:
                    raise HandoffError("invalid_input", "请求内容过长", 400)
            try:
                data = json.loads(raw)
            except (ValueError, UnicodeError):
                raise HandoffError("invalid_input", "请求不是有效 JSON", 400) from None
            if not isinstance(data, dict):
                raise HandoffError("invalid_input", "请求必须是 JSON 对象", 400)
            action = request.match_info["action"]
            if action == "start":
                if set(data) - {"source_session_id", "request_id", "note"} or not {"source_session_id", "request_id"} <= data.keys():
                    raise HandoffError("invalid_input", "接力请求包含未支持的字段", 400)
                source = data["source_session_id"]
                note = data.get("note", "")
                if (not isinstance(source, str) or not source.strip() or len(source) > 512
                        or any(ord(char) < 32 for char in source)
                        or not isinstance(note, str) or len(note) > 2000):
                    raise HandoffError("invalid_input", "会话标识或附注无效", 400)
                operation = engine.start(source, data["request_id"], note, rows=rows, cookie=cookie)
            else:
                if set(data) != {"operation_id"}:
                    raise HandoffError("invalid_input", "请求仅接受操作标识", 400)
                operation = (engine.continue_unreviewed(data["operation_id"], rows=rows, cookie=cookie)
                             if action == "continue" else await engine._offloop(
                                 lambda: engine.status(data["operation_id"], rows=rows)))
            return host.json_response({"operation": operation})
        except HandoffError as exc:
            return host.json_response({"error": exc.code, "message": exc.message}, status=exc.status)
        except engine_module.ContextError:
            return host.json_response({"error": "invalid_source", "message": "源日志无法形成可信交接边界"}, status=422)
        except Exception:
            # Neither native bodies nor local filesystem/exception details leave this seam.
            return host.json_response({"error": "handoff_unavailable", "message": "接力服务暂不可用"}, status=503)

    prefix = host.prefix.rstrip("/") + "/" + NAMESPACE
    route = prefix + "/{action:start|status|continue}"
    app.router.add_post(route, handle)

    async def wrong_method(_request):
        return host.json_response({"error": "method_not_allowed", "message": "接力接口仅接受 POST"}, status=405)

    # Claim other methods too, so the host's generic wildcard proxy cannot turn
    # a GET/OPTIONS into a different state-bearing endpoint or a CORS fallback.
    app.router.add_route("*", route, wrong_method)
    return engine

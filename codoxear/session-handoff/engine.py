"""One-shot Pi handoff, with durable intent and app-owned (not request-owned) work."""
from __future__ import annotations

import asyncio
from copy import deepcopy
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import stat
import tempfile
import time
import unicodedata
from urllib.parse import quote
from uuid import UUID, uuid4

from .context import ContextError, create_source_snapshot, prepare_artifacts, read_artifact
from .prompts import build_recovery_prompt, build_review_prompt, build_continuation_prompt

PUBLIC = ("id", "request_id", "source_session_id", "target_session_id", "status",
          "message", "reviewed", "can_continue_unreviewed", "updated_at", "error_code",
          "target_settings", "target_check")
MESSAGES = {
    "creating": "正在保存交接边界", "launching": "正在创建新的 Pi 会话",
    "recovering": "新会话正在独立恢复并整理理解报告", "waiting_source": "等待原会话空闲后审阅",
    "reviewing": "原会话正在审阅；原会话仅担任审阅者", "revising": "新会话正在核对审阅意见",
    "ready": "新会话已提交接续回执并继续工作", "unreviewed": "原会话未完成校对，可明确选择未经校对继续",
    "conflict": "目标存在分歧，需要用户确认", "creation_unknown": "创建结果不确定，未自动重建",
    "delivery_unknown": "命名或消息投递结果不确定，未自动重试", "interrupted": "服务中断，未重放任何创建、命名或发送",
    "failed": "接力未完成；请检查会话与交接产物",
}
FINISHED = {"ready", "conflict", "failed", "creation_unknown", "delivery_unknown", "interrupted"}
# Unknown mutations remain source-locked: a new UUID must not disguise a retry.
def failure_status(op):
    create = op.get("intents", {}).get("create")
    if create is None or create.get("result") == "rejected":
        return "failed"  # No successor can have received the startup prompt.
    return "delivery_unknown" if op.get("target_header") else "creation_unknown"


def releases_source(op):
    return op["status"] in {"ready", "conflict"} or (
        op["status"] == "failed" and failure_status(op) == "failed")


class HandoffError(Exception):
    def __init__(self, code: str, message: str, status: int = 409):
        super().__init__(code)
        self.code, self.message, self.status = code, message, status


TARGET_KEYS = ("model_provider", "model", "reasoning_effort")
CHECK_SEVERITY = {"not_contradicted": 0, "effort_differs": 1, "model_differs": 2}


def requested_target(value):
    """Strict explicit-choice shape; absence is handled by the caller, never here."""
    if (not isinstance(value, dict) or set(value) != set(TARGET_KEYS)
            or any(not isinstance(v, str) or not v or v != v.strip() or len(v) > 256
                   or any(unicodedata.category(ch) == "Cc" for ch in v) for v in value.values())
            or value["reasoning_effort"] != value["reasoning_effort"].lower()):
        raise HandoffError("invalid_input", "目标模型需要完整、准确的提供商、模型和小写推理级别", 400)
    return {key: value[key] for key in TARGET_KEYS}


def validate_target(catalog, target):
    """Validate only the existing native catalog; never discover or infer models."""
    unverifiable = HandoffError("target_unverifiable", "原生模型列表暂不能验证此选择，请重新打开选择器", 422)
    invalid = HandoffError("target_invalid", "目标提供商、模型或推理级别不在原生可选范围内", 422)
    if (not isinstance(catalog, dict)
            or isinstance(catalog.get("warnings"), dict) and "pi" in catalog["warnings"]):
        raise unverifiable
    backends = catalog.get("backends")
    pi = backends.get("pi") if isinstance(backends, dict) else None
    if not isinstance(pi, dict):
        raise unverifiable
    def choices(values):
        return [v.strip() for v in values if isinstance(v, str)] if isinstance(values, list) else []
    provider, model, effort = (target[key] for key in TARGET_KEYS)
    models = pi.get("provider_models")
    if (provider not in choices(pi.get("provider_choices")) or model.lower() == "default"
            or not isinstance(models, dict) or model not in choices(models.get(provider))):
        raise invalid
    by_model = pi.get("reasoning_efforts_by_model")
    caps = by_model.get(provider + "/" + model) if isinstance(by_model, dict) else None
    if not isinstance(caps, list):
        caps = pi.get("reasoning_efforts")
    if not isinstance(caps, list):
        raise unverifiable
    if effort not in [v.lower() for v in choices(caps)]:
        raise invalid


def validate_frozen_target(op):
    """Old operations omit both fields; malformed new state is never inheritance."""
    if not any(key in op for key in ("requested_target", "target_settings", "target_check")):
        return
    try:
        requested = op["requested_target"]
        if requested is not None:
            requested_target(requested)
        settings = op["target_settings"]
        if (not isinstance(settings, dict) or set(settings) != {*TARGET_KEYS, "origin"}
                or any(not isinstance(settings[k], str) or not settings[k] for k in TARGET_KEYS)
                or settings["origin"] != ("inherited" if requested is None else "explicit")
                or requested is not None and any(settings[k] != requested[k] for k in TARGET_KEYS)):
            raise ValueError("invalid frozen target")
        if "target_check" in op:
            check = op["target_check"]
            if (not isinstance(check, dict) or set(check) != {"result", "stage", "observed"}
                    or check["result"] not in CHECK_SEVERITY
                    or check["stage"] not in ("launch", "named", "continuation")
                    or not isinstance(check["observed"], dict) or set(check["observed"]) != set(TARGET_KEYS)
                    or any(not isinstance(v, str) or not v for v in check["observed"].values())):
                raise ValueError("invalid target check")
    except (KeyError, ValueError, TypeError, HandoffError):
        raise HandoffError("unsafe_state", "接力目标设置损坏，不能自动恢复", 500) from None


def handoff_names(row):
    """Freeze native display state, never use a display label as identity."""
    source = row.get("display_name")
    if not isinstance(source, str):
        alias, cwd = row.get("alias"), row.get("cwd")
        source = alias.strip() if isinstance(alias, str) and alias.strip() else (
            PurePosixPath(cwd.rstrip("/")).name if isinstance(cwd, str) and cwd.strip() else "")
    if not source:
        raise HandoffError("invalid_name", "原会话没有可用名称，请先设置名称再接力", 422)
    suffix = re.search(r"_([0-9]{2})\Z", source)
    number = int(suffix[1]) if suffix else 0
    if number == 99:
        raise HandoffError("sequence_limit", "已达到接力序号 99 上限，不能创建新会话", 409)
    target = (source[:suffix.start()] if suffix else source) + f"_{number + 1:02d}"
    # Native clean_alias folds whitespace/truncates at 80; tmux mirror rejects
    # control characters/leading dash. Refuse rather than silently change names.
    if (len(target) > 80 or " ".join(target.split()).strip() != target
            or target.startswith("-") or any(ord(ch) < 32 or ord(ch) == 127 for ch in target)):
        raise HandoffError("invalid_name", "新名称无法原样保存或映射到窗口；请先缩短或调整原会话名称（最多 80 字符、单空格、无控制字符且不以 - 开头）", 422)
    return source, target


def uuid_text(value):
    if not isinstance(value, str) or len(value) != 36:
        raise HandoffError("invalid_input", "需要有效的 UUID", 400)
    try:
        parsed = str(UUID(value))
    except ValueError:
        raise HandoffError("invalid_input", "需要有效的 UUID", 400) from None
    if parsed != value.lower():
        raise HandoffError("invalid_input", "需要有效的 UUID", 400)
    return parsed


def private_dir(path: Path):
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise HandoffError("unsafe_state", "接力私有目录不可用", 500)
    path.chmod(0o700)


def atomic_state(path: Path, data: dict):
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode()
    if len(payload) > 128 * 1024:
        raise HandoffError("state_too_large", "接力状态超过限制", 500)
    fd, name = tempfile.mkstemp(prefix=".state-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def read_private_json(path: Path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise HandoffError("unsafe_state", "接力私有状态不可用", 500)
        data = stream.read(128 * 1024 + 1)
        if len(data) > 128 * 1024:
            raise HandoffError("unsafe_state", "接力私有状态不可用", 500)
        return json.loads(data)


def header_identity(row: dict):
    """Only the authenticated row's bound file is evidence, never alias/cwd/newest."""
    path = row.get("log_path")
    if not isinstance(path, str) or not Path(path).is_absolute():
        raise HandoffError("identity_missing", "会话的真实 Pi 日志身份尚未就绪")
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode):
                raise ValueError("not regular")
            line = stream.readline(64 * 1024 + 1)
        if len(line) > 64 * 1024:
            raise ValueError("oversized header")
        if not line.endswith(b"\n"):
            raise HandoffError("identity_missing", "会话的真实 Pi 日志头尚未完整写入")
        header = json.loads(line)
        ident = header.get("id")
        if header.get("type") != "session" or not isinstance(ident, str) or not ident:
            raise ValueError("not Pi")
    except FileNotFoundError:
        raise HandoffError("identity_missing", "会话的真实 Pi 日志尚未生成") from None
    except (OSError, ValueError, TypeError, AttributeError):
        raise HandoffError("identity_changed", "会话绑定与真实 Pi 日志身份不一致") from None
    return {"pi_session_id": ident, "log_path": path, "device": info.st_dev, "inode": info.st_ino}


def binding(row):
    return {key: row.get(key) for key in ("session_id", "broker_pid", "launch_id", "spawn_nonce")}


def idle(row):
    # Missing busy is not positive evidence of idle; queued/autonomous work is not idle.
    return (row.get("busy") is False and not any(row.get(key) for key in
            ("queue_len", "broker_queue_len", "commit_unknown_send", "pending_attachment",
             "unattended_enabled", "subagents_running", "lost")))


class Engine:
    def __init__(self, upstream_json, *, state_root=None, timeouts=None,
                 clock=time.monotonic, sleep=asyncio.sleep):
        self.upstream_json = upstream_json
        self.root = Path(state_root or (Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state"))
                                       / "codoxear/session-handoff")).absolute()
        self.timeouts = {"native": 30.0, "launch": 120.0, "understanding": 900.0,
                         "source_idle": 300.0, "review": 600.0, "resolution": 900.0, "poll": 0.5}
        self.timeouts.update(timeouts or {})
        if any(not isinstance(v, (int, float)) or not math.isfinite(v) or v <= 0 for v in self.timeouts.values()):
            raise ValueError("deadlines must be finite and positive")
        self.clock, self.sleep = clock, sleep
        self.operations, self.requests, self.tasks, self.manifests = {}, {}, {}, {}
        self.closed = False

    def open(self):
        private_dir(self.root)
        for directory in self.root.iterdir():
            if directory.is_symlink() or not directory.is_dir():
                continue
            try:
                uuid_text(directory.name)
            except HandoffError:
                continue
            private_dir(directory)
            state_path = directory / "state.json"
            if not state_path.exists():
                continue
            op = read_private_json(state_path)
            if op.get("id") != directory.name or op.get("status") not in MESSAGES:
                raise HandoffError("unsafe_state", "接力状态损坏，不能自动恢复", 500)
            validate_frozen_target(op)
            self.operations[op["id"]] = op
            for request_id in op["request_ids"]:
                self.requests[request_id] = op["id"]
            if op["status"] == "failed":
                # Upgrade old persisted failures whose accepted effects never ended.
                self._set(op, "failed", error_code=op.get("error_code", "workflow_error"))
            elif op["status"] not in FINISHED:
                self._set(op, "interrupted", error_code="restart")
        return self

    def _persist(self, op):
        atomic_state(self.root / op["id"] / "state.json", op)

    def _set(self, op, status, **fields):
        if status == "failed":
            status = failure_status(op)
        if "error_code" not in fields:
            op.pop("error_code", None)
        op.update(fields, status=status, message=MESSAGES[status], updated_at=time.time(),
                  can_continue_unreviewed=status == "unreviewed")
        self._persist(op)

    def public(self, operation_id):
        op = self.operations.get(uuid_text(operation_id))
        if op is None:
            raise HandoffError("not_found", "未找到接力操作", 404)
        return deepcopy({key: op[key] for key in PUBLIC if key in op})

    def status(self, operation_id, *, rows):
        """Reconcile a late receipt using fresh authenticated rows; never send."""
        self.public(operation_id)  # Validate the identifier and existence first.
        op = self.operations[uuid_text(operation_id)]
        continuation = op.get("intents", {}).get("continuation", {})
        if (op["status"] not in {"delivery_unknown", "interrupted"}
                or continuation.get("result") not in {"accepted", "unknown"}):
            return self.public(op["id"])
        try:
            self._verify(op, rows, "target")
            self._check_chain(op)
            resolution = self._artifact(op, "resolution")
        except (ContextError, HandoffError, OSError) as exc:
            self._set(op, op["status"], error_code=(
                getattr(exc, 'code', 'invalid_artifact')))
        else:
            if resolution is not None:
                op["resolution_sha256"] = resolution["_sha256"]
                self._set(op, "conflict" if resolution["verdict"] == "conflict" else "ready")
        return self.public(op["id"])

    async def sessions(self, cookie):
        return (await self.listing(cookie))[0]

    async def listing(self, cookie):
        try:
            status, data = await asyncio.wait_for(self.upstream_json(
                "GET", "/api/sessions", cookie=cookie, timeout=self.timeouts["native"]), self.timeouts["native"])
        except Exception:
            raise HandoffError("native_unavailable", "原生会话服务暂不可用", 503) from None
        if status in (401, 403):
            raise HandoffError("authentication_required", "请重新登录原生页面", 401)
        if status != 200 or not isinstance(data, dict) or not isinstance(data.get("sessions"), list):
            raise HandoffError("native_unavailable", "无法验证原生会话列表", 503)
        if any(not isinstance(row, dict) for row in data["sessions"]):
            raise HandoffError("native_unavailable", "无法验证原生会话列表", 503)
        defaults = data.get("new_session_defaults")
        return data["sessions"], defaults if isinstance(defaults, dict) else None

    @staticmethod
    def _row(rows, route):
        matches = [r for r in rows if r.get("session_id") == route]
        if len(matches) != 1:
            raise HandoffError("identity_changed", "会话不存在或原生路由绑定已改变")
        return matches[0]

    def start(self, source_session_id, request_id, note, target=None, *, rows, catalog=None, cookie):
        """No await between deduplication, intent storage, and task ownership."""
        if self.closed:
            raise HandoffError("shutting_down", "服务正在关闭", 503)
        request_id = uuid_text(request_id)
        requested = requested_target(target) if target is not None else None
        if request_id in self.requests:
            op = self.operations[self.requests[request_id]]
            if op["source_session_id"] != source_session_id or op.get("requested_target") != requested:
                raise HandoffError("request_conflict", "同一请求标识不能用于不同会话或目标选择")
            return self.public(op["id"])
        for op in self.operations.values():
            if op["source_session_id"] == source_session_id and not releases_source(op):
                if op.get("requested_target") != requested:
                    raise HandoffError("target_conflict", "原会话已有不同目标选择的接力操作，请查看原操作")
                if len(op["request_ids"]) >= 256:
                    raise HandoffError("request_limit", "此操作的重复请求过多，请使用原操作标识")
                op["request_ids"].append(request_id)
                self.requests[request_id] = op["id"]
                self._persist(op)
                return self.public(op["id"])
        row = dict(self._row(rows, source_session_id))
        if row.get("agent_backend") != "pi":
            raise HandoffError("unsupported_backend", "当前仅支持 Pi 会话接力", 422)
        source_name, target_name = handoff_names(row)
        source_header = header_identity(row)
        required = ("cwd", *TARGET_KEYS) if requested is None else ("cwd",)
        if not all(isinstance(row.get(k), str) and row[k] for k in required):
            raise HandoffError("settings_missing", "源会话的目录、模型、提供商或推理设置尚未明确")
        if requested is not None:
            validate_target(catalog, requested)
        settings = {key: (row if requested is None else requested)[key] for key in TARGET_KEYS}
        settings["origin"] = "inherited" if requested is None else "explicit"
        operation_id = str(uuid4())
        directory = self.root / operation_id
        private_dir(directory)
        op = {"id": operation_id, "request_id": request_id, "request_ids": [request_id],
              "source_session_id": source_session_id, "target_session_id": None, "reviewed": False,
              "source_binding": binding(row), "source_header": source_header, "intents": {},
              "source_display_name": source_name, "target_display_name": target_name,
              "requested_target": requested, "target_settings": settings}
        self._set(op, "creating")
        self.operations[operation_id], self.requests[request_id] = op, operation_id
        self._schedule(op, lambda: self._run(op, cookie, row, note))
        return self.public(operation_id)

    def _prepare(self, op, row, note):
        # Run bounded transcript IO/projection outside the shared HTTP event loop.
        snapshot = create_source_snapshot(Path(op["source_header"]["log_path"]))
        if snapshot["session_id"] != op["source_header"]["pi_session_id"] or header_identity(row) != op["source_header"]:
            raise HandoffError("identity_changed", "源会话在截取交接边界时发生变化")
        directory = self.root / op["id"]
        manifest = {"version": 1, "operation_id": op["id"],
                    "source": {"session_id": op["source_session_id"], "pi_session_id": snapshot["session_id"],
                               "cwd": row["cwd"], "snapshot": snapshot},
                    "source_sha256": snapshot["sha256"], "note": note,
                    "paths": {"operation_dir": str(directory), "source_context": str(directory / "source-context.md"),
                              **{kind: str(directory / (kind + ".json")) for kind in ("understanding", "review", "resolution")}}}
        prepare_artifacts(directory, manifest)
        # Keep the bounded published manifest, not transient raw transcript entries.
        return read_private_json(directory / "manifest.json")

    def _schedule(self, op, factory):
        task = asyncio.create_task(self._guard(op, factory), name="handoff-" + op["id"])
        self.tasks[op["id"]] = task
        task.add_done_callback(lambda done: self.tasks.pop(op["id"], None) if self.tasks.get(op["id"]) is done else None)

    async def _guard(self, op, factory):
        try:
            await factory()
        except asyncio.CancelledError:
            if op["status"] not in FINISHED:
                self._set(op, "interrupted", error_code="shutdown")
            raise
        except (ContextError, HandoffError) as exc:
            self._set(op, "failed", error_code=getattr(exc, 'code', 'invalid_artifact'))
        except Exception:
            self._set(op, "failed", error_code="workflow_error")

    async def _mutation(self, op, cookie, stage, path, body):
        if stage in op["intents"]:
            raise HandoffError("duplicate_intent", "该阶段已发起，不会自动重试")
        # Intent is fsynced before the first byte can reach native transport.
        op["intents"][stage] = {"id": str(uuid4()), "result": "unknown"}
        if stage in {"review", "continuation"}:
            body = {**body, "send_id": op["intents"][stage]["id"]}
        self._persist(op)
        try:
            status, data = await asyncio.wait_for(self.upstream_json(
                "POST", path, cookie=cookie, body=body, timeout=self.timeouts["native"]), self.timeouts["native"])
        except Exception:
            status, data = None, None
        # Native create's 400 is a pre-launch rejection. Other errors (including
        # 500 with a launch attempt) do not establish that no process was started.
        if (stage == "create" and status == 400 and isinstance(data, dict)
                and data.get("error") and not data.get("commit_unknown")
                and not any(data.get(k) for k in ("launch_id", "broker_pid", "launch_attempt"))):
            op["intents"][stage]["result"] = "rejected"
            self._set(op, "failed", error_code="native_create_rejected")
            return None
        if status != 200 or not isinstance(data, dict) or data.get("ok") is False or data.get("error") or data.get("commit_unknown"):
            self._set(op, "creation_unknown" if stage == "create" else "delivery_unknown", error_code="native_mutation_unknown")
            return None
        op["intents"][stage]["result"] = "accepted"
        self._persist(op)
        return data

    def _verify(self, op, rows, who):
        row = self._row(rows, op[who + "_session_id"])
        if row.get("agent_backend") != "pi" or binding(row) != op[who + "_binding"]:
            raise HandoffError("identity_changed", "会话路由或启动身份已改变，未继续发送")
        if header_identity(row) != op[who + "_header"]:
            raise HandoffError("identity_changed", "会话真实日志身份已改变，未继续发送")
        return row

    def _observe_target(self, op, row, stage):
        """Diagnostic on an already identity-bound row, never an effective-settings claim."""
        if "target_settings" not in op:
            return  # Legacy operations have no frozen creation tuple to compare.
        observed = {key: row.get(key) for key in TARGET_KEYS}
        if any(not isinstance(v, str) or not v for v in observed.values()):
            return
        settings = op["target_settings"]
        result = ("model_differs" if any(observed[k] != settings[k] for k in TARGET_KEYS[:2])
                  else "effort_differs" if observed["reasoning_effort"] != settings["reasoning_effort"]
                  else "not_contradicted")
        previous = op.get("target_check")
        if previous is not None and CHECK_SEVERITY[previous["result"]] >= CHECK_SEVERITY[result]:
            return
        op["target_check"] = {"result": result, "stage": stage, "observed": observed}
        self._persist(op)

    async def _offloop(self, function, *args):
        """Join owned file/service work even when the HTTP app shuts down."""
        work = asyncio.create_task(asyncio.to_thread(function, *args))
        try:
            return await asyncio.shield(work)
        except asyncio.CancelledError:
            await asyncio.gather(work, return_exceptions=True)
            raise

    async def _poll(self, seconds, check):
        deadline = self.clock() + seconds
        while True:
            remaining = deadline - self.clock()
            if remaining <= 0:
                return None
            try:
                result = await asyncio.wait_for(check(), remaining)
            except asyncio.TimeoutError:
                return None
            if result is not None:
                return result
            await self.sleep(min(self.timeouts["poll"], max(0, deadline - self.clock())))

    async def _bind_target(self, op, cookie, created):
        launch_id, pid = created.get("launch_id"), created.get("broker_pid")
        if not ((isinstance(launch_id, str) and launch_id) or (type(pid) is int and pid > 0)):
            self._set(op, "creation_unknown", error_code="launch_identity_missing")
            return False
        op["launch"] = {k: created[k] for k in ("launch_id", "broker_pid", "spawn_nonce") if created.get(k) is not None}
        self._persist(op)

        async def check():
            rows = await self.sessions(cookie)
            candidates = [r for r in rows if all(r.get(k) == v for k, v in op["launch"].items())]
            if len(candidates) > 1:
                raise HandoffError("identity_ambiguous", "原生启动身份匹配多个会话")
            if not candidates:
                return None
            row = candidates[0]
            if row.get("agent_backend") != "pi" or row.get("session_id") == op["source_session_id"]:
                raise HandoffError("identity_changed", "新会话身份与原会话冲突")
            if op["target_session_id"] is None:
                op["target_session_id"], op["target_binding"] = row["session_id"], binding(row)
                self._persist(op)
            elif binding(row) != op["target_binding"]:
                raise HandoffError("identity_changed", "新会话启动身份在恢复前改变")
            # Initial input already traveled in create.args. Lazy log is only a receipt gate.
            if not row.get("log_path"):
                return None
            try:
                identity = header_identity(row)
            except HandoffError as exc:
                if exc.code == "identity_missing":
                    return None
                raise
            op["target_header"] = identity
            if op["target_header"]["pi_session_id"] == op["source_header"]["pi_session_id"]:
                raise HandoffError("identity_changed", "新会话复用了原 Pi 日志身份")
            self._persist(op)
            self._observe_target(op, row, "launch")
            return row

        result = await self._poll(self.timeouts["launch"], check)
        if result is None:
            self._set(op, "creation_unknown", error_code="launch_timeout")
        return result is not None

    async def _name_target(self, op, cookie):
        # Rename has no atomic expected-header precondition, just like send.
        target = self._verify(op, await self.sessions(cookie), "target")
        expected = op["target_display_name"]
        renamed = await self._mutation(op, cookie, "rename",
            "/api/sessions/" + quote(target["session_id"], safe="") + "/rename", {"name": expected})
        if renamed is None:
            return False
        if renamed.get("alias") != expected:
            raise HandoffError("name_mismatch", "原生服务未原样保存新名称，未继续接力")
        # Listing also triggers native's asynchronous nonce-bound tmux mirror;
        # this proves durable display state, not completion of that mirror.
        observed = self._verify(op, await self.sessions(cookie), "target")
        self._observe_target(op, observed, "named")
        if observed.get("alias") != expected or observed.get("display_name") != expected:
            raise HandoffError("name_mismatch", "无法核实新会话的准确名称，未继续接力")
        op["intents"]["rename"]["verified"] = True
        self._persist(op)
        return True

    def _artifact(self, op, kind):
        result = read_artifact(self.root / op["id"], kind, operation_id=op["id"],
                               source_sha256=op["source_sha256"],
                               understanding_sha256=op.get("understanding_sha256"),
                               review_sha256=op.get("review_sha256"))
        pinned = op.get(kind + "_sha256")
        if result is not None and pinned and result["_sha256"] != pinned:
            raise HandoffError("artifact_changed", "已提交的交接产物被替换")
        return result

    def _check_chain(self, op):
        for kind in ("understanding", "review"):
            if op.get(kind + "_sha256") not in (None, "unreviewed") and self._artifact(op, kind) is None:
                raise HandoffError("artifact_changed", "已提交的交接产物缺失")

    async def _wait_artifact(self, op, cookie, kind, timeout):
        async def check():
            rows = await self.sessions(cookie)
            self._verify(op, rows, "target")
            if kind == "review":
                if not any(row.get("session_id") == op["source_session_id"] for row in rows):
                    return None  # Disappeared reviewer may return before the deadline.
                self._verify(op, rows, "source")
            self._check_chain(op)
            return await self._offloop(self._artifact, op, kind)
        return await self._poll(self.timeouts[timeout], check)

    async def _run(self, op, cookie, row, note):
        preparation = asyncio.create_task(asyncio.to_thread(self._prepare, op, row, note))
        try:
            manifest = await asyncio.shield(preparation)
        except asyncio.CancelledError:
            # A thread cannot be cancelled: join our owned preparation before
            # shutdown returns, so no manifest writes outlive the app lifecycle.
            await asyncio.gather(preparation, return_exceptions=True)
            raise
        self.manifests[op["id"]] = manifest
        op["source_sha256"] = manifest["source_sha256"]
        # Only genuinely old operations lack the durable tuple. Never replay on open().
        settings = op["target_settings"] if "target_settings" in op else row
        launch = {"cwd": row["cwd"], **{key: settings[key] for key in TARGET_KEYS}}
        launch.update(agent_backend="pi", create_in_tmux=True,
                      args=[build_recovery_prompt(manifest, self.root / op["id"])])
        self._set(op, "launching")
        self._verify(op, await self.sessions(cookie), "source")
        created = await self._mutation(op, cookie, "create", "/api/sessions", launch)
        if created is None or not await self._bind_target(op, cookie, created):
            return
        if not await self._name_target(op, cookie):
            return
        self._set(op, "recovering")
        understanding = await self._wait_artifact(op, cookie, "understanding", "understanding")
        if understanding is None:
            self._set(op, "failed", error_code="understanding_timeout")
            return
        op["understanding_sha256"] = understanding["_sha256"]
        self._set(op, "waiting_source")

        async def source_idle():
            rows = await self.sessions(cookie)
            self._verify(op, rows, "target")
            self._check_chain(op)
            if not any(row.get("session_id") == op["source_session_id"] for row in rows):
                return None  # Unavailable is unreviewed; a rebound identity still fails.
            row = self._verify(op, rows, "source")
            return row if idle(row) else None

        row = await self._poll(self.timeouts["source_idle"], source_idle)
        if row is None:
            self._set(op, "unreviewed", error_code="source_idle_timeout")
            return
        self._set(op, "reviewing")
        text = build_review_prompt(self.manifests[op["id"]], self.root / op["id"], op["understanding_sha256"])
        text += "\n[handoff:" + op["id"] + ":review]"
        sent = await self._mutation(op, cookie, "review", "/api/sessions/" + quote(row["session_id"], safe="") + "/send", {"text": text})
        if sent is None:
            return
        review = await self._wait_artifact(op, cookie, "review", "review")
        if review is None:
            self._set(op, "unreviewed", error_code="review_timeout")
            return
        await self._continue(op, cookie, review)

    def continue_unreviewed(self, operation_id, *, rows, cookie):
        op = self.operations.get(uuid_text(operation_id))
        if op is None:
            raise HandoffError("not_found", "未找到接力操作", 404)
        if self.closed:
            raise HandoffError("shutting_down", "服务正在关闭", 503)
        if op["status"] != "unreviewed" or op["id"] in self.tasks:
            raise HandoffError("not_unreviewed", "仅未校对操作可明确选择继续")
        self._verify(op, rows, "target")
        self._check_chain(op)
        # Do not hide a review which arrived just after the deadline.
        review = self._artifact(op, "review") if "review" in op["intents"] else None
        op["explicit_unreviewed"] = review is None
        self._set(op, "revising")
        self._schedule(op, lambda: self._continue(op, cookie, review))
        return self.public(op["id"])

    async def _continue(self, op, cookie, review):
        if review:
            op.update(review_sha256=review["_sha256"], reviewed=True, review_verdict=review["verdict"])
        else:
            if not op.get("explicit_unreviewed"):
                raise HandoffError("bypass_not_authorized", "未经校对继续需要用户明确选择")
            op["review_sha256"] = "unreviewed"
        self._set(op, "revising")
        text = build_continuation_prompt(self.manifests[op["id"]], self.root / op["id"],
                                         op["review_sha256"], unreviewed=review is None)
        text += "\n[handoff:" + op["id"] + ":continuation]"
        # Wait for the successor to finish its recovery turn, not a fixed settle sleep.
        async def target_idle():
            rows = await self.sessions(cookie)
            target = self._verify(op, rows, "target")
            self._observe_target(op, target, "continuation")
            self._check_chain(op)
            return target if idle(target) else None
        target = await self._poll(self.timeouts["source_idle"], target_idle)
        if target is None:
            self._set(op, "failed", error_code="target_idle_timeout")
            return
        sent = await self._mutation(op, cookie, "continuation", "/api/sessions/" + quote(target["session_id"], safe="") + "/send", {"text": text})
        if sent is None:
            return
        resolution = await self._wait_artifact(op, cookie, "resolution", "resolution")
        if resolution is None:
            self._set(op, "failed", error_code="resolution_timeout")
            return
        op["resolution_sha256"] = resolution["_sha256"]
        self._set(op, "conflict" if resolution["verdict"] == "conflict" else "ready")

    async def close(self):
        self.closed = True
        tasks = list(self.tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        for op in self.operations.values():
            if op["status"] not in FINISHED:
                self._set(op, "interrupted", error_code="shutdown")
        self.tasks.clear()
        self.manifests.clear()

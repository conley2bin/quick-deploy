"""One source message, one handover file, one native successor; never replay work."""
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

from .prompts import build_handover_prompt, TARGET_PROMPT

PUBLIC = ("id", "request_id", "source_session_id", "target_session_id", "status",
          "message", "updated_at", "error_code", "target_settings")
MESSAGES = {"waiting_source": "等待源会话写交接", "creating": "创建新会话",
            "completed": "完成", "failed": "失败"}
FINISHED = {"completed", "failed"}


class HandoffError(Exception):
    def __init__(self, code: str, message: str, status: int = 409):
        super().__init__(code)
        self.code, self.message, self.status = code, message, status


TARGET_KEYS = ("model_provider", "model", "reasoning_effort")


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
            or target.startswith("-") or any(unicodedata.category(ch) == "Cc" for ch in target)):
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


def binding(row):
    return {key: row.get(key) for key in ("session_id", "broker_pid", "launch_id", "spawn_nonce")}


def idle(row):
    # Missing busy is not positive evidence of idle; queued/autonomous work is not idle.
    return (row.get("busy") is False and not any(row.get(key) for key in
            ("queue_len", "broker_queue_len", "commit_unknown_send", "pending_attachment",
             "unattended_enabled", "subagents_running", "lost")))


class Engine:
    def __init__(self, upstream_json, *, state_root=None, timeouts=None,
                 clock=time.monotonic, sleep=asyncio.sleep, wall_ns=time.time_ns):
        self.upstream_json = upstream_json
        self.root = Path(state_root or (Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state"))
                                       / "codoxear/session-handoff")).absolute()
        self.timeouts = {"native": 30.0, "launch": 120.0, "handover": 1200.0, "poll": 0.5}
        self.timeouts.update(timeouts or {})
        if any(not isinstance(v, (int, float)) or not math.isfinite(v) or v <= 0 for v in self.timeouts.values()):
            raise ValueError("deadlines must be finite and positive")
        self.clock, self.sleep, self.wall_ns = clock, sleep, wall_ns
        self.operations, self.requests, self.tasks = {}, {}, {}
        self.closed = False

    def open(self):
        private_dir(self.root)
        # Old protocol directories deliberately have no role in this workflow.
        for path in self.root.glob("*.json"):
            if path.is_symlink():
                continue
            op = read_private_json(path)
            if (not isinstance(op, dict) or op.get("format") != "handover-1"):
                continue
            if path.stem != uuid_text(op.get("id")) or op.get("status") not in MESSAGES:
                raise HandoffError("invalid_state", "接力状态无法读取", 500)
            self.operations[op["id"]] = op
            for request in op["request_ids"]:
                self.requests[uuid_text(request)] = op["id"]
            if op["status"] not in FINISHED:
                self._set(op, "failed", message="服务重启，未完成", error_code="restart")
        return self

    def _persist(self, op):
        atomic_state(self.root / (op["id"] + ".json"), op)

    def _set(self, op, status, *, message=None, **fields):
        op.update(fields, status=status, message=message or MESSAGES[status], updated_at=time.time())
        self._persist(op)

    def public(self, operation_id):
        op = self.operations.get(uuid_text(operation_id))
        if op is None:
            raise HandoffError("not_found", "未找到接力操作", 404)
        return deepcopy({key: op[key] for key in PUBLIC if key in op})

    def status(self, operation_id):
        return self.public(operation_id)

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
        if (status != 200 or not isinstance(data, dict) or not isinstance(data.get("sessions"), list)
                or any(not isinstance(row, dict) for row in data["sessions"])):
            raise HandoffError("native_unavailable", "无法验证原生会话列表", 503)
        return data["sessions"], data.get("new_session_defaults")

    @staticmethod
    def _row(rows, route):
        matches = [r for r in rows if r.get("session_id") == route]
        if len(matches) != 1:
            raise HandoffError("identity_changed", "会话不存在或原生路由绑定已改变")
        return matches[0]

    def _verify(self, op, rows, who):
        row = self._row(rows, op[who + "_session_id"])
        if row.get("agent_backend") != "pi" or binding(row) != op[who + "_binding"]:
            raise HandoffError("identity_changed", "会话启动身份已改变，接力未完成")
        if who == "source" and row.get("cwd") != op["cwd"]:
            raise HandoffError("identity_changed", "源会话目录已改变，接力未完成")
        return row

    def start(self, source_session_id, request_id, target=None, *, rows, catalog=None, cookie):
        """Acceptance and source exclusion are synchronous on the app event loop."""
        if self.closed:
            raise HandoffError("shutting_down", "服务正在关闭", 503)
        request_id = uuid_text(request_id)
        requested = requested_target(target) if target is not None else None
        if request_id in self.requests:
            op = self.operations[self.requests[request_id]]
            if op["source_session_id"] != source_session_id or op["requested_target"] != requested:
                raise HandoffError("request_conflict", "同一请求标识不能用于不同会话或目标选择")
            return self.public(op["id"])
        for op in self.operations.values():
            if op["source_session_id"] == source_session_id and op["status"] not in FINISHED:
                if op["requested_target"] != requested:
                    raise HandoffError("target_conflict", "源会话已有不同目标选择的接力操作")
                if len(op["request_ids"]) >= 256:
                    raise HandoffError("request_limit", "此操作的重复请求过多，请使用原操作标识")
                op["request_ids"].append(request_id)
                self._persist(op)
                self.requests[request_id] = op["id"]
                return self.public(op["id"])
        row = dict(self._row(rows, source_session_id))
        if row.get("agent_backend") != "pi":
            raise HandoffError("unsupported_backend", "当前仅支持 Pi 会话接力", 422)
        if not idle(row):
            raise HandoffError("source_busy", "源会话忙，稍后再试")
        source_name, target_name = handoff_names(row)
        required = ("cwd", *TARGET_KEYS) if requested is None else ("cwd",)
        if not all(isinstance(row.get(k), str) and row[k] for k in required) or not Path(row["cwd"]).is_absolute():
            raise HandoffError("settings_missing", "源会话的目录或模型设置尚未明确")
        if requested is not None:
            validate_target(catalog, requested)
        settings = {key: (row if requested is None else requested)[key] for key in TARGET_KEYS}
        settings["origin"] = "inherited" if requested is None else "explicit"
        op = {"format": "handover-1", "id": str(uuid4()), "request_id": request_id, "request_ids": [request_id],
              "source_session_id": source_session_id, "source_binding": binding(row), "target_session_id": None,
              "cwd": row["cwd"], "requested_at_ns": self.wall_ns(),
              "source_display_name": source_name, "target_display_name": target_name,
              "requested_target": requested, "target_settings": settings}
        self._set(op, "waiting_source")
        self.operations[op["id"]], self.requests[request_id] = op, op["id"]
        task = asyncio.create_task(self._run(op, cookie, self.clock() + self.timeouts["handover"]),
                                   name="handover-" + op["id"])
        self.tasks[op["id"]] = task
        task.add_done_callback(lambda done: self.tasks.pop(op["id"], None))
        return self.public(op["id"])

    async def _post(self, cookie, path, body, stage):
        try:
            status, data = await asyncio.wait_for(self.upstream_json(
                "POST", path, cookie=cookie, body=body, timeout=self.timeouts["native"]), self.timeouts["native"])
        except Exception:
            raise HandoffError("native_request_failed", stage + "响应未收到；请人工核对，不会自动重试") from None
        if (status != 200 or not isinstance(data, dict) or data.get("ok") is False
                or data.get("error") or data.get("commit_unknown")):
            raise HandoffError("native_request_failed", stage + "未确认成功；请人工核对，不会自动重试")
        return data

    async def _poll(self, deadline, check):
        while self.clock() < deadline:
            try:
                result = await asyncio.wait_for(check(), deadline - self.clock())
            except asyncio.TimeoutError:
                return None
            if result is not None:
                return result
            await self.sleep(min(self.timeouts["poll"], max(0, deadline - self.clock())))
        return None

    async def _bind_target(self, op, cookie, created):
        launch = {k: created[k] for k in ("launch_id", "broker_pid", "spawn_nonce") if created.get(k) is not None}
        if not (isinstance(launch.get("launch_id"), str) and launch["launch_id"]
                or type(launch.get("broker_pid")) is int and launch["broker_pid"] > 0):
            raise HandoffError("launch_identity_missing", "新会话启动标识缺失，请人工核对")

        async def check():
            rows = await self.sessions(cookie)
            candidates = [r for r in rows if all(r.get(k) == v for k, v in launch.items())]
            if len(candidates) > 1:
                raise HandoffError("identity_ambiguous", "新会话启动标识匹配多个会话")
            if not candidates:
                return None
            row = candidates[0]
            if row.get("agent_backend") != "pi" or row.get("session_id") == op["source_session_id"]:
                raise HandoffError("identity_changed", "新会话身份与源会话冲突")
            return row

        row = await self._poll(self.clock() + self.timeouts["launch"], check)
        if row is None:
            raise HandoffError("launch_timeout", "等待新会话出现超时，请人工核对")
        op.update(target_session_id=row["session_id"], target_binding=binding(row))
        self._persist(op)

    async def _run(self, op, cookie, deadline):
        try:
            source = self._verify(op, await self.sessions(cookie), "source")
            if not idle(source):
                raise HandoffError("source_busy", "源会话忙，稍后再试")
            handover = Path(op["cwd"]) / ".memory/handover.md"
            await self._post(cookie, "/api/sessions/" + quote(op["source_session_id"], safe="") + "/send",
                             {"text": build_handover_prompt(op["cwd"]), "send_id": op["id"]}, "发送交接请求")

            async def written_and_idle():
                row = self._verify(op, await self.sessions(cookie), "source")
                try:
                    info = handover.stat()
                except FileNotFoundError:
                    return None
                return True if (stat.S_ISREG(info.st_mode) and info.st_mtime_ns > op["requested_at_ns"] and idle(row)) else None

            if await self._poll(deadline, written_and_idle) is None:
                raise HandoffError("handover_timeout", "等待源会话写交接并空闲超时（20 分钟），未创建新会话")
            self._set(op, "creating")
            launch = {"cwd": op["cwd"], **{k: op["target_settings"][k] for k in TARGET_KEYS},
                      "agent_backend": "pi", "create_in_tmux": True, "args": [TARGET_PROMPT]}
            created = await self._post(cookie, "/api/sessions", launch, "创建新会话")
            await self._bind_target(op, cookie, created)
            target = self._verify(op, await self.sessions(cookie), "target")
            path = "/api/sessions/" + quote(target["session_id"], safe="") + "/rename"
            expected = op["target_display_name"]
            renamed = await self._post(cookie, path, {"name": expected}, "命名新会话")
            observed = self._verify(op, await self.sessions(cookie), "target")
            if renamed.get("alias") != expected or any(observed.get(k) != expected for k in ("alias", "display_name")):
                raise HandoffError("name_mismatch", "无法核实新会话名称，请人工核对")
            self._set(op, "completed")
        except asyncio.CancelledError:
            self._set(op, "failed", message="服务重启，未完成", error_code="restart")
            raise
        except HandoffError as exc:
            self._set(op, "failed", message=exc.message, error_code=exc.code)
        except Exception:
            self._set(op, "failed", message="接力未完成，请检查目录和原生会话服务；不会自动重试", error_code="workflow_error")

    async def close(self):
        self.closed = True
        tasks = list(self.tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        # A task cancelled before its coroutine starts cannot run its handler.
        for op in self.operations.values():
            if op["status"] not in FINISHED:
                self._set(op, "failed", message="服务重启，未完成", error_code="restart")
        self.tasks.clear()

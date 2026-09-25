#!/usr/bin/env python3
"""launch.py 的隔离单元测试：PIOX_CLIPBOARD_BACKEND 只改 pi 子进程环境。

不读取也不打印真实 .env：需要 .env 来源的用例把 launch.ENV_FILE 指到
临时文件；需要执行 main() 的用例替换 runtime_env/exec_program。整个文件
不启动 broker、server，不访问网络、剪贴板或真实用户配置。

运行：
    python3 codoxear/tests/test_launch_clipboard.py
"""
from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

LAUNCH_PATH = Path(__file__).resolve().parent.parent / ".internal" / "launch.py"


def load_launch():
    spec = importlib.util.spec_from_file_location("launch_under_test", LAUNCH_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


launch = load_launch()


def wayland_env(**overrides: str) -> dict[str, str]:
    env = {
        "PATH": "/usr/bin",
        "DISPLAY": ":1",
        "WAYLAND_DISPLAY": "wayland-0",
        "XDG_SESSION_TYPE": "wayland",
        "PIOX_CLIPBOARD_BACKEND": "x11",
    }
    env.update(overrides)
    return env


class ClipboardBackendTransform(unittest.TestCase):
    """纯函数层：命令与环境矩阵，不依赖进程或文件。"""

    def test_pi_x11_hides_wayland_and_keeps_display(self) -> None:
        env = wayland_env()
        child = launch.apply_clipboard_backend("pi", env)
        self.assertEqual(child["DISPLAY"], ":1")
        self.assertEqual(child["XDG_SESSION_TYPE"], "x11")
        self.assertNotIn("WAYLAND_DISPLAY", child)

    def test_pi_x11_does_not_mutate_caller_env(self) -> None:
        env = wayland_env()
        launch.apply_clipboard_backend("pi", env)
        self.assertEqual(env["WAYLAND_DISPLAY"], "wayland-0")
        self.assertEqual(env["XDG_SESSION_TYPE"], "wayland")

    def test_pi_unset_empty_auto_are_noops(self) -> None:
        for value in (None, "", "auto", "  auto  "):
            with self.subTest(value=value):
                env = wayland_env()
                if value is None:
                    env.pop("PIOX_CLIPBOARD_BACKEND")
                else:
                    env["PIOX_CLIPBOARD_BACKEND"] = value
                child = launch.apply_clipboard_backend("pi", env)
                self.assertEqual(child, env)
                self.assertEqual(child["WAYLAND_DISPLAY"], "wayland-0")
                self.assertEqual(child["XDG_SESSION_TYPE"], "wayland")

    def test_pi_invalid_value_fails_closed(self) -> None:
        for value in ("wayland", "wl", "x11,wayland", "never"):
            with self.subTest(value=value):
                env = wayland_env(PIOX_CLIPBOARD_BACKEND=value)
                with self.assertRaises(RuntimeError) as ctx:
                    launch.apply_clipboard_backend("pi", env)
                message = str(ctx.exception)
                self.assertIn("PIOX_CLIPBOARD_BACKEND", message)
                self.assertIn("x11", message)
                # 失败不得修改环境，也不得静默降级
                self.assertEqual(env["WAYLAND_DISPLAY"], "wayland-0")

    def test_pi_x11_value_is_whitespace_trimmed(self) -> None:
        child = launch.apply_clipboard_backend("pi", wayland_env(PIOX_CLIPBOARD_BACKEND="  x11 "))
        self.assertEqual(child["XDG_SESSION_TYPE"], "x11")
        self.assertNotIn("WAYLAND_DISPLAY", child)

    def test_pi_x11_requires_nonempty_display(self) -> None:
        for display in (None, "", "   "):
            with self.subTest(display=display):
                env = wayland_env()
                if display is None:
                    env.pop("DISPLAY")
                else:
                    env["DISPLAY"] = display
                with self.assertRaises(RuntimeError) as ctx:
                    launch.apply_clipboard_backend("pi", env)
                self.assertIn("DISPLAY", str(ctx.exception))
                self.assertEqual(env["WAYLAND_DISPLAY"], "wayland-0")

    def test_non_pi_commands_are_untouched(self) -> None:
        for command in ("codex", "server", "validate", "describe", ""):
            with self.subTest(command=command):
                env = wayland_env()
                self.assertIs(launch.apply_clipboard_backend(command, env), env)
                self.assertEqual(env["WAYLAND_DISPLAY"], "wayland-0")
                self.assertEqual(env["XDG_SESSION_TYPE"], "wayland")

    def test_non_pi_invalid_value_is_ignored(self) -> None:
        env = wayland_env(PIOX_CLIPBOARD_BACKEND="wayland")
        self.assertIs(launch.apply_clipboard_backend("codex", env), env)


class LaunchMainWiring(unittest.TestCase):
    """main() 接线：只对 pi 生效，且在任何进程执行前失败。

    exec_program 真实实现是 os.execve（永不返回）；测试里让替身在记录
    调用后抛哨兵异常，阻止 main() 落下到末尾的 usage 错误。
    """

    class ExecCalled(Exception):
        pass

    def run_main(self, argv: list[str], env: dict[str, str], calls: list) -> None:
        def fake_exec(program: str, args: list[str], child_env: dict[str, str]) -> None:
            calls.append((program, args, dict(child_env)))
            raise self.ExecCalled()

        with mock.patch.object(launch, "runtime_env", return_value=env), \
             mock.patch.object(launch, "validate_env"), \
             mock.patch.object(launch, "exec_program", side_effect=fake_exec), \
             mock.patch.object(os, "chdir"), \
             mock.patch.object(sys, "argv", ["launch.py", *argv]):
            try:
                launch.main()
            except self.ExecCalled:
                pass

    def test_main_pi_applies_transform_before_exec(self) -> None:
        calls: list = []
        self.run_main(["pi", "--foo", "bar"], wayland_env(), calls)
        self.assertEqual(len(calls), 1)
        program, args, child_env = calls[0]
        self.assertEqual(program, "codoxear-broker")
        self.assertEqual(args, ["--", "--foo", "bar"])
        self.assertEqual(child_env["CODEX_WEB_AGENT_BACKEND"], "pi")
        self.assertEqual(child_env["XDG_SESSION_TYPE"], "x11")
        self.assertNotIn("WAYLAND_DISPLAY", child_env)
        self.assertEqual(child_env["DISPLAY"], ":1")

    def test_main_pi_without_option_is_unchanged(self) -> None:
        env = wayland_env()
        env.pop("PIOX_CLIPBOARD_BACKEND")
        calls: list = []
        self.run_main(["pi"], env, calls)
        _, _, child_env = calls[0]
        self.assertEqual(child_env["WAYLAND_DISPLAY"], "wayland-0")
        self.assertEqual(child_env["XDG_SESSION_TYPE"], "wayland")

    def test_main_pi_invalid_value_stops_before_exec(self) -> None:
        env = wayland_env(PIOX_CLIPBOARD_BACKEND="wl")
        calls: list = []
        with self.assertRaises(RuntimeError):
            self.run_main(["pi"], env, calls)
        self.assertEqual(calls, [])

    def test_main_pi_missing_display_stops_before_exec(self) -> None:
        env = wayland_env()
        env.pop("DISPLAY")
        calls: list = []
        with self.assertRaises(RuntimeError):
            self.run_main(["pi"], env, calls)
        self.assertEqual(calls, [])

    def test_main_codex_keeps_wayland_environment(self) -> None:
        calls: list = []
        self.run_main(["codex", "exec"], wayland_env(), calls)
        program, args, child_env = calls[0]
        self.assertEqual(program, "codoxear-broker")
        self.assertEqual(args, ["--", "exec"])
        self.assertEqual(child_env["WAYLAND_DISPLAY"], "wayland-0")
        self.assertEqual(child_env["XDG_SESSION_TYPE"], "wayland")
        self.assertNotIn("CODEX_WEB_AGENT_BACKEND", child_env)

    def test_main_server_ignores_backend_choice(self) -> None:
        # 即使配置指向 x11 且没有 DISPLAY，server 也照常启动（不受影响）。
        env = wayland_env(PIOX_CLIPBOARD_BACKEND="x11")
        env.pop("DISPLAY")
        calls: list = []
        self.run_main(["server"], env, calls)
        program, _, child_env = calls[0]
        self.assertEqual(program, "codoxear-server")
        self.assertEqual(child_env["WAYLAND_DISPLAY"], "wayland-0")
        self.assertEqual(child_env["XDG_SESSION_TYPE"], "wayland")


class RuntimeEnvSources(unittest.TestCase):
    """runtime_env 的来源优先级：临时 .env 提供值，当前 shell 优先。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="launch-clipboard-test.")
        self.addCleanup(self._tmp.cleanup)
        self.env_file = Path(self._tmp.name) / ".env"
        self.env_file.write_text(
            "\n".join(
                [
                    "# 测试用临时 .env，不含真实凭据",
                    "CODEX_WEB_PASSWORD=change-me",
                    "PIOX_CLIPBOARD_BACKEND=x11",
                    "",
                ]
            ),
            encoding="utf-8",
        )

    def test_env_file_value_reaches_pi_child(self) -> None:
        base = {
            "PATH": "/usr/bin",
            "DISPLAY": ":1",
            "WAYLAND_DISPLAY": "wayland-0",
            "XDG_SESSION_TYPE": "wayland",
        }
        with mock.patch.object(launch, "ENV_FILE", self.env_file), \
             mock.patch.dict(os.environ, base, clear=True):
            env = launch.runtime_env()
            self.assertEqual(env["PIOX_CLIPBOARD_BACKEND"], "x11")
            child = launch.apply_clipboard_backend("pi", env)
        self.assertEqual(child["XDG_SESSION_TYPE"], "x11")
        self.assertNotIn("WAYLAND_DISPLAY", child)
        self.assertEqual(child["DISPLAY"], ":1")

    def test_shell_value_wins_over_env_file(self) -> None:
        base = {
            "PATH": "/usr/bin",
            "DISPLAY": ":1",
            "WAYLAND_DISPLAY": "wayland-0",
            "XDG_SESSION_TYPE": "wayland",
            "PIOX_CLIPBOARD_BACKEND": "auto",
        }
        with mock.patch.object(launch, "ENV_FILE", self.env_file), \
             mock.patch.dict(os.environ, base, clear=True):
            env = launch.runtime_env()
            self.assertEqual(env["PIOX_CLIPBOARD_BACKEND"], "auto")
            child = launch.apply_clipboard_backend("pi", env)
        self.assertEqual(child["WAYLAND_DISPLAY"], "wayland-0")


if __name__ == "__main__":
    unittest.main(verbosity=2)

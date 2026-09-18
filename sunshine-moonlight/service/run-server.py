#!/usr/bin/python3
"""Strict local connector for the Sunshine/Moonlight inventory."""
from __future__ import annotations

import argparse
import ipaddress
import os
from pathlib import Path
import re
import secrets
import shlex
import subprocess
import sys
import threading
from typing import Any

MODULE_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INVENTORY = MODULE_ROOT / "machines.yaml"
DEFAULT_MOONLIGHT_PORT = 47989
MIN_MOONLIGHT_PORT = 1029
MAX_MOONLIGHT_PORT = 65514
DESTINATION_RE = re.compile(r"(?:[A-Za-z0-9][A-Za-z0-9_.-]*@)?[A-Za-z0-9][A-Za-z0-9_.-]*$")
MACHINE_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*$")
QUERY_OUTPUT_LIMIT = 4096
# The unpaired sentence is the only machine-readable pair-state signal `moonlight
# list` exposes. These are the exact upstream strings at Moonlight Qt v6.1.0
# (commit f786e94c7b2f943e24e65d7d74deb539b827fc84): app/cli/listapps.cpp and the
# zh_CN/zh_TW translations in app/languages/qml_zh_CN.ts and qml_zh_TW.ts.
UNPAIRED_DIAGNOSTICS = (
    "has not been paired. Please open Moonlight to pair before retrieving games list.",
    "未配对，请在请求游戏列表前使用 Moonlight 配对",
    "未配對，請在擷取遊戲清單前開啟 Moonlight 進行配對",
)


class InventoryError(Exception):
    """A local inventory is absent or violates the closed schema."""


def missing_inventory_message(path: Path) -> str:
    """The installer refreshes only the module-root inventory; any other path is
    read exactly as given, so an absent custom path gets path-shaped advice."""
    if path != DEFAULT_INVENTORY:
        return (
            f"清单不存在: {path}\n"
            f"连接器只读取指定的这一个文件，不会自动探测、生成或改用其它清单。\n"
            f"请核对该路径，或省略 --config 使用模块根目录的默认清单：{DEFAULT_INVENTORY}"
        )
    return (
        f"清单不存在: {path}\n"
        f"它由一次成功的模块根安装器生成/刷新，内容是不含真实地址的通用占位清单，也不会读取旧文件兜底。\n"
        f"请先按实际用途在模块目录运行一次安装器（所选安装阶段全部成功后才会刷新该清单）：\n"
        f"    cd -- {shlex.quote(str(MODULE_ROOT))} && ./install.sh\n"
        f"成功后直接编辑 {shlex.quote(str(path))}，按其中文注释填入你的真实机器信息。"
    )


def parser() -> argparse.ArgumentParser:
    argument_parser = argparse.ArgumentParser(
        description=(
            "Launch local Moonlight Desktop streaming or an interactive SSH login. "
            "Before streaming, the connector queries `moonlight list`, and when the host "
            "is clearly unpaired it prints a locally generated 4-digit PIN plus the "
            "target Sunshine Web UI steps, pairs, re-confirms, and only then streams."
        )
    )
    argument_parser.add_argument("--config", metavar="PATH", type=Path)
    mode = argument_parser.add_mutually_exclusive_group()
    mode.add_argument("--moonlight", action="store_true")
    mode.add_argument("--ssh", action="store_true")
    argument_parser.add_argument("--list", action="store_true", dest="list_machines")
    argument_parser.add_argument("name", nargs="?")
    return argument_parser


def load_yaml(path: Path) -> Any:
    try:
        import yaml
    except ModuleNotFoundError as error:
        raise InventoryError(
            "系统 Python 缺少 PyYAML；请先运行 ./install.sh 安装 python3-yaml"
        ) from error

    class UniqueKeyLoader(yaml.SafeLoader):
        pass

    def construct_mapping(loader: Any, node: Any, deep: bool = False) -> dict[Any, Any]:
        mapping: dict[Any, Any] = {}
        for key_node, value_node in node.value:
            key = loader.construct_object(key_node, deep=deep)
            try:
                duplicate = key in mapping
            except TypeError as error:
                raise yaml.constructor.ConstructorError(
                    "while constructing a mapping", node.start_mark,
                    "mapping keys must be scalar", key_node.start_mark,
                ) from error
            if duplicate:
                raise yaml.constructor.ConstructorError(
                    "while constructing a mapping", node.start_mark,
                    f"duplicate key: {key!r}", key_node.start_mark,
                )
            mapping[key] = loader.construct_object(value_node, deep=deep)
        return mapping

    UniqueKeyLoader.add_constructor(
        yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, construct_mapping
    )
    try:
        with path.open(encoding="utf-8") as inventory_file:
            return yaml.load(inventory_file, Loader=UniqueKeyLoader)
    except FileNotFoundError as error:
        raise InventoryError(missing_inventory_message(path)) from error
    except UnicodeDecodeError as error:
        raise InventoryError(f"清单不是有效 UTF-8 文本: {path}") from error
    except OSError as error:
        raise InventoryError(f"无法读取清单 {path}: {error}") from error
    except yaml.YAMLError as error:
        raise InventoryError(f"清单 YAML 无效: {error}") from error


def require_string(value: Any, field: str, machine: str) -> str:
    if type(value) is not str or not value:
        raise InventoryError(f"机器 {machine} 的 {field} 必须是非空字符串")
    return value


def require_port(value: Any, field: str, machine: str, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise InventoryError(f"机器 {machine} 的 {field} 必须是 {minimum}–{maximum} 的整数")
    return value


def validate_inventory(data: Any) -> dict[str, dict[str, Any]]:
    if type(data) is not dict or set(data) != {"machines"}:
        raise InventoryError("清单顶层只能包含 machines 映射")
    machines = data["machines"]
    if type(machines) is not dict or not machines:
        raise InventoryError("machines 必须是非空映射")

    validated: dict[str, dict[str, Any]] = {}
    allowed_fields = {"ssh", "tailnet_ip", "moonlight_port", "ssh_port", "note"}
    for name, entry in machines.items():
        if type(name) is not str or not MACHINE_NAME_RE.fullmatch(name):
            raise InventoryError("机器名只能使用字母、数字、点、下划线或连字符，且不能以符号开头")
        if type(entry) is not dict:
            raise InventoryError(f"机器 {name} 必须是字段映射")
        unknown = set(entry) - allowed_fields
        if unknown:
            raise InventoryError(f"机器 {name} 包含不支持字段: {', '.join(sorted(map(str, unknown)))}")
        missing = {"ssh", "tailnet_ip"} - set(entry)
        if missing:
            raise InventoryError(f"机器 {name} 缺少字段: {', '.join(sorted(missing))}")

        ssh = require_string(entry["ssh"], "ssh", name)
        if not DESTINATION_RE.fullmatch(ssh):
            raise InventoryError(f"机器 {name} 的 ssh 必须是单个别名或 [user@]hostname/IP，不能含选项或命令")
        tailnet_ip = require_string(entry["tailnet_ip"], "tailnet_ip", name)
        try:
            ipaddress.IPv4Address(tailnet_ip)
        except ipaddress.AddressValueError as error:
            raise InventoryError(f"机器 {name} 的 tailnet_ip 必须是 IPv4 字面量") from error
        if "note" in entry and type(entry["note"]) is not str:
            raise InventoryError(f"机器 {name} 的 note 必须是字符串")

        moonlight_port = DEFAULT_MOONLIGHT_PORT
        if "moonlight_port" in entry:
            moonlight_port = require_port(
                entry["moonlight_port"], "moonlight_port", name,
                MIN_MOONLIGHT_PORT, MAX_MOONLIGHT_PORT,
            )
        ssh_port = None
        if "ssh_port" in entry:
            ssh_port = require_port(entry["ssh_port"], "ssh_port", name, 1, 65535)
        validated[name] = {
            "ssh": ssh,
            "tailnet_ip": tailnet_ip,
            "moonlight_port": moonlight_port,
            "ssh_port": ssh_port,
        }
    return validated


def report_program_error(program: str, error: OSError) -> None:
    if isinstance(error, FileNotFoundError):
        print(f"错误: 找不到本地可执行文件: {program}", file=sys.stderr)
    else:
        print(f"错误: 无法执行 {program}: {error}", file=sys.stderr)
    raise SystemExit(127)


def exec_program(argv: list[str]) -> None:
    try:
        if os.path.sep in argv[0]:
            os.execv(argv[0], argv)
        os.execvp(argv[0], argv)
    except OSError as error:
        report_program_error(argv[0], error)
    raise SystemExit(127)


def drain_bounded(stream: Any, sink: bytearray) -> None:
    """Read a pipe to EOF, retaining only its leading QUERY_OUTPUT_LIMIT bytes."""
    while True:
        chunk = stream.read(4096)
        if not chunk:
            return
        if len(sink) < QUERY_OUTPUT_LIMIT:
            sink.extend(chunk[: QUERY_OUTPUT_LIMIT - len(sink)])


def run_query(argv: list[str]) -> tuple[int, str, str]:
    """Run one non-GUI `moonlight list` query and return bounded output.

    Classification needs the canonical diagnostic, while Moonlight translates it
    from the user's locale; pinning LC_ALL for this short-lived child keeps the
    upstream English wording deterministic. The pair action and the final stream
    still inherit the user's untouched environment.
    """
    try:
        process = subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=dict(os.environ, LC_ALL="C"),
        )
    except OSError as error:
        report_program_error(argv[0], error)
    stdout, stderr = bytearray(), bytearray()
    readers = [
        threading.Thread(target=drain_bounded, args=(process.stdout, stdout), daemon=True),
        threading.Thread(target=drain_bounded, args=(process.stderr, stderr), daemon=True),
    ]
    for reader in readers:
        reader.start()
    returncode = process.wait()
    for reader in readers:
        reader.join()
    process.stdout.close()
    process.stderr.close()
    return returncode, stdout.decode("utf-8", "replace"), stderr.decode("utf-8", "replace")


def run_pair(argv: list[str]) -> int:
    """Run the GUI pair action in the foreground with the user's environment."""
    try:
        process = subprocess.Popen(argv)
    except OSError as error:
        report_program_error(argv[0], error)
    return process.wait()


def generate_pin() -> str:
    """A fresh 4-digit PIN from the OS CSPRNG; it is only ever written to the terminal."""
    return f"{secrets.randbelow(10000):04d}"


def classify_list_result(returncode: int, stderr_text: str) -> str:
    """Map one `moonlight list` result to ``paired``/``unpaired``/``unknown``.

    Upstream prints the unpaired sentence and exits 255; a paired host prints its
    app list and exits 0. Every other outcome (connect failure, QPA errors, a
    future or translated diagnostic) is unknown and must never start pairing.
    """
    if returncode != 0:
        for diagnostic in UNPAIRED_DIAGNOSTICS:
            if diagnostic in stderr_text:
                return "unpaired"
    return "paired" if returncode == 0 else "unknown"


def has_desktop_app(stdout_text: str) -> bool:
    """Require the exact Desktop application before sending a launch request."""
    return any(line.strip().casefold() == "desktop" for line in stdout_text.splitlines())


def print_bounded_diagnostic(label: str, diagnostics: str) -> None:
    if diagnostics.strip():
        print(f"{label}:", file=sys.stderr)
        print(diagnostics.rstrip("\n"), file=sys.stderr)


def print_unpaired_guidance(name: str, target: str, web_ui: str, pin: str) -> None:
    print(f"未配对: {name} ({target}) 尚未与本机 Moonlight 配对。", file=sys.stderr)
    print(f"本机 PIN: {pin}", file=sys.stderr)
    print("远端 Sunshine 网页操作（网页由目标主机的 Sunshine 服务内置提供）:", file=sys.stderr)
    print(f"  1. 打开 {web_ui} （自签名证书需在浏览器中手动信任）", file=sys.stderr)
    print("  2. 用 Sunshine 管理员凭据登录后进入 PIN 页面", file=sys.stderr)
    print("  3. 核对待配对客户端名称与来源地址，在对应请求中输入上面的本机 PIN（若列表为空，等几秒刷新页面）", file=sys.stderr)
    print("本机 Moonlight 操作（保持本终端运行）:", file=sys.stderr)
    print(f"  4. 脚本自动运行: ~/.local/bin/moonlight pair --pin {pin} -- {target}", file=sys.stderr)
    print("  5. 配对返回后脚本会重新确认已配对，确认通过才启动 Desktop 串流", file=sys.stderr)


def print_app_missing(name: str, target: str, web_ui: str, apps_output: str) -> None:
    print(f"错误: {name} 已配对，但 Moonlight 的应用列表没有 Desktop；未启动串流。", file=sys.stderr)
    print(f"目标 Sunshine Web UI: {web_ui}", file=sys.stderr)
    print("请登录该页面的 Applications/应用页面，添加或恢复名为 Desktop 的应用；修改 apps.json 后重启 Sunshine，再重新运行本脚本。", file=sys.stderr)
    if apps_output.strip():
        print("当前应用列表:", file=sys.stderr)
        print(apps_output.rstrip("\n"), file=sys.stderr)


def print_list_failure(
    stage: str,
    name: str,
    target: str,
    web_ui: str,
    returncode: int,
    stdout_text: str,
    stderr_text: str,
    outcome: str,
) -> None:
    print(f"错误: {stage}，无法确认 {name} ({target}) 的配对状态；{outcome}", file=sys.stderr)
    print(f"目标 Sunshine Web UI: {web_ui}", file=sys.stderr)
    print(f"moonlight list 退出码: {returncode}", file=sys.stderr)
    print_bounded_diagnostic("moonlight list stdout 诊断输出", stdout_text)
    print_bounded_diagnostic("moonlight list stderr 诊断输出", stderr_text)
    print("请确认目标主机已开机、Sunshine 服务在运行且 Tailnet 可达；修正后重新运行本脚本。", file=sys.stderr)
    print("若只是尚未配对，也可在 Moonlight 图形界面手动配对后再用本脚本串流。", file=sys.stderr)


def print_pair_failure(
    name: str,
    target: str,
    web_ui: str,
    pair_returncode: int,
    returncode: int,
    stdout_text: str,
    stderr_text: str,
) -> None:
    print(f"错误: 配对未完成: {name} ({target}) 仍未配对；未启动串流。", file=sys.stderr)
    print(f"本机 Moonlight 配对进程退出码: {pair_returncode}（该退出码本身不能证明配对成功）", file=sys.stderr)
    print(f"目标 Sunshine Web UI: {web_ui}（可在 PIN 页面核对是否还有待配对请求）", file=sys.stderr)
    print(f"moonlight list 退出码: {returncode}", file=sys.stderr)
    print_bounded_diagnostic("moonlight list stdout 诊断输出", stdout_text)
    print_bounded_diagnostic("moonlight list stderr 诊断输出", stderr_text)
    print("修正后重新运行本脚本：脚本会重新检测；若仍未配对，会重新给出 PIN 与步骤。", file=sys.stderr)


def connect_moonlight(name: str, machine: dict[str, Any], wrapper: Path) -> None:
    """Check pair state, guide and pair a clearly unpaired host, then stream."""
    program = str(wrapper)
    target = f"{machine['tailnet_ip']}:{machine['moonlight_port']}"
    web_ui = f"https://{machine['tailnet_ip']}:{machine['moonlight_port'] + 1}"
    query = [program, "list", "--", target]

    returncode, stdout, stderr = run_query(query)
    state = classify_list_result(returncode, stderr)
    if state == "unknown":
        print_list_failure(
            "配对状态检查失败", name, target, web_ui, returncode, stdout, stderr,
            "未开始配对，也未启动串流。",
        )
        raise SystemExit(1)
    if state == "unpaired":
        pin = generate_pin()
        print_unpaired_guidance(name, target, web_ui, pin)
        print("开始本机 Moonlight 配对（等待远端 Sunshine 网页输入 PIN）…", file=sys.stderr)
        pair_returncode = run_pair([program, "pair", "--pin", pin, "--", target])
        returncode, stdout, stderr = run_query(query)
        state = classify_list_result(returncode, stderr)
        if state == "unknown":
            print_list_failure(
                "配对后确认失败", name, target, web_ui, returncode, stdout, stderr,
                "配对是否生效无法验证，未启动串流。",
            )
            raise SystemExit(1)
        if state == "unpaired":
            print_pair_failure(name, target, web_ui, pair_returncode, returncode, stdout, stderr)
            raise SystemExit(1)
        if not has_desktop_app(stdout):
            print_app_missing(name, target, web_ui, stdout)
            raise SystemExit(1)
        print(f"配对已确认: {name} ({target})；启动 Desktop 串流。", file=sys.stderr)
    elif not has_desktop_app(stdout):
        print_app_missing(name, target, web_ui, stdout)
        raise SystemExit(1)
    exec_program([program, "stream", "--", target, "Desktop"])


def main(argv: list[str]) -> None:
    args = parser().parse_args(argv)
    if args.list_machines:
        if args.name is not None or args.moonlight or args.ssh:
            parser().error("--list 不能与机器名或连接模式同时使用")
    elif args.name is None:
        parser().error("必须指定机器名；使用 --list 查看可用名称")

    config_path = args.config if args.config is not None else DEFAULT_INVENTORY
    try:
        machines = validate_inventory(load_yaml(config_path))
    except InventoryError as error:
        print(f"错误: {error}", file=sys.stderr)
        raise SystemExit(1)

    if args.list_machines:
        for name in sorted(machines):
            print(name)
        return

    machine = machines.get(args.name)
    if machine is None:
        print(f"错误: 清单中没有机器: {args.name}", file=sys.stderr)
        raise SystemExit(1)
    if args.ssh:
        command = ["ssh"]
        if machine["ssh_port"] is not None:
            command.extend(["-p", str(machine["ssh_port"])])
        command.append(machine["ssh"])
        exec_program(command)
    else:
        connect_moonlight(args.name, machine, Path.home() / ".local/bin/moonlight")


if __name__ == "__main__":
    try:
        main(sys.argv[1:])
    except KeyboardInterrupt:
        print("已取消", file=sys.stderr)
        raise SystemExit(130)

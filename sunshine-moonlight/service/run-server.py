#!/usr/bin/python3
"""Strict local connector for the Sunshine/Moonlight inventory."""
from __future__ import annotations

import argparse
import ipaddress
import os
from pathlib import Path
import re
import sys
from typing import Any

DEFAULT_MOONLIGHT_PORT = 47989
MIN_MOONLIGHT_PORT = 1029
MAX_MOONLIGHT_PORT = 65514
DESTINATION_RE = re.compile(r"(?:[A-Za-z0-9][A-Za-z0-9_.-]*@)?[A-Za-z0-9][A-Za-z0-9_.-]*$")
MACHINE_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*$")


class InventoryError(Exception):
    """A local inventory is absent or violates the closed schema."""


def parser() -> argparse.ArgumentParser:
    argument_parser = argparse.ArgumentParser(
        description="Launch local Moonlight Desktop streaming or an interactive SSH login."
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
        raise InventoryError(f"清单不存在: {path}") from error
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


def exec_program(argv: list[str]) -> None:
    try:
        if os.path.sep in argv[0]:
            os.execv(argv[0], argv)
        os.execvp(argv[0], argv)
    except FileNotFoundError:
        print(f"错误: 找不到本地可执行文件: {argv[0]}", file=sys.stderr)
    except OSError as error:
        print(f"错误: 无法执行 {argv[0]}: {error}", file=sys.stderr)
    raise SystemExit(127)


def main(argv: list[str]) -> None:
    args = parser().parse_args(argv)
    if args.list_machines:
        if args.name is not None or args.moonlight or args.ssh:
            parser().error("--list 不能与机器名或连接模式同时使用")
    elif args.name is None:
        parser().error("必须指定机器名；使用 --list 查看可用名称")

    config_path = args.config if args.config is not None else Path(__file__).resolve().parents[1] / "machines.local.yaml"
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
    else:
        wrapper = Path.home() / ".local/bin/moonlight"
        command = [str(wrapper), "stream", "--", f"{machine['tailnet_ip']}:{machine['moonlight_port']}", "Desktop"]
    exec_program(command)


if __name__ == "__main__":
    main(sys.argv[1:])

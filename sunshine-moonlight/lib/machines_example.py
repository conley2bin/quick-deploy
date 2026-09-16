#!/usr/bin/python3
"""Write the deterministic Chinese machines.example.yaml inventory template.

The example is generated source, not a tracked YAML artifact: the module root
installer refreshes it after every successful role install. Real inventories
(machines.yaml, machines.local.yaml) are never read, created, migrated, or
modified here, and no address discovery of any kind is performed.
"""
from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile

MODULE_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TARGET = MODULE_ROOT / "machines.example.yaml"

TEMPLATE = """\
# 本文件由模块根目录的 ./install.sh 在所选安装阶段全部成功后自动生成/刷新：
# 它是通用占位示例，不是自动扫描或探测的结果，每次成功安装都会按本模板重写；
# 请不要在此文件里填写真实信息，你的改动会被下一次成功安装覆盖。
# 用法：把它复制成实际清单，再按下面的中文注释逐项改成你自己的机器：
#     cp machines.example.yaml machines.yaml
# 实际清单 machines.yaml 由你手工维护：安装器与连接器都不会创建、迁移或覆盖它，
# 也不会自动读取旧文件；示例与实际清单都不会入库（见模块 .gitignore）。
# 只写连接信息：不要放密码、配对 PIN、私钥或任何命令/参数。

# 顶层键固定为 machines；其下每一项是一台机器，键名就是机器名。
machines:
  # 机器名：由你自定，供 ./run_server.sh <机器名> 和 --list 使用；
  # 只能用字母、数字、点、下划线或连字符，且必须以字母或数字开头。
  desktop:
    # 必填。SSH 目的地：单个别名，或 [用户@]主机名/IP；不要写选项或命令。
    # 使用别名时，由系统 ssh 按你自己的 SSH 配置、密钥与 known_hosts 解析。
    ssh: user@100.64.0.10
    # 必填。被控主机的 Tailnet IPv4 字面量；须手工填写，程序不会自动发现。
    tailnet_ip: 100.64.0.10
    # 可选。Moonlight 基准端口，省略时默认 47989。
    # 注意这不是 Sunshine Web UI 端口；Web UI 默认为基准端口 + 1（即 47990）。
    moonlight_port: 47989
    # 可选。SSH 端口；省略时不传 -p，沿用系统 ssh 自己的配置与默认值。
    # ssh_port: 22
    # 可选。备注，仅供自己阅读，不参与连接或命令构造。
    note: 示例备注：请替换为这台主机的说明
"""


class ExampleError(Exception):
    """The example target cannot be published safely."""


def template_bytes() -> bytes:
    return TEMPLATE.encode("utf-8")


def publish(target: Path) -> None:
    """Atomically replace the example, refusing links and non-regular targets."""
    if target.is_symlink():
        raise ExampleError(f"示例路径 {target} 是符号链接；拒绝追随链接，未写入任何文件")
    if target.exists() and not target.is_file():
        raise ExampleError(f"示例路径 {target} 不是普通文件；拒绝覆盖")
    parent = target.parent
    if not parent.is_dir():
        raise ExampleError(f"示例目录 {parent} 不存在或不是目录")
    handle_fd, temp_name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".qdtmp", dir=parent
    )
    try:
        with os.fdopen(handle_fd, "wb") as handle:
            handle.write(template_bytes())
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_name, 0o644)
        # Re-check immediately before the atomic rename: a symlink or directory
        # that appeared after the first check must be refused, never replaced.
        if target.is_symlink() or (target.exists() and not target.is_file()):
            raise ExampleError(f"示例路径 {target} 在写入期间不再是普通文件；拒绝发布")
        os.replace(temp_name, target)
    except BaseException:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def main(argv: list[str]) -> int:
    to_stdout = False
    targets: list[str] = []
    for argument in argv:
        if argument == "--stdout":
            to_stdout = True
        elif argument in ("-h", "--help"):
            print(f"用法: {Path(__file__).name} [--stdout] [示例路径]")
            print("无参数时按模块根目录生成 machines.example.yaml；--stdout 只打印模板，不写文件。")
            return 0
        elif argument.startswith("-"):
            print(f"错误: 未知参数 {argument}", file=sys.stderr)
            return 2
        else:
            targets.append(argument)
    if len(targets) > 1:
        print("错误: 最多指定一个示例路径", file=sys.stderr)
        return 2
    if to_stdout:
        sys.stdout.buffer.write(template_bytes())
        return 0
    target = Path(targets[0]) if targets else DEFAULT_TARGET
    try:
        publish(target)
    except ExampleError as error:
        print(f"错误: {error}", file=sys.stderr)
        return 1
    except OSError as error:
        print(f"错误: 无法写入示例 {target}: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

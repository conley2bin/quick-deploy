#!/usr/bin/python3
"""Create the deterministic all-Chinese inventory only when machines.yaml is absent.

Existing regular inventories belong to the user and are never opened or replaced.
No address discovery or legacy-inventory read/migration happens here.
"""
from __future__ import annotations

import os
from pathlib import Path
import stat
import sys
import tempfile

MODULE_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TARGET = MODULE_ROOT / "machines.yaml"

TEMPLATE = """\
# 本文件只在模块根目录的 ./install.sh 所选安装阶段全部成功、且清单不存在时创建：
# 它是全字段中文的通用占位清单，不是自动扫描或探测的结果，也不含任何真实地址；
# 请把下面各字段改成自己的机器。以后成功安装会保留这份普通文件，不覆盖你的修改。
# 只写连接信息：不要放密码、配对 PIN、私钥或任何命令/参数。

# 顶层键固定为 machines；其下每一项是一台机器，键名就是机器名。
machines:
  # 机器名：由你自定，供 ./run_server.sh <机器名> 和 --list 使用；
  # 只能用字母、数字、点、下划线或连字符，且必须以字母或数字开头。
  desktop:
    # 必填。SSH 目的地：单个别名，或 [user@]主机名/IP；不要写选项或命令。
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
    note: 占位备注：请替换为这台主机的说明
"""


class InventoryError(Exception):
    """The inventory target cannot be published safely."""


def template_bytes() -> bytes:
    return TEMPLATE.encode("utf-8")


def publish(target: Path) -> str:
    """Return 'created' or 'retained'; link(2) never replaces a racing target."""
    try:
        mode = target.lstat().st_mode
    except FileNotFoundError:
        pass
    else:
        if stat.S_ISLNK(mode):
            raise InventoryError(f"清单路径 {target} 是符号链接；拒绝追随链接，未写入任何文件")
        if not stat.S_ISREG(mode):
            raise InventoryError(f"清单路径 {target} 不是普通文件；拒绝覆盖")
        return "retained"
    parent = target.parent
    if not parent.is_dir():
        raise InventoryError(f"清单目录 {parent} 不存在或不是目录")
    handle_fd, temp_name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".qdtmp", dir=parent
    )
    try:
        with os.fdopen(handle_fd, "wb") as handle:
            handle.write(template_bytes())
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_name, 0o644)
        try:
            # Atomic create-only publication: EEXIST protects even a target
            # appearing after lstat, unlike a check followed by os.replace.
            os.link(temp_name, target)
        except FileExistsError as error:
            raise InventoryError(f"清单路径 {target} 在发布期间已出现；拒绝覆盖，请核对后重跑") from error
        return "created"
    finally:
        os.unlink(temp_name)


def main(argv: list[str]) -> int:
    to_stdout = False
    targets: list[str] = []
    for argument in argv:
        if argument == "--stdout":
            to_stdout = True
        elif argument in ("-h", "--help"):
            print(f"用法: {Path(__file__).name} [--stdout] [清单路径]")
            print("无参数时仅在模块根目录缺失 machines.yaml 时创建；已有普通文件原样保留；--stdout 只打印模板。")
            return 0
        elif argument.startswith("-"):
            print(f"错误: 未知参数 {argument}", file=sys.stderr)
            return 2
        else:
            targets.append(argument)
    if len(targets) > 1:
        print("错误: 最多指定一个清单路径", file=sys.stderr)
        return 2
    if to_stdout:
        sys.stdout.buffer.write(template_bytes())
        return 0
    target = Path(targets[0]) if targets else DEFAULT_TARGET
    try:
        outcome = publish(target)
    except InventoryError as error:
        print(f"错误: {error}", file=sys.stderr)
        return 1
    except OSError as error:
        print(f"错误: 无法写入清单 {target}: {error}", file=sys.stderr)
        return 1
    if outcome == "created":
        print("已新建全字段中文占位清单 machines.yaml；请编辑它，填入你的真实机器信息。")
    else:
        print("已保留现有普通清单 machines.yaml；原有内容、权限和修改时间未改动。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

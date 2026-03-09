#!/usr/bin/env python3
from __future__ import annotations
import configparser
import getpass
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = (SCRIPT_DIR / "../..").resolve()

CODEX_HOME = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")).expanduser()
CODEX_CLI_CONFIG = Path(
    os.environ.get("CODEX_CLI_CONFIG", CODEX_HOME / "config.toml")
).expanduser()
ZSHRC_PATH = Path.home() / ".zshrc"

MCP_ROOT = REPO_ROOT / "coding-agent/shared/MCP"
MCP_CONFIG = MCP_ROOT / "mcp.json"
GITMODULES_PATH = REPO_ROOT / ".gitmodules"
DEEP_RESEARCH_ENV_PATH = MCP_ROOT / "deep-research/.env"
DEVELOPER_INSTRUCTIONS_PATH = REPO_ROOT / "coding-agent/codex/developer_instructions.md"

MARKER_BEGIN = "# >>> codex mcp api keys >>>"
MARKER_END = "# <<< codex mcp api keys <<<"
DEV_INST_MARKER_BEGIN = "# >>> codex managed developer_instructions >>>"
DEV_INST_MARKER_END = "# <<< codex managed developer_instructions <<<"


def die(msg: str) -> None:
    print(f"ERROR: {msg}", file=sys.stderr)
    raise SystemExit(1)


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def write_text(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def run_cmd(cmd: list[str], cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess[str]:
    try:
        env = os.environ.copy()
        env.setdefault("GIT_TERMINAL_PROMPT", "0")
        cp = subprocess.run(
            cmd,
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            timeout=120,
            env=env,
            check=False,
        )
    except subprocess.TimeoutExpired:
        die(f"命令超时：{' '.join(cmd)}")
    except FileNotFoundError:
        die(f"缺少命令：{cmd[0]}")

    if check and cp.returncode != 0:
        err = (cp.stderr or cp.stdout or "").strip()
        die(f"命令失败：{' '.join(cmd)}\n{err}")
    return cp


def parse_dotenv(path: Path) -> dict[str, str]:
    if not path.is_file():
        die(f"找不到 env 文件：{path}")
    envs: dict[str, str] = {}
    for raw in read_text(path).splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        key, val = line.split("=", 1)
        key = key.strip()
        val = val.strip()
        if not key:
            continue
        if len(val) >= 2 and ((val[0] == '"' and val[-1] == '"') or (val[0] == "'" and val[-1] == "'")):
            val = val[1:-1]
        envs[key] = val
    return envs


def to_https_url(url: str) -> str:
    if url.startswith("git@github.com:"):
        return "https://github.com/" + url[len("git@github.com:") :]
    return url


def to_ssh_url(url: str) -> str:
    if url.startswith("https://github.com/"):
        return "git@github.com:" + url[len("https://github.com/") :]
    return url


def build_repo_urls(url: str) -> list[str]:
    candidates = [url, to_https_url(url), to_ssh_url(url)]
    out: list[str] = []
    for x in candidates:
        if x and x not in out:
            out.append(x)
    return out


def load_submodule_defs() -> dict[str, dict[str, object]]:
    if not GITMODULES_PATH.is_file():
        return {}

    parser = configparser.ConfigParser()
    parser.read(GITMODULES_PATH, encoding="utf-8")

    defs: dict[str, dict[str, object]] = {}
    prefix = "coding-agent/shared/MCP/"

    for section in parser.sections():
        path = parser.get(section, "path", fallback="").strip()
        url = parser.get(section, "url", fallback="").strip()
        if not path.startswith(prefix):
            continue
        if not url:
            continue
        name = Path(path).name.lower()
        defs[name] = {
            "path": REPO_ROOT / path,
            "urls": build_repo_urls(url),
        }

    return defs


def load_deep_research_values() -> dict[str, str]:
    envs = parse_dotenv(DEEP_RESEARCH_ENV_PATH)

    port = envs.get("DEEP_RESEARCH_PORT", "").strip() or "3000"
    if not re.fullmatch(r"[0-9]+", port):
        die(f"DEEP_RESEARCH_PORT 必须是整数：{port}")

    values: dict[str, str] = {
        "url": f"http://127.0.0.1:{port}/api/mcp",
    }

    access_password = envs.get("ACCESS_PASSWORD", "").strip()
    if access_password:
        values["access_password"] = access_password

    return values


def get_git_default_branch(repo_dir: Path) -> str:
    cp = run_cmd(
        ["git", "-C", str(repo_dir), "symbolic-ref", "--short", "refs/remotes/origin/HEAD"],
        check=False,
    )
    out = cp.stdout.strip()
    if cp.returncode == 0 and out.startswith("origin/"):
        return out.split("/", 1)[1]
    return "main"


def clone_repo(repo_name: str, repo_dir: Path, urls: list[str]) -> None:
    repo_dir.parent.mkdir(parents=True, exist_ok=True)
    last_error = ""
    for url in urls:
        cp = run_cmd(["git", "clone", url, str(repo_dir)], check=False)
        if cp.returncode == 0:
            return
        last_error = (cp.stderr or cp.stdout or "").strip()
    die(
        f"无法克隆 {repo_name} 仓库。\n"
        f"尝试地址：{', '.join(urls)}\n"
        f"{last_error}"
    )


def ensure_repo_latest(repo_name: str, repo_dir: Path, urls: list[str]) -> str:
    if repo_dir.exists() and not (repo_dir / ".git").exists():
        die(f"路径已存在且不是 git 仓库：{repo_dir}")

    if not repo_dir.exists():
        print(f"note: 本地不存在 {repo_name}，开始克隆到 {repo_dir}")
        clone_repo(repo_name, repo_dir, urls)

    run_cmd(["git", "-C", str(repo_dir), "fetch", "--prune", "origin"])
    branch = get_git_default_branch(repo_dir)
    if not branch:
        die(f"无法识别 {repo_name} 默认分支：{repo_dir}")

    status = run_cmd(["git", "-C", str(repo_dir), "status", "--porcelain"]).stdout.strip()
    if status:
        die(
            f"{repo_name} 仓库存在未提交修改，停止自动更新。\n"
            f"请先处理本地改动：{repo_dir}"
        )

    run_cmd(["git", "-C", str(repo_dir), "checkout", "--detach", f"origin/{branch}"])

    rev = run_cmd(["git", "-C", str(repo_dir), "rev-parse", "--short", "HEAD"]).stdout.strip()
    print(f"{repo_name} repo: branch={branch}, commit={rev}")
    return rev


def path_status_in_repo(path: Path) -> str:
    rel = str(path.relative_to(REPO_ROOT))
    out = run_cmd(["git", "-C", str(REPO_ROOT), "status", "--porcelain", "--", rel]).stdout
    return out.strip()


def auto_commit_submodule_updates(selected_names: set[str], submodule_defs: dict[str, dict[str, object]]) -> None:
    changed: list[tuple[str, str]] = []
    for name, cfg in submodule_defs.items():
        if name not in selected_names:
            continue
        repo_path = cfg["path"]
        if not isinstance(repo_path, Path):
            continue
        if not repo_path.exists():
            continue
        if not path_status_in_repo(repo_path):
            continue
        rel = str(repo_path.relative_to(REPO_ROOT))
        changed.append((name, rel))

    if not changed:
        return

    run_cmd(["git", "-C", str(REPO_ROOT), "add", "--"] + [rel for _, rel in changed])
    msg = "chore(submodules): update " + ", ".join(name for name, _ in changed)
    cp = run_cmd(
        ["git", "-C", str(REPO_ROOT), "commit", "-m", msg],
        check=False,
    )
    if cp.returncode != 0:
        err = (cp.stderr or cp.stdout or "").strip()
        die(
            "子模块已更新，但自动提交失败。\n"
            f"提交信息：{msg}\n"
            f"{err}"
        )
    print(f"submodule commit: {msg}")


def escape_double_quotes(value: str) -> str:
    value = value.replace("\\", "\\\\")
    value = value.replace('"', '\\"')
    value = value.replace("$", "\\$")
    value = value.replace("`", "\\`")
    return value


def unescape_double_quotes(value: str) -> str:
    out = []
    i = 0
    while i < len(value):
        ch = value[i]
        if ch == "\\" and i + 1 < len(value):
            out.append(value[i + 1])
            i += 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def parse_zshrc_block(text: str):
    pat = re.compile(
        rf"(?ms)^{re.escape(MARKER_BEGIN)}\n(.*?)^{re.escape(MARKER_END)}\n?"
    )
    m = pat.search(text)
    if not m:
        return {}, None
    block = m.group(1)
    envs: dict[str, str] = {}
    for line in block.splitlines():
        m2 = re.match(r'^export\s+([A-Z0-9_]+)="(.*)"\s*$', line)
        if not m2:
            continue
        key = m2.group(1)
        envs[key] = unescape_double_quotes(m2.group(2))
    return envs, m.span()


def render_zshrc_block(envs: dict[str, str]) -> str:
    lines = [MARKER_BEGIN]
    for key in sorted(envs.keys()):
        val = escape_double_quotes(envs[key])
        lines.append(f'export {key}="{val}"')
    lines.append(MARKER_END)
    return "\n".join(lines) + "\n"


def update_zshrc(envs_to_add: dict[str, str]) -> None:
    if not envs_to_add:
        return

    if ZSHRC_PATH.exists():
        original = read_text(ZSHRC_PATH)
    else:
        original = ""

    existing, span = parse_zshrc_block(original)

    changed = False
    for key, val in envs_to_add.items():
        if key in existing:
            continue
        existing[key] = val
        changed = True

    if not changed:
        return

    block = render_zshrc_block(existing)

    if original:
        backup = ZSHRC_PATH.with_name(
            ZSHRC_PATH.name + ".backup." + datetime.now().strftime("%Y%m%d_%H%M%S")
        )
        write_text(backup, original)

    if span:
        start, end = span
        updated = original[:start] + block + original[end:]
    else:
        updated = original
        if updated and not updated.endswith("\n"):
            updated += "\n"
        if updated and not updated.endswith("\n\n"):
            updated += "\n"
        updated += block

    ZSHRC_PATH.parent.mkdir(parents=True, exist_ok=True)
    write_text(ZSHRC_PATH, updated)


def load_mcp_defs():
    if not MCP_CONFIG.is_file():
        die(f"找不到 MCP 配置：{MCP_CONFIG}")
    data = json.loads(read_text(MCP_CONFIG))
    if isinstance(data, dict) and "mcp_servers" in data:
        items = data["mcp_servers"]
    elif isinstance(data, list):
        items = data
    else:
        die(f"mcp.json 格式错误：{MCP_CONFIG}")
    if not isinstance(items, list):
        die(f"mcp.json mcp_servers 必须是数组：{MCP_CONFIG}")
    items = sorted(items, key=lambda d: str(d.get("name", "")).lower())
    return items


def toml_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def toml_array(items: list[str]) -> str:
    inner = ",".join(f'"{toml_escape(x)}"' for x in items)
    return f"[{inner}]"


def toml_inline_table(items: dict[str, str]) -> str:
    parts = []
    for k, v in items.items():
        parts.append(f'{k} = "{toml_escape(v)}"')
    return "{ " + ", ".join(parts) + " }"


def remove_mcp_block(text: str, name: str) -> str:
    pat = re.compile(rf"(?ms)^\[mcp_servers\.{re.escape(name)}\]\n.*?(?=^\[|\Z)")
    return pat.sub("", text, count=1)


def upsert_mcp_block(text: str, name: str, block: str) -> str:
    pat = re.compile(rf"(?ms)^\[mcp_servers\.{re.escape(name)}\]\n.*?(?=^\[|\Z)")
    if pat.search(text):
        return pat.sub(block + "\n", text, count=1)
    if text and not text.endswith("\n"):
        text += "\n"
    text += "\n" + block
    return text


def render_block(name: str, lines: list[str]) -> str:
    return "\n".join([f"[mcp_servers.{name}]"] + lines) + "\n"


def remove_managed_developer_instructions_block(text: str) -> str:
    pat = re.compile(
        rf"(?ms)^{re.escape(DEV_INST_MARKER_BEGIN)}\n.*?^{re.escape(DEV_INST_MARKER_END)}\n?"
    )
    return pat.sub("", text, count=1)


def remove_plain_developer_instructions(text: str) -> str:
    pat = re.compile(
        r'(?ms)^developer_instructions\s*=\s*(?:"""[\s\S]*?"""|\'\'\'[\s\S]*?\'\'\'|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\')\s*(?:\n|$)'
    )
    while True:
        updated = pat.sub("", text, count=1)
        if updated == text:
            return text
        text = updated


def render_developer_instructions_block(instructions: str) -> str:
    if "'''" in instructions:
        die(
            f"developer_instructions.md 包含连续三个单引号（'''），无法写入 TOML literal string：{DEVELOPER_INSTRUCTIONS_PATH}"
        )

    body = instructions.rstrip("\n")
    return "\n".join(
        [
            DEV_INST_MARKER_BEGIN,
            "developer_instructions = '''",
            body,
            "'''",
            DEV_INST_MARKER_END,
        ]
    ) + "\n"


def sync_developer_instructions() -> None:
    if not DEVELOPER_INSTRUCTIONS_PATH.is_file():
        die(f"找不到 {DEVELOPER_INSTRUCTIONS_PATH}")

    instructions = read_text(DEVELOPER_INSTRUCTIONS_PATH)
    if not instructions.strip():
        die(f"developer_instructions.md 为空：{DEVELOPER_INSTRUCTIONS_PATH}")

    block = render_developer_instructions_block(instructions)
    config_text = read_text(CODEX_CLI_CONFIG) if CODEX_CLI_CONFIG.exists() else ""

    updated = remove_managed_developer_instructions_block(config_text)
    updated = remove_plain_developer_instructions(updated)

    if updated and not updated.endswith("\n"):
        updated += "\n"
    if updated and not updated.endswith("\n\n"):
        updated += "\n"
    updated += block

    if updated == config_text:
        print("developer_instructions: 未变化")
        return

    CODEX_CLI_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    write_text(CODEX_CLI_CONFIG, updated)
    print(f"developer_instructions: 已同步到 {CODEX_CLI_CONFIG}")


def substitute_template(template: str, values: dict[str, str]) -> tuple[str, list[str]]:
    missing = []

    def repl(m: re.Match) -> str:
        key = m.group(1)
        val = values.get(key, "")
        if not val:
            missing.append(key)
            return ""
        return val

    out = re.sub(r"\{([A-Za-z0-9_]+)\}", repl, template)
    return out, missing


def prompt_input(label: str, default=None) -> str:
    if default:
        prompt = f"{label} [{default}] "
    else:
        prompt = f"{label} "
    val = input(prompt).strip()
    if not val and default is not None:
        return default
    return val


def prompt_int(label: str, default=None):
    default_str = str(default) if default is not None else None
    val = prompt_input(label, default_str)
    if not val:
        return None
    if not re.fullmatch(r"[0-9]+", val):
        die(f"{label} 必须是整数：{val}")
    return int(val)


def prompt_secret(label: str, optional: bool = False) -> str:
    val = getpass.getpass(f"{label}: ")
    if not val and not optional:
        return ""
    return val


def collect_required_env_vars(selected: list[dict]) -> dict[str, set[str]]:
    required: dict[str, set[str]] = {}
    for mcp in selected:
        name = str(mcp.get("name", "")).strip()
        env_vars = mcp.get("env_vars", [])
        if not name or not isinstance(env_vars, list):
            continue
        for var in env_vars:
            if not isinstance(var, str):
                continue
            key = var.strip()
            if not key:
                continue
            required.setdefault(key, set()).add(name)
    return required


def resolve_required_env_vars(
    required: dict[str, set[str]],
    zshrc_envs: dict[str, str],
) -> tuple[dict[str, str], dict[str, str]]:
    resolved: dict[str, str] = {}
    sources: dict[str, str] = {}

    for key in sorted(required.keys()):
        env_val = os.environ.get(key, "")
        if env_val:
            resolved[key] = env_val
            sources[key] = "当前终端环境变量"
            continue

        zshrc_val = zshrc_envs.get(key, "")
        if zshrc_val:
            resolved[key] = zshrc_val
            sources[key] = "~/.zshrc marker"
            continue

        sources[key] = "缺失"

    return resolved, sources


def resolve_cwd(cwd):
    if not cwd:
        return None
    out = cwd.replace("${REPO_ROOT}", str(REPO_ROOT))
    out = out.replace("${HOME}", str(Path.home()))
    return out


def configure_http(mcp: dict, values: dict):
    http_cfg = mcp.get("http", {})
    missing = []

    url_val = values.get("url", "")
    if not url_val:
        if http_cfg.get("url"):
            url_val = http_cfg.get("url")
        elif http_cfg.get("url_template"):
            rendered, miss = substitute_template(http_cfg.get("url_template"), values)
            if miss:
                missing.extend(miss)
            url_val = rendered

    if not url_val:
        missing.append("url")

    timeout_val = None
    if values.get("timeout"):
        timeout_val = values.get("timeout")
    elif http_cfg.get("timeout") is not None:
        timeout_val = http_cfg.get("timeout")

    headers = {}
    for k, tmpl in mcp.get("headers", {}).items():
        rendered, miss = substitute_template(tmpl, values)
        if miss:
            continue
        headers[k] = rendered

    lines = []
    lines.append(f'url = "{toml_escape(url_val)}"')
    transport = http_cfg.get("transportType")
    if transport:
        lines.append(f'transportType = "{toml_escape(transport)}"')

    bearer_token_env_var = http_cfg.get("bearer_token_env_var")
    if bearer_token_env_var:
        lines.append(f'bearer_token_env_var = "{toml_escape(str(bearer_token_env_var))}"')

    if timeout_val is not None and str(timeout_val).strip():
        lines.append(f"timeout = {int(timeout_val)}")
    if headers:
        lines.append(f"headers = {toml_inline_table(headers)}")
    if http_cfg.get("startup_timeout_sec") is not None:
        lines.append(f"startup_timeout_sec = {int(http_cfg.get('startup_timeout_sec'))}")

    return lines, missing


def configure_stdio(mcp: dict, values: dict):
    missing: list[str] = []

    command = mcp.get("command")
    args = mcp.get("args", [])
    if not command:
        missing.append("command")

    lines = []
    if command:
        lines.append(f'command = "{toml_escape(command)}"')
    if args:
        lines.append(f"args = {toml_array(args)}")

    cwd = resolve_cwd(mcp.get("cwd"))
    if cwd:
        lines.append(f'cwd = "{toml_escape(cwd)}"')

    env_vars = mcp.get("env_vars", [])
    if env_vars:
        lines.append(f"env_vars = {toml_array(env_vars)}")

    if mcp.get("startup_timeout_sec") is not None:
        lines.append(f"startup_timeout_sec = {int(mcp.get('startup_timeout_sec'))}")

    return lines, missing


def sync_agents_and_commands() -> None:
    src = REPO_ROOT / "coding-agent/codex/AGENTS.md"
    dst = CODEX_HOME / "AGENTS.md"
    override = CODEX_HOME / "AGENTS.override.md"

    if not src.is_file():
        die(f"找不到 {src}")

    CODEX_HOME.mkdir(parents=True, exist_ok=True)

    if override.exists():
        print(f"note: 检测到 {override}，Codex 将优先使用它而不是 {dst}")

    if dst.exists() and src.read_bytes() == dst.read_bytes():
        print("Global AGENTS.md: 未变化")
    else:
        if dst.exists():
            backup = dst.with_name(dst.name + ".backup." + datetime.now().strftime("%Y%m%d_%H%M%S"))
            shutil.copy2(dst, backup)
        shutil.copy2(src, dst)
        print(f"Global AGENTS.md: 已更新到 {dst}")

    src_dir = REPO_ROOT / "coding-agent/codex/commands"
    dst_dir = CODEX_HOME / "prompts"

    if not src_dir.is_dir():
        die(f"找不到目录 {src_dir}")

    dst_dir.mkdir(parents=True, exist_ok=True)

    files = sorted(src_dir.glob("*.md"))
    if not files:
        print(f"Custom prompts: 未找到 {src_dir}/*.md")
        return

    copied = 0
    names = []
    for f in files:
        base = f.name
        names.append(base[:-3])
        target = dst_dir / base
        if target.exists() and target.read_bytes() == f.read_bytes():
            continue
        shutil.copy2(f, target)
        copied += 1

    print(f"Custom prompts: 已安装到 {dst_dir}（更新/覆盖 {copied} 个文件）")
    if names:
        print("自定义 commands 使用方式：")
        for n in names:
            print(f"  - /prompts:{n}")


def main() -> None:
    CODEX_HOME.mkdir(parents=True, exist_ok=True)

    print("[1/3] sync AGENTS.md and commands")
    sync_agents_and_commands()
    print("")

    print("[2/3] sync developer_instructions")
    sync_developer_instructions()
    print("")

    print("[3/3] configure MCP servers")
    mcp_defs = load_mcp_defs()
    if not mcp_defs:
        die(f"未找到任何 MCP 定义：{MCP_CONFIG}")

    print("请选择要配置到 Codex 的 MCP servers：")
    print("")
    for i, cfg in enumerate(mcp_defs, start=1):
        name = cfg.get("name")
        desc = cfg.get("description", "")
        if not name:
            die(f"mcp.json 缺少 name: {MCP_CONFIG}")
        if desc:
            print(f"  {i}) {name:<20} ({desc})")
        else:
            print(f"  {i}) {name}")
    print("")
    print("输入：1,3,5    或 all    或 n")
    selection_raw = input("> ").strip().lower().replace(" ", "")

    if selection_raw in ("", "n"):
        print("未选择任何 MCP（跳过 MCP 配置）。")
        return

    if selection_raw == "all":
        selected_idx = list(range(1, len(mcp_defs) + 1))
    else:
        if not re.fullmatch(r"[0-9,]+", selection_raw):
            die(f"输入格式错误：{selection_raw}（应为 1,3,5 / all / n）")
        selected_idx = []
        for x in selection_raw.split(","):
            if not x:
                continue
            n = int(x)
            if n < 1 or n > len(mcp_defs):
                die(f"输入包含无效选项：{x}（有效范围：1-{len(mcp_defs)}）")
            selected_idx.append(n)

    selected = [mcp_defs[i - 1] for i in selected_idx]
    selected_names = {str(cfg.get("name", "")) for cfg in selected}
    submodule_defs = load_submodule_defs()

    for name in sorted(submodule_defs.keys()):
        if name not in selected_names:
            continue
        cfg = submodule_defs[name]
        repo_dir = cfg.get("path")
        urls = cfg.get("urls")
        if not isinstance(repo_dir, Path):
            continue
        if not isinstance(urls, list):
            continue
        repo_urls = [u for u in urls if isinstance(u, str) and u]
        if not repo_urls:
            continue
        print(f"sync {name} repo to latest origin...")
        ensure_repo_latest(
            repo_name=name,
            repo_dir=repo_dir,
            urls=repo_urls,
        )
    auto_commit_submodule_updates(selected_names, submodule_defs)

    zshrc_envs, _ = ({}, None)
    if ZSHRC_PATH.exists():
        zshrc_envs, _ = parse_zshrc_block(read_text(ZSHRC_PATH))

    envs_to_write: dict[str, str] = {}
    required_env_vars = collect_required_env_vars(selected)
    resolved_env_values, key_sources = resolve_required_env_vars(required_env_vars, zshrc_envs)

    if required_env_vars:
        print("")
        print("MCP 必填 key 状态：")
        for key in sorted(required_env_vars.keys()):
            source = key_sources.get(key, "缺失")
            used_by = ",".join(sorted(required_env_vars[key]))
            if source == "缺失":
                print(f"  - {key}: 缺失（用于: {used_by}）")
            else:
                print(f"  - {key}: 已读取（来源: {source}；用于: {used_by}）")

        missing_keys = [k for k in sorted(required_env_vars.keys()) if key_sources.get(k) == "缺失"]
        for key in missing_keys:
            val = prompt_secret(key, optional=False)
            if not val:
                continue
            resolved_env_values[key] = val
            key_sources[key] = "交互输入"
            if key not in zshrc_envs:
                envs_to_write[key] = val

        unresolved_keys = [k for k in sorted(required_env_vars.keys()) if not resolved_env_values.get(k)]
        if unresolved_keys:
            print(f"warning: 以下 key 仍缺失，相关 MCP 配置会被移除：{', '.join(unresolved_keys)}")

    if CODEX_CLI_CONFIG.exists():
        config_text = read_text(CODEX_CLI_CONFIG)
    else:
        config_text = ""

    for mcp in selected:
        name = mcp.get("name")
        if not name:
            die(f"mcp.json 缺少 name: {MCP_CONFIG}")

        env_vars = mcp.get("env_vars", [])
        values = {}
        missing = []
        if env_vars:
            for var in env_vars:
                if not isinstance(var, str):
                    continue
                key = var.strip()
                if not key:
                    continue
                val = resolved_env_values.get(key, "")
                if not val:
                    missing.append(key)
                    continue
                values[key] = val
                if key_sources.get(key) == "当前终端环境变量" and key not in zshrc_envs and key not in envs_to_write:
                    envs_to_write[key] = val

        if name == "deep-research":
            values.update(load_deep_research_values())

        prompts = mcp.get("prompts", [])
        if name != "deep-research":
            for p in prompts:
                key = p.get("key")
                if not key:
                    continue
                if p.get("type") == "secret":
                    val = prompt_secret(p.get("label", key), optional=p.get("optional", False))
                    if not val and not p.get("optional", False):
                        missing.append(key)
                    values[key] = val
                    continue
                if p.get("type") == "int":
                    val = prompt_int(p.get("label", key), p.get("default"))
                    if val is None and not p.get("optional", False):
                        missing.append(key)
                    values[key] = "" if val is None else str(val)
                    continue
                default = p.get("default")
                if p.get("default_from"):
                    http_cfg = mcp.get("http", {})
                    template = http_cfg.get(p.get("default_from"), "")
                    rendered, miss = substitute_template(template, values)
                    if not miss and rendered:
                        default = rendered
                val = prompt_input(p.get("label", key), default)
                if not val and not p.get("optional", False):
                    missing.append(key)
                values[key] = val

        if missing:
            print(f"warning: {name} 缺少必填参数，移除配置：{', '.join(sorted(set(missing)))}")
            config_text = remove_mcp_block(config_text, name)
            continue

        if mcp.get("type") == "http":
            lines, miss = configure_http(mcp, values)
        elif mcp.get("type") == "stdio":
            lines, miss = configure_stdio(mcp, values)
        else:
            die(f"未知 MCP 类型：{mcp.get('type')} ({name})")

        if miss:
            print(f"warning: {name} 缺少必填参数，移除配置：{', '.join(sorted(set(miss)))}")
            config_text = remove_mcp_block(config_text, name)
            continue

        if not lines:
            config_text = remove_mcp_block(config_text, name)
            continue

        block = render_block(name, lines)
        config_text = upsert_mcp_block(config_text, name, block)

    CODEX_CLI_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    write_text(CODEX_CLI_CONFIG, config_text)
    print(f"wrote Codex config: {CODEX_CLI_CONFIG}")

    if envs_to_write:
        update_zshrc(envs_to_write)
        print(f"wrote zshrc keys: {ZSHRC_PATH}")


if __name__ == "__main__":
    main()

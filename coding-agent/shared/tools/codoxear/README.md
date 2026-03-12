# codoxear（非 MCP）快速接入

该目录用于快速配置 [yiwenlu66/codoxear](https://github.com/yiwenlu66/codoxear)。

`codoxear` 不是 MCP server，不会写入 `coding-agent/shared/MCP/mcp.json`。  
它是本地 Web + broker 方案：用浏览器接力本机正在运行的 Codex TUI 会话。

## 1. 一键配置

```bash
cd <quick-deploy主仓库>
source .venv/bin/activate
cd coding-agent/shared/tools/codoxear
./setup.sh
```

`setup.sh` 会执行：

- 克隆/更新上游仓库到 `codoxear`
- 每次运行都执行 `fetch + reset --hard + clean`，对齐远端最新提交
- 如果设置了 `CODOXEAR_REPO_URL`，已有 checkout 的 `origin` 也会被改成这个地址
- 用 `python3 -m pip -e` 安装 `codoxear-server` 和 `codoxear-broker`
  - 未激活虚拟环境时：`--user`
  - 已激活虚拟环境时：不加 `--user`
- 生成本目录 `.env`（若不存在）
- 向当前 shell 对应 rc 文件写入 `codex()` wrapper（marker block）

如果 `.env` 里 `CODEX_WEB_PASSWORD` 为空，脚本会停止并提示先填写。

## 2. 启动服务

```bash
cd coding-agent/shared/tools/codoxear
./run_server.sh
```

`run_server.sh` 启动前也会先同步到远端最新提交；如果本地 editable install 对应的提交或路径已经过期，会先重新执行一次安装。
如果当前 shell 没有导出 `CODEX_WEB_PASSWORD`、`CODEX_WEB_HOST`、`CODEX_WEB_PORT`、`CODEX_HOME`、`CODEX_BIN`，`run_server.sh` 会从本目录 `.env` 补全这些变量。

默认访问地址：

```text
http://127.0.0.1:8743
```

手机局域网访问请改为你电脑 IP，例如 `http://192.168.x.x:8743`。

## 3. 使 wrapper 生效

`setup.sh` 会写入如下函数到 rc 文件：

```sh
codex() {
  if [ "${CODEX_WEB_OWNER:-}" = "web" ]; then
    local _real_codex
    _real_codex="$(whence -p codex 2>/dev/null)"
    if [ -n "${_real_codex}" ]; then
      "${_real_codex}" "$@"
      return $?
    fi
    echo "error: 未找到真实 codex 可执行文件" >&2
    return 127
  fi
  if command -v codoxear-broker >/dev/null 2>&1; then
    codoxear-broker -- "$@"
    return
  fi
  if [ -x "<quick-deploy>/.venv/bin/codoxear-broker" ]; then
    "<quick-deploy>/.venv/bin/codoxear-broker" -- "$@"
    return
  fi
  echo "error: codoxear-broker 未找到" >&2
  return 127
}
```

执行一次：

```bash
source ~/.zshrc
```

或重开终端。之后你照常运行 `codex`，会话会被 codoxear 发现。

## 4. 配置项

在本目录 `.env` 里设置：

- `CODEX_WEB_PASSWORD`（必填）
- `CODEX_WEB_HOST`（默认 `::`）
- `CODEX_WEB_PORT`（默认 `8743`）
- `CODEX_HOME`（默认 `~/.codex`）
- `CODEX_BIN`（默认 `codex`）

## 5. 安全边界

- 默认是明文 HTTP，无内建 TLS。
- 远程访问请自行通过 VPN、SSH 隧道或 HTTPS 反代保护传输链路。

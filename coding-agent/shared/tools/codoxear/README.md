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

- 克隆/更新上游仓库到 `upstream/codoxear`
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

默认访问地址：

```text
http://127.0.0.1:8743
```

手机局域网访问请改为你电脑 IP，例如 `http://192.168.x.x:8743`。

## 3. 使 wrapper 生效

`setup.sh` 会写入如下函数到 rc 文件：

```sh
codex() {
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

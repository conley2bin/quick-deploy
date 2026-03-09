# deep-research（Docker）

该目录用于部署 `u14app/deep-research` 的 MCP HTTP 服务，供 `coding-agent/codex/setup.py` 里的 `deep-research` 选项连接。

## 1. 初始化

```bash
cd coding-agent/shared/MCP/deep-research
```

运行 `./start_service.sh` 时：

- 如果 `.env` 不存在，会自动创建模板并退出，等待你填写 key
- 自动创建模板时，会优先带入当前终端里的 `OPENAI_COMPATIBLE_API_BASE_URL`、`OPENAI_COMPATIBLE_API_KEY`、`TAVILY_API_KEY`
- 如果 `.env` 已存在，会自动读取 `~/.codex/config.toml` 顶层 `model`，同步到 `.env` 的 `MCP_THINKING_MODEL` 和 `MCP_TASK_MODEL`
- 默认 `MCP_SEARCH_PROVIDER=tavily`
- 如果当前 shell 已导出 `OPENAI_COMPATIBLE_API_KEY` 或 `TAVILY_API_KEY`，会优先覆盖写入 `.env`
- 若当前 shell 缺少 `TAVILY_API_KEY`，会自动将 `MCP_SEARCH_PROVIDER` 从 `tavily` 切换到 `model` 并打印提示

`.env` 至少需要填写：

- `OPENAI_COMPATIBLE_API_KEY`
- `OPENAI_COMPATIBLE_API_BASE_URL`
- 按需设置 `ACCESS_PASSWORD`
- `MCP_AI_PROVIDER` 保持 `openaicompatible`
- `MCP_SEARCH_PROVIDER` 可用值：`model`、`tavily`、`firecrawl`、`exa`、`bocha`、`searxng`
- 若使用 `tavily`，需要在当前终端导出 `TAVILY_API_KEY`（脚本会回写到 `.env`）

## 2. 启动（含开机自启设置）

```bash
cd coding-agent/shared/MCP/deep-research
./start_service.sh
```

默认 MCP 地址：

```text
http://127.0.0.1:3000/api/mcp
```

如果设置了 `ACCESS_PASSWORD`，在 Codex MCP 配置中写：

```toml
headers = { Authorization = "Bearer <ACCESS_PASSWORD>" }
```

`deep-research` 单次调用链较长，建议同时配置：

```toml
timeout = 600
tool_timeout_sec = 600
```

- `timeout`：HTTP 传输超时
- `tool_timeout_sec`：Codex 对单次 MCP 工具调用的超时

## 3. 运维命令

```bash
cd coding-agent/shared/MCP/deep-research
docker compose logs -f
docker compose ps
docker compose down
```

---
name: codex-mcp-integration
description: 为 coding-agent 仓库新增或改造 MCP 接入策略。用于新增 MCP server、切换通信方式（stdio 或 http）、补齐鉴权与环境变量注入、修改 coding-agent/shared/MCP/mcp.json、扩展 coding-agent/codex/setup.py 渲染逻辑、以及设计 shared/MCP/server-name 本地部署目录时。
---

# Codex MCP Integration

基于当前仓库结构执行，不重写已有机制。

先读以下文件再动手：
- `coding-agent/shared/MCP/mcp.json`
- `coding-agent/codex/setup.py`
- 目标 MCP 目录（如果已存在）：`coding-agent/shared/MCP/<name>/`

## 决策入口

先确定 5 个参数：
- `name`：MCP 名称，建议与目录名一致并用小写连字符。
- `transport`：`stdio` 或 `http`。
- `hosting`：本地进程、本地 HTTP、自建远端 HTTP、第三方托管 HTTP。
- `auth`：无鉴权、环境变量、Bearer header、URL query 模板注入。
- `runtime`：`uv`、`npx`、`uvx`、docker compose、系统服务。

## 实施流程

1. 先用 `mcp.json` 可表达的字段完成配置，不改 `setup.py`。
2. 如果 `mcp.json` 无法表达，再扩展 `setup.py`，保持 fail-fast。
3. 如果服务是本地部署型，再补 `shared/MCP/<name>/` 启动脚本与 README。
4. 用 `python coding-agent/codex/setup.py` 走一遍配置生成，验证 `~/.codex/config.toml`。

## 通信方式配置策略

### 1) stdio MCP

适用：服务进程由 Codex 客户端直接拉起，进程 stdin/stdout 承载 MCP。

执行：
- 在 `mcp.json` 新增条目：`type=stdio`，至少包含 `command`。
- 需要固定工作目录时设置 `cwd`，路径用 `${REPO_ROOT}` 或 `${HOME}` 模板。
- 需要 key 时写 `env_vars`，不要在 `args` 里硬编码密钥。
- 需要更长握手时间时设置 `startup_timeout_sec`。

约束来源：
- `setup.py` 通过 `configure_stdio()` 渲染字段，不识别未知字段。
- `env_vars` 会参与缺失检查，缺失会触发配置块移除。

### 2) http MCP（远端托管或本地 HTTP）

适用：服务已经是 HTTP endpoint，Codex 只负责连接。

执行：
- 在 `mcp.json` 新增条目：`type=http`。
- 在 `http` 中写 `transportType`（当前仓库使用 `streamable-http`）和 `timeout`。
- endpoint 固定时用 `http.url`。
- endpoint 依赖 key 时用 `http.url_template` + `env_vars`。
- Bearer token 场景优先用 `http.bearer_token_env_var`（值是环境变量名）。
- 只有服务端不支持 `bearer_token_env_var` 时，才用 `headers` 注入鉴权头。
- 只读场景优先使用服务端提供的 readonly endpoint（例如 `/readonly`）。

约束来源：
- `setup.py` 通过 `configure_http()` 渲染 `url/transportType/bearer_token_env_var/timeout/headers`。
- `headers` 模板变量缺失时该 header 被跳过。

### 3) 本地自部署 HTTP MCP

适用：服务由仓库脚本启动，再由 Codex 通过 HTTP 连接。

执行：
- 在 `shared/MCP/<name>/` 提供 `README.md`、启动脚本、可选 docker compose。
- 启动脚本负责：创建模板 env、覆盖运行时环境变量、必要校验、启动服务。
- `mcp.json` 里仅声明连接参数，不嵌入部署细节。
- 若 endpoint 和 token 需要从本地文件派生，扩展 `setup.py` 的值加载函数。

## setup.py 扩展规则

只在以下条件满足时修改 `setup.py`：
- `mcp.json` + `env_vars` + `url_template` + `headers` + `prompts` 仍无法覆盖需求。

实现方式：
- 新增 `load_<name>_values()`，集中解析本地配置并做输入校验。
- 在主循环中按 `name` 注入 `values.update(...)`。
- 校验失败直接 `die(...)`，不要静默 fallback。

## 子模块同步规则

如果 MCP 目录走 git submodule，额外满足：
- `.gitmodules` 中 path 需位于 `coding-agent/shared/MCP/` 下。
- `setup.py` 用目录名小写和 MCP 名称匹配，命名不一致会跳过自动同步。
- 目标子模块有未提交改动时，`setup.py` 会停止更新。

## 校验步骤

1. 运行 `python coding-agent/codex/setup.py`，仅选择新增 MCP。
2. 观察输出中的 key 状态和 warning，确认没有“缺 key 被移除”。
3. 检查 `~/.codex/config.toml` 是否出现 `[mcp_servers.<name>]` 且字段完整。
4. Bearer token 场景确认配置中有 `bearer_token_env_var`，且不存在明文 token 形式的 `Authorization` 值。
5. 本地服务型 MCP 先手动启动，再验证 endpoint 可达。
6. 失败时先定位是配置渲染问题、服务启动问题，还是鉴权问题，不加兜底默认值。

## 参考模板

需要配置片段时，读取：
- `references/mcp-entry-templates.md`

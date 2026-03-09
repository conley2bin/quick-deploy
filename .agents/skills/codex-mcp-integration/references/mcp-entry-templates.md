# MCP Entry Templates

## 1) stdio: 本地进程

```json
{
  "name": "example-stdio",
  "description": "STDIO，本地进程 MCP",
  "type": "stdio",
  "command": "uv",
  "args": ["run", "python", "server.py"],
  "cwd": "${REPO_ROOT}/coding-agent/shared/MCP/example-stdio",
  "env_vars": ["EXAMPLE_API_KEY"],
  "startup_timeout_sec": 120
}
```

## 2) http: 远端托管，URL 模板注入 key

```json
{
  "name": "example-http-hosted",
  "description": "HTTP，远端托管 MCP",
  "type": "http",
  "env_vars": ["EXAMPLE_API_KEY"],
  "http": {
    "transportType": "streamable-http",
    "timeout": 60,
    "url_template": "https://mcp.example.com/api/mcp?apiKey={EXAMPLE_API_KEY}"
  }
}
```

## 3) http: 固定 URL + bearer_token_env_var（优先）

```json
{
  "name": "example-http-bearer-env",
  "description": "HTTP，Bearer 鉴权（token 来自环境变量）",
  "type": "http",
  "env_vars": ["EXAMPLE_ACCESS_TOKEN"],
  "http": {
    "transportType": "streamable-http",
    "timeout": 120,
    "url": "https://mcp.example.com/api/mcp",
    "bearer_token_env_var": "EXAMPLE_ACCESS_TOKEN"
  }
}
```

## 4) http: 固定 URL + Bearer header（仅兼容场景）

当目标服务不支持 `bearer_token_env_var`，且必须通过自定义 header 传 token 时使用。

```json
{
  "name": "example-http-auth-header",
  "description": "HTTP，Bearer 鉴权 MCP（header 方案）",
  "type": "http",
  "http": {
    "transportType": "streamable-http",
    "timeout": 120,
    "url": "https://mcp.example.com/api/mcp"
  },
  "headers": {
    "Authorization": "Bearer {access_token}"
  },
  "prompts": [
    {
      "key": "access_token",
      "label": "ACCESS TOKEN",
      "type": "secret",
      "optional": false
    }
  ]
}
```

## 5) http: 本地部署服务（由 setup.py 派生 URL）

`mcp.json`:

```json
{
  "name": "example-local-http",
  "description": "HTTP，本地部署 MCP",
  "type": "http",
  "http": {
    "transportType": "streamable-http",
    "timeout": 600
  },
  "headers": {
    "Authorization": "Bearer {access_password}"
  }
}
```

`setup.py`:

```python
def load_example_local_http_values() -> dict[str, str]:
    envs = parse_dotenv(MCP_ROOT / "example-local-http/.env")
    port = envs.get("EXAMPLE_PORT", "").strip() or "3000"
    if not re.fullmatch(r"[0-9]+", port):
        die(f"EXAMPLE_PORT 必须是整数：{port}")

    values = {"url": f"http://127.0.0.1:{port}/api/mcp"}
    token = envs.get("ACCESS_PASSWORD", "").strip()
    if token:
        values["access_password"] = token
    return values
```

主循环注入：

```python
if name == "example-local-http":
    values.update(load_example_local_http_values())
```

## 6) http: 同一服务的读写与只读双入口（通用）

读写 endpoint：

```json
{
  "name": "example-http-write",
  "description": "HTTP，远端 MCP（读写入口）",
  "type": "http",
  "env_vars": ["EXAMPLE_ACCESS_TOKEN"],
  "http": {
    "url": "https://mcp.example.com/mcp/",
    "transportType": "streamable-http",
    "timeout": 60,
    "startup_timeout_sec": 60,
    "bearer_token_env_var": "EXAMPLE_ACCESS_TOKEN"
  }
}
```

只读 endpoint：

```json
{
  "name": "example-http-readonly",
  "description": "HTTP，远端 MCP（只读入口）",
  "type": "http",
  "env_vars": ["EXAMPLE_ACCESS_TOKEN"],
  "http": {
    "url": "https://mcp.example.com/mcp/readonly",
    "transportType": "streamable-http",
    "timeout": 60,
    "startup_timeout_sec": 60,
    "bearer_token_env_var": "EXAMPLE_ACCESS_TOKEN"
  }
}
```

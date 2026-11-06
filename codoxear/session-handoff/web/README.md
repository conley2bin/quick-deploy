# 会话接力界面

宿主按 `core.js → target-model.js → panel.js → bootstrap.js` 拼为独立同源脚本；URL 前缀来自该脚本地址。

- Details 后的原生样式图标打开确认面板；只提供可选目标模型/提供商/推理强度，没有附注或“未经校对继续”。
- `target-model.js` 复用原生 `createNewSessionController` 的目录、分组、搜索和推理选择规则，只维护接力自己的草稿，不调用原生 Start 或偏好写入方法。无法读取目录时只可明确沿用源设置；未提交的搜索或失效选择阻止确认。
- 状态只有 `waiting_source`（等待源会话写交接）、`creating`（创建新会话）、`completed`（完成）、`failed`（失败及原因）。完成时显示新会话链接。
- 每个源的草稿、请求和状态相互独立。所有操作先核对当前 URL 和显示身份，切换源后不会把旧源操作或模型带给新源。
- 本地只保存每源请求 UUID/目标选择或操作 UUID。显式目标在 POST 前冻结保存；待确认请求不自动重放，用户可用同一 UUID 查询原请求。服务器失败不自动重试。
- 页面可见时才轮询所选源的活动操作；终态停止轮询。登出/卸载清理请求、定时器、监听器和局部观察器。旧服务操作查询 404 后释放本地引用，不触发新接力。
- 原生模型/创建设置仅影响新会话。交接文件内容核实由新会话完成；界面不展示任何审阅、哈希或回执协议。

从仓库根运行：

```sh
CODOXEAR_STATIC_DIR="$PWD/codoxear/.runtime/venv/lib/python3.12/site-packages/codoxear/static" \
  node --test codoxear/session-handoff/tests/*.test.cjs
```

测试用可控 DOM/HTTP 和只读原生模型模块，覆盖状态、源隔离、重复点击、身份切换、模型选择、失败和生命周期，不访问生产 API。

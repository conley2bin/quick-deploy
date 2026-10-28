# 上游补丁（只读上游 main 的临时修改层）

`../.runtime/upstream` 是 `https://github.com/yiwenlu66/codoxear` 的单分支 checkout，
`run_server.sh` 每次启动都会 fetch origin/main 并 `git checkout --detach` 到远程最新提交，
再用 `git archive` 重新安装到 `.runtime/venv`。因此**直接改上游源码不是持久状态**：
只要 checkout 干净，下一次 `run_server.sh` 就会把这些修改覆盖掉。

本目录保存尚未进入上游 main 的补丁，来源是当前 checkout 的 local diff。

## 现有补丁

| 补丁 | 作用 | 状态 |
| --- | --- | --- |
| `0001-clipboard-http-fallback.patch` | 手机浏览器用 `http://<内网/tailscale IP>:8743` 打开时，`window.isSecureContext === false`、`navigator.clipboard` 不存在，原实现直接抛 “Clipboard API unavailable; requires a secure context (HTTPS)”，所有复制按钮（代码块、消息、会话导出、文件选区、诊断）全部失败。补丁拆出 `codoxear/static/app_clipboard.js`，改成三级阶梯：安全上下文用异步 Clipboard API → 否则用 `execCommand("copy")` + 临时 textarea（不需要安全上下文，只需要用户手势）→ 两者都被拒时给出“长按文本手动复制”的说明性错误。 | 本地已部署并验证；等待上游合并 |

## 应用 / 复核

```bash
cd codoxear/.runtime/upstream
git apply --check ../patches/0001-clipboard-http-fallback.patch && \
git apply ../patches/0001-clipboard-http-fallback.patch
npx --yes esbuild codoxear/static/app.js --bundle --minify \
  --outfile=codoxear/static/dist/app.bundle.js --format=esm
```

该 esbuild 重建对 `e42cfe63` 是可复现的：打过补丁后重建的 `dist/app.bundle.js`
sha256 为 `e973af4cc2349db2866039e7f34b175496c26e9e00db634d3d2ec65ac8c09cd0`。

## 注意

- checkout 有本地修改时 `run_server.sh` / `setup.sh` 会**直接报错退出**（“Codoxear runtime checkout
  有本地修改，无法确认远程 main 最新版本”），不会静默回退。这是故意的：宁可启动失败，也不要
  悄悄把修复丢掉。
- 上游一旦包含 `codoxear/static/app_clipboard.js`，就删掉本补丁并 `git checkout -- .` 恢复干净 checkout。
- 只改静态资源时的部署方式见 `.memory/project/codoxear-web-restart.md`（需要重启进程，
  `?v=` 资源版本号在进程内 `lru_cache`，不重启手机端会继续命中旧 bundle）。

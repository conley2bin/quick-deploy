# pi-suspend-guard

防止 Pi 的 `Ctrl+Z`（`app.suspend`）在**没有 shell job-control owner** 的进程组里把 TUI 永久卡死。

## 问题机制

Pi 的 suspend 路径是：注册一次性 `SIGCONT` 恢复处理器 → `ui.stop()`（恢复 cooked/echo 终端并移除输入监听）→ 向自身进程组发送 `SIGTSTP`。这条链假设有一个交互 shell 在用户 `fg` 时发送 `SIGCONT`。

当 Pi 运行在 `forkpty` 式的嵌套 PTY 中（它是自身 session/process-group leader，父进程属于另一个 session），该进程组是 POSIX 意义上的 **orphaned process group**，内核会丢弃默认的 stop 动作。结果是：Pi 依然存活，但 TUI 已经停止——终端把 Escape 回显为 `^[`、方向键回显为 `^[[A`，看起来“按什么都不灵”，直到人工 `kill -CONT <pi-pid>` 才可能恢复。

## 解决方式（通用，无特例）

本扩展在 Pi 的 **terminal-input 层**（先于 editor 与 `app.suspend` 动作）做防护：

1. 监听从 `pi-tui` 的公共 `getKeybindings()` 读取当前**有效的** `app.suspend` 绑定（用户改绑后依然正确）；
2. 只有输入匹配该绑定时，才读取 Linux `/proc/<pid>/stat`，按形式定义判定当前 process group 是否 orphaned（组内任一成员的父进程在同一 session 且不同 pgrp ⇒ 非 orphan）；
3. 仅在 orphan 结论完整成立时消费该输入并提示 `Suspend unavailable: this process group has no controlling job owner.`；其余情况（非 orphan、非 Linux、证据不完整）一律放行，Pi 原生 `Ctrl+Z` / `fg` 完全不变。

无轮询、无定时器、无信号转发；普通按键不读 `/proc`；每次 suspend 输入即时重判，父进程退出/重挂也能反映；不以 session、pane、PID、PTY、进程名或路径做判断。

替代方案说明：全局把 `app.suspend` 绑定置空（`keybindings.json` 里 `"app.suspend": []`）也能避免此 bug，但会连正常 shell 下 Pi 的合法 suspend 一并禁用；本扩展只屏蔽“注定无法恢复”的那一种。

## 安装

```bash
./install.sh
```

创建受管链接 `~/.pi/agent/extensions/pi-suspend-guard -> 本目录`，并向 PI home 的 git `info/exclude` 添加 `/extensions/pi-suspend-guard`。幂等；拒绝覆盖外部文件/目录/链接；首次部署时曾从 `fresh-install/modules/tmux/pi-suspend-guard` 链接的旧安装会被识别并备份迁移。

安装后在 Pi 中 `/reload`（或重启）生效。安装器不会替你 reload。

## 测试

```bash
./test/run.sh
```

覆盖：proc 解析与 orphan 形式分支、fail-open 行为、有效绑定消费与普通输入放行、懒重分类、监听器生命周期、真实 Pi 在 shell-job / forkpty 两种拓扑下的判定、真实 forkpty Pi TUI 中 Ctrl-Z 被消费且保持 raw/live 并可正常 Ctrl-D 退出、安装器契约。需要本机可执行 `pi`、`tmux` 不在依赖之列。

## 卸载

```bash
rm ~/.pi/agent/extensions/pi-suspend-guard   # 仅符号链接
```

再从 PI home 的 `.git/info/exclude` 删除 `/extensions/pi-suspend-guard` 一行，然后 `/reload`。

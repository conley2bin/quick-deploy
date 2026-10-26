# pi-suspend-guard

本扩展负责 Pi 根会话的 **error 停滞自动续跑**，并保留无 shell job-control owner 时的 suspend 按键防护。恢复不依赖 tmux，持久 TUI（regular/fullscreen）与 RPC 会话使用同一规则；子代理的失败仍由其 supervisor 处理。逻辑不按 UI mode 过滤，但一次性 JSON/print 调用若退出，会清理 timer，不会为恢复保持进程运行。

## error 停滞自动 continue

- 每次终态消息 `role:"assistant", stopReason:"error"` 记录新的错误时间。**停滞满 60 秒**，且 Pi 原生 run 已 `agent_settled`、当前 idle、没有 queued input 时，发送 `pi.sendUserMessage("continue")`。错误发生后原生重试超过 60 秒，仍须等其结束；最后一次错误重新计时。
- 只看结构字段，不匹配错误文字。并发 `permission_error`、未知错误、空/缺失 `errorMessage`、auth/quota 等全部适用。工具失败、普通文本中的 `Error:`、`stopReason:"aborted"` 不独立触发。
- **一次 error 触发最多自动发送 5 次，每次至少间隔 60 秒。续跑后再次报错会重置次数。** 每个新的终态 assistant error 都重新开始 60 秒计时和五次预算；因此连续报新错时，总发送数可以超过 5 次。同一消息的重复投递、`message_update` 或发送 `continue` 本身不续预算；没有新错误而发送被消费/失败时，才会耗尽该触发的五次上限。正常 assistant 完成（包括 `toolUse`）或切换模型结束旧触发。
- `agent_start` 取消旧 timer，原生 retry/compaction/queued follow-up 优先。语义 assistant 输出（文字、thinking、tool call）、正常完成、模型切换或显式取消使旧错误 deadline 失效。无关 tool/user 消息不解除错误资格。
- 提交输入会取消当前 timer，并给输入完整的 60 秒优先窗口；新 run 开始后不注入。输入被其他 hook 消费或一次 `sendUserMessage` 没有启动 run 时，保留一个受五次上限约束的 timer 重新检查，避免永久失去恢复机会。执行前始终重查 idle/queued input，不向忙碌模型发送。
- TUI 原始 Esc 取消恢复且不吞掉原按键；支持 legacy、Kitty 和 modifyOtherKeys 编码。Pi 1.4 的 retry-sleep Esc 可能仍以 `aborted:false` settled，idle Esc 也没有生命周期事件，所以不能只依赖 aborted settlement。普通 aborted 消息及 `agent_settled.aborted:true` 同样取消。旧版 `Aborted after N retry attempts` 尾消息只保留已有 error，不单独触发，也不能覆盖明确的取消。
- Esc 取消当前触发且不重置发送间隔；取消记录当前 run signal，该 run 的延迟 error 不能重新启动 timer。新 run 的新 error 可重新触发并获得五次预算。session shutdown/reload 释放 timer、terminal listener 和事件订阅；失效 context 的检查失败会明确报错并撤销 timer。

阈值与次数位于 `recovery.mjs`：`ERROR_STALL_MS = 60_000`、`AUTO_CONTINUE_MIN_INTERVAL_MS = 60_000`、`AUTO_CONTINUE_MAX = 5`。本模块原先只有 Ctrl-Z 防护，没有 error timer 或可沿用的阈值；60 秒/5 次是新增的用户指定策略。

### 与 tmux 状态扩展共存

`pi-suspend-guard` 是唯一的自动 `continue` 发送者。`pi-tmux-window-status` 只负责红色/呼吸状态、lease 和 pane marker，不再拥有恢复 timer。两者通过进程内 `pi.events` 的 `pi-suspend-guard:cancel`（payload `{ signal }`）同步取消：无论哪一个先消费 tmux 的私有 `\x1b[997;1~` sentinel，都同时取消 timer 和本 root 的红色 lease，且不依赖加载顺序。发送本身不清红；正常输出/完成/模型切换按状态扩展规则清红。

## suspend 按键防护

Pi 的 suspend 路径是：注册一次性 `SIGCONT` 恢复处理器 → `ui.stop()`（恢复 cooked/echo 终端并移除输入监听）→ 向自身进程组发送 `SIGTSTP`。它假设有交互 shell 在用户 `fg` 时发送 `SIGCONT`。

在 `forkpty` 式嵌套 PTY 中，Pi 可能处于 POSIX **orphaned process group**；内核丢弃默认 stop，Pi 存活但 TUI 已停止，按键回显 `^[`、`^[[A`，直到人工 `SIGCONT` 才可能恢复。

本扩展在 terminal-input 层、先于 editor 与 `app.suspend`：

1. 从 `pi-tui` 公共 `getKeybindings()` 读取**有效的** `app.suspend` 绑定，尊重用户改绑或禁用；
2. 只有匹配该绑定才读取 Linux `/proc/<pid>/stat`。组内任一成员的父进程在同一 session、不同 pgrp，即非 orphan；
3. 仅完整证明 orphan 时消费输入并提示 `Suspend unavailable: this process group has no controlling job owner.`。非 orphan、非 Linux、证据不完整均放行，原生 suspend / `fg` 不变。

这条按键防护仍无轮询、timer 或信号转发，普通按键不读 `/proc`；每次 suspend 输入即时重判，不按 session、pane、PID、PTY、进程名或路径特判。全局 `"app.suspend": []` 也可禁用 suspend，但会同时禁用正常 shell 的合法 suspend；此防护仅屏蔽无法恢复的一种。

## 安装与启用

```bash
bash pi-agent/extensions/pi-suspend-guard/install.sh   # 从仓库根目录
```

创建受管链接 `~/.pi/agent/extensions/pi-suspend-guard -> 本目录`；Pi home 为 Git worktree 时向 `info/exclude` 添加对应规则。幂等，拒绝覆盖外部路径，识别并备份迁移最早的 `fresh-install/modules/tmux/pi-suspend-guard` 旧链接。`pi-agent/install.sh` 安装所有扩展；tmux 模块安装器也会同时安装此恢复扩展与状态扩展。

更新后在 Pi **空闲时** `/reload` 或重启生效。安装器不替你 reload；tmux 配置重载不会重载 Pi。既有会话只恢复后续观察到的错误，不追溯 session 历史自动重试。

## 测试

```bash
bash pi-agent/extensions/pi-suspend-guard/test/run.sh
node --experimental-strip-types --test pi-agent/extensions/pi-tmux-window-status/test/status.test.mjs
```

虚拟时钟覆盖真实 concurrent `permission_error`、任意/空错误、59,999/60,000ms 边界、无 tmux 的 TUI/RPC/JSON/print、原生 busy/settled、queued input、正常进展、取消/延迟事件、五次上限、输入/发送未启动 run 和 shutdown。tmux suite 覆盖唯一发送者、两种 listener 顺序和真实 copy-mode 搜索关闭取消。保留 proc/classifier、有效绑定、listener 生命周期、真实 shell-job/forkpty Pi、Ctrl-Z raw/live 与安装器测试；拓扑测试使用隔离 Pi home，不受个人 keybindings 影响。需要本机 `pi`；suspend-guard suite 不依赖 tmux。

## 卸载

```bash
rm ~/.pi/agent/extensions/pi-suspend-guard   # 仅符号链接
```

Git Pi home 再删除 `info/exclude` 中的 `/extensions/pi-suspend-guard`，然后 `/reload`。卸载会停止自动恢复；tmux 状态扩展本身不再发送 `continue`。

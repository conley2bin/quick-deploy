# tmux 安装模块

本模块为 Ubuntu 24.04+ 主机安装 tmux 和 [Oh my tmux!](https://github.com/gpakosz/.tmux)（gpakosz/.tmux）配置：

```bash
./install.sh
```

## 安装内容

1. `apt install tmux git xclip wl-clipboard` —— 后两者是 gpakosz 配置“复制到系统剪贴板”功能在 Linux 下的依赖：X11 会话用 `xclip`，Wayland 会话用 `wl-copy`，两个都装以覆盖两种会话。
2. 克隆 `https://github.com/gpakosz/.tmux` 到 `~/.tmux`（`--single-branch`）。
3. 链接本仓库的 `tmux.conf.local` 和 Pi→tmux breathing status 扩展。

## 脚本创建的链接

gpakosz/.tmux 的两个固定查找路径都以符号链接落盘，目标一个指向上游克隆、一个指向本仓库：

| 路径 | 指向 | 作用 |
| --- | --- | --- |
| `~/.tmux.conf` | `~/.tmux/.tmux.conf` | 主配置入口（tmux 固定读取位置）；随重跑时的 `git pull` 自动更新 |
| `~/.tmux.conf.local` | `fresh-install/modules/tmux/tmux.conf.local`（安装时本仓库的绝对路径） | 定制入口；改动即仓库改动 |
| `~/.pi/agent/extensions/pi-tmux-window-status` | `pi-agent/extensions/pi-tmux-window-status`（安装时本仓库的绝对路径） | Pi 生命周期到 tmux breathing status 的受管扩展 |

历史命名：该扩展由 `quick-deploy-tmux-status` 更名而来。installer 仍识别旧名 `~/.pi/agent/extensions/quick-deploy-tmux-status` 的已知受管链接：视为 legacy，备份为 `*.bak.<时间戳>` 后创建新链接；未知旧路径（外部链接、普通文件、目录）一律不改动并失败。tmux 窗口选项 `@quick_deploy_pi_*` 与运行时私有目录 `quick-deploy/pi-tmux-status` 有意保持不变，避免已运行 Pi 进程产生重复运行时状态。

约束与修复：

- 替换任何既有文件前一律先改名为 `*.bak.<时间戳>`，从不删除。
- `~/.tmux.conf.local` 记录的是仓库的绝对路径：若整个仓库搬走，链接仍会悬空；到新位置重跑一次 install.sh 即可重新链接。
- 少数编辑器写文件时会把符号链接替换成普通文件，重跑 install.sh 同样自动备份并重建链接。

## 定制入口

上游明确要求**不要改主配置** `~/.tmux/.tmux.conf`（改了 `git pull` 会冲突），一切定制写在 `.tmux.conf.local` 里。本模块把这个入口符号链接到仓库文件（见上节链接表），因此：

- 单一事实源：改 `~/.tmux.conf.local` 就是改仓库文件（`<前缀> e` 打开的也是它），改完 `<前缀> r` 生效、`git commit` 入库。
- 多机同步只拉不装：别的机器 `git pull` 本仓库即生效，无需重跑 install.sh。
- 基线只记真实改动（目前是鼠标模式、精简状态栏、左侧 session 与右侧时间同样式、未选中 window 使用灰色块、取消 `Ctrl+a` 第二前缀、`Ctrl+Alt+←/→` 切换 window、`Ctrl+Alt+=/+` 新建 window）；全部可用选项查上游模板 `~/.tmux/.tmux.conf.local`。该文件本质是 tmux 配置片段，可直接写 `set -g ...`；若某行被主配置覆盖，按上游说明在行尾加 `#!important`。

## 幂等语义

重跑本脚本：

- apt 包已装则跳过；
- `~/.tmux` 已是克隆则用 `git pull --ff-only` 更新；更新失败（离线、本地有改动）只警告不中止，保留现有版本；
- `~/.tmux.conf.local` 已是指向模块基线的符号链接则跳过；若它被换成普通文件（少数编辑器写文件时会替换符号链接）或指向别处，先备份为 `*.bak.<时间戳>` 再重新链接——重跑即修复；
- `install-pi-tmux-window-status.sh` 可独立运行，且只管理唯一的 Pi 扩展链接：精确新目标跳过；新目标位置已知受管旧链接（仓库搬迁遗留）先备份再修复；旧名 `quick-deploy-tmux-status` 的已知受管链接视为 legacy 迁移；未知文件、目录或外部链接（新旧任一侧）直接失败且不改动；新旧都存在时只有两者都是已知受管链接才处理。它接受 `QUICK_DEPLOY_PI_TMUX_WINDOW_STATUS_SOURCE`、`QUICK_DEPLOY_PI_HOME`、`QUICK_DEPLOY_PI_TMUX_WINDOW_STATUS_TARGET`、`QUICK_DEPLOY_PI_TMUX_WINDOW_STATUS_LEGACY_TARGET` 做隔离测试。
- 替换既有 `~/.tmux.conf` 或非仓库的 `~/.tmux` 目录前同样先备份。

在 `setup.sh` 中本步骤为 tolerate：tmux 本体走 apt 很可靠，但配置仓库要从 GitHub 克隆，全新机器还没配代理时可能失败——只提示不中止，网络就绪后重跑本脚本即可。

## Pi 扩展自动发现

Pi 只在启动时扫描 `~/.pi/agent/extensions/` 下的目录，不会扫描本仓库——仓库里的 `pi-agent/extensions/pi-tmux-window-status` 必须通过上面的受管符号链接暴露到 `~/.pi/agent/extensions/` 才会被加载。安装/更新扩展后需要**重启 Pi 或执行 `/reload`** 才生效；tmux 只须 `<前缀> r` 重载样式。

## 使用要点

- 前缀键仅保留默认 `Ctrl+b`；Oh my tmux! 默认新增的第二前缀 `Ctrl+a` 已取消。
- `<前缀> e` 打开 `.tmux.conf.local`，`<前缀> r` 重载配置。
- 状态栏左侧只显示 session 名，并与右侧时间使用完全相同的浅灰字、深灰底样式；右侧移除电池信息。所有未选中的 window 空闲时为 `#bcbcbc` 灰白块、深色字；bell 状态保留黄色前景和 `!` 标记。last/activity 不改变背景或额外强调。选中不再用蓝色背景块，而是在色块左右末端各画一个整格实心的 `#0077aa` 蓝色竖条（`█`，fg/bg 同设蓝，字体即使留缝隙也不漏底色）。选中与 error 走正交视觉通道：竖条 vs 背景，error 红底不再吞掉选中标识；两侧蓝色竖条永不呼吸、永不变色。Pi 根进程主回合或其 pi-subagents 0.56 异步子代理实际运行时，相应 window 以 24 帧、42ms/帧、约 24 FPS、1s 周期呼吸；选中与未选中呼吸同一条灰色路径 `#808080 ↔ #f5f5f5`（呼吸只发生在背景色块上），从空闲基线开始并用单调时间跳帧/回绕。等待用户或 `needs_attention` 是空闲。Pi 0.84.3 根助手出现模型/供应商不可用错误（配额/余额、认证、模型不存在、超时、传输、限流/过载、无部署、5xx 等）时优先显示稳定红底白字；abort、上下文溢出、policy/refusal、普通 400/schema 和 tool-result 错误不会触发。红色状态优先级高于呼吸，纯红窗口只用约 1s 慢速 reconciliation，不跑 42ms 动画；后续语义输出、成功 assistant 结束或切换模型会清除。模型错误且 agent 空闲时，扩展会在 250ms 防抖后自动发送 `continue`（用户输入或新一轮启动会取消），30s 至少间隔一次、单轮失败最多 10 次，正常对话后计数清零；达到上限后保持红色不再自动发送。异步 ownership 只在根 Pi 的私有 lease 中瞬态保存 parent session ID 与活动 run/node ID；错误 lease 只存 `state=error`，不记录原始 provider 文本、历史、prompt、cwd 或名称。深色边格仍分隔相邻标签。安装后需 **重启 Pi 或执行 `/reload`** 让扩展加载；tmux 只须 `<前缀> r` 重载样式。
- `<前缀> m` 切换鼠标模式；普通 pane 中鼠标滚轮每格滚动 1 行；`<前缀> -` / `<前缀> _` 分屏；`<前缀> h/j/k/l` 在窗格间移动。应用主动开启 mouse reporting 时，滚轮仍交给应用自身处理。
- `Ctrl+Alt+←/→` **不需要前缀**，直接切换上一个/下一个 window（底部状态栏的标签）。
  绑定落在 root 表：`C-M-Left=previous-window`、`C-M-Right=next-window`。
  Ghostty 模块显式 unbind 这两个键，确保按键进入 pty；gpakosz 检测到
  `TERM_PROGRAM=ghostty` 后自动开启 extended-keys，tmux 才能识别组合键。
- `Ctrl+Alt+=` / `Ctrl+Alt++` **不需要前缀**，提示输入名称后在当前 pane 的目录新建 window；直接回车则让 tmux 按运行程序自动命名。Ghostty 模块为两者显式发送 CSI-u 序列，tmux 分别绑定 `C-M-=` / `C-M-+`，避免符号键修饰信息在终端编码中丢失。
- 内置 TPM 插件支持：在 `.tmux.conf.local` 里写 `set -g @plugin ...`，`<前缀> I` 安装，`<前缀> u` 更新，`<前缀> M-u` 卸载。
- 完整键位与状态栏变量见上游模板 `~/.tmux/.tmux.conf.local` 和上游 README。

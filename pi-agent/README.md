# pi-agent

本目录是 quick-deploy 仓库中 Pi 侧资源（extensions / skills）的源码归属地。

## 一键安装

```bash
./install.sh            # 安装所有带 install.sh 的扩展
./install.sh --skills   # 之后再交互式安装 skills（菜单选择 + API key 提示）
```

行为：

- 自动发现 `extensions/*/install.sh`，逐个执行；新增带 `install.sh` 的扩展即自动纳入，无需改本脚本。
- 各扩展安装器都是幂等的：已安装的跳过；旧 checkout 或旧名字的受管链接先备份再迁移；外部文件/目录/链接一律拒绝覆盖。
- 默认不碰 skills（其安装器是交互式的）；不加 `--skills` 时只打印提示。
- 任何一步失败立即中止；脚本不会 reload 或重启 Pi，完成后按提示在 Pi 里执行 `/reload`。

## 布局约定

| 路径 | 内容 | 部署方式 |
| --- | --- | --- |
| `extensions/<name>/` | 扩展源码（`index.ts` 入口） | 各自的 `install.sh` 创建 `~/.pi/agent/extensions/<name>` 受管符号链接；PI home 是 git worktree 时同时写入 `info/exclude` |
| `skills/` | 技能源码（`SKILL.md`） | `skills/install-skills.sh` 交互式复制到 `~/.pi/agent/skills/` |

`pi-copy-links` 提供代码块右下角复制按钮和 Ctrl+左键打开网页；安装后需在 Pi 的 `/settings` 中开启 `fullscreen`。它不修改全局设置，当前适配 Pi 0.85.1，详见 [使用说明](extensions/pi-copy-links/README.md)。

扩展测试在各自 `test/` 下，例如 `extensions/pi-suspend-guard/test/run.sh`、`extensions/pi-tmux-window-status/test/installer.sh`；`pi-copy-links` 使用 `npm run check`。

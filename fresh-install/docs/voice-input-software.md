# 现成 Linux 语音输入软件

**优先试用 Nextalk v0.2.18。**它有现成 Ubuntu/Debian 安装包，本机录音、本机中文识别，并通过随包 Fcitx5 插件将文字输入到当前应用。无需先搭建 Qwen 服务、自己编译语音插件或维护录音脚本。

这是截至 2026-10-04 的产品推荐，尚未在 Y9000p 安装或测试；仓库 `setup.sh` 当前不会安装它。

## 为什么它最贴合当前需求

- 免费开源，面向 Linux；[0.2.18 发布页](https://github.com/gonewx/nextalk/releases/tag/v0.2.18)提供 `nextalk_0.2.18-1_amd64.deb`，约 26.2 MB。
- 默认本地 SenseVoice 支持中文、英语等语言，首次启动自动下载约 200 MB 模型；不需要云账户。
- 系统快捷键调用 `nextalk-toggle`：按一次开始，再按一次结束并将文字上屏；录音中 Esc 可取消。
- 正常 Fcitx5 路径直接提交 Unicode 文字，不需要客户端/公司机的剪贴板配对。本地 Pi 与 SSH Pi 沿用同一终端输入通道。
- Fcitx5 路径声明支持 X11 与 Wayland。README 中 X11/XWayland 手动粘贴回退指的是**未使用 Fcitx5 时的 GNOME Shell 扩展路线**，不要把它套到正常 Fcitx5 用户。

### 停顿、上屏与发送

v0.2.18 README 仍有“停顿后自动提交”的表述；该 tag 的[实际装配代码](https://github.com/gonewx/nextalk/blob/v0.2.18/voice_capsule/lib/main.dart)设置 `autoStopOnEndpoint: false`、`autoReset: false`，采用跨停顿累积、手动结束的流程。核查的停止逻辑由第二次切换触发，而非静音端点提交。

[插件](https://github.com/gonewx/nextalk/blob/v0.2.18/addons/fcitx5/src/nextalk.cpp)调用 `commitString(text)` 上屏，未合成 Return/Enter。正常流程是文字进入草稿，再由用户自行按回车。实际 Pi 草稿保留和手动发送行为仍须在目标终端试用确认。

## 在客户端试用的最短步骤

以下只在**面前有麦克风的本机桌面**执行，不是在 SSH 公司机 shell 中安装。

1. 从上述发布页取得 amd64 `.deb`，用 APT 安装，由包管理器解析依赖。安装包会重启 Fcitx5 加载插件，应选择没有正在编辑输入法文本的时刻。仓库源码不需要改变。
2. 启动 Nextalk，等待首个模型下载，选择默认 SenseVoice 和实际麦克风。
3. Ubuntu 24.04 GNOME 需配置一次系统快捷键：**设置 → 键盘 → 自定义快捷键**，命令 `nextalk-toggle`；选择未占用的键，文档示例 `Super+Z`，也可试未占用的 F8。较新 GNOME/KDE 的 Portal 可自动注册，但不能据此承诺24.04免配置。
4. 为工程指令关闭额外自动纠错。在已有 `~/.config/nextalk/settings.yaml` 的 `text` 节改为：

   ```yaml
   text:
     auto_correct: false
   ```

   合并修改这一项，不覆盖整份设置或重复添加 `text` 节；之后重启应用。它关闭自动大小写、口头禅、重复英文词和标点纠错，避免额外改动指令原文。SenseVoice 自身数字/日期规整 `use_itn` 是另一项设置，可按体验决定，不必一开始关闭。
5. 聚焦 Pi 输入框并在原草稿中间放置光标，按键、说一句中文夹工程词、再按键。检查前后草稿保留、停顿期间没有提前完成、文字出现后没有发起新 turn；最后自己回车。

首次试用只需证明热键、音频、识别和上屏这条完整链路。若显示“已复制到剪贴板”，说明没有走预期 Fcitx5 直上屏路线，应先修复输入法连接，不能把手动粘贴回退称为已满足自动填字。

## 其他现成候选

| 软件 | 适合什么 | 当前不优先的原因 |
| --- | --- | --- |
| [Handy v0.9.8](https://github.com/cjpais/Handy/releases/tag/v0.9.8) | 跨平台独立应用、明确的 Toggle-only、本地模型管理；已支持 Qwen3、SenseVoice、Whisper。 | GNOME Wayland 的粘贴仍需合适输入工具与配置，不能使用不被 Mutter 支持的 wtype；当前 Ghostty 键位还涉及剪贴板读取路由。可作为 Nextalk 不兼容时的备选，不宣称无法使用。 |
| [Speech Note](https://github.com/mkiol/dsnote) | Linux 离线转录、笔记、朗读等组合功能；有 Flatpak 与中文模型。 | Wayland 活动窗口输出需 ydotool daemon/socket权限，全局快捷键有Portal版本条件；同键开关的具体配置尚未得到足够文档确认。仅为当前简单听写需求，配置不比Nextalk更短。 |

Nextalk 默认使用 SenseVoice，而不是前一阶段提到的 Qwen。先用默认模型完成热键、识别和上屏试用，再根据实际纠错量决定是否需要其他模型或应用。

## 与之前集成方案的关系

[原集成方案](voice-input-plan.md)是自行包装 Fcitx5-vinput、模型和文本保护的备选设计，不是使用 Nextalk 的前置要求。当前按用户“现成软件优先”的方向，先验证 Nextalk；符合体验后，再决定是否把其安装和少量配置包装为本仓库模块。

证据：Nextalk [tag README](https://github.com/gonewx/nextalk/blob/v0.2.18/README_zh.md)、[发布资产](https://github.com/gonewx/nextalk/releases/expanded_assets/v0.2.18)、[默认配置](https://github.com/gonewx/nextalk/blob/v0.2.18/voice_capsule/lib/constants/settings_constants.dart)、[纠错开关实现](https://github.com/gonewx/nextalk/blob/v0.2.18/voice_capsule/lib/services/text_post_processor.dart)。

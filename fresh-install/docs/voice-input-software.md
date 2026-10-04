# 现成 Linux 语音输入软件

**Nextalk v0.2.18 可供此前“按一次开始、再按一次结束填字”的流程试用。**它有现成 Ubuntu/Debian 安装包，本机录音、本机中文识别，并通过随包 Fcitx5 插件输入文字。

**新增的“独立 Ctrl 长按3秒后才录音，松开即停，边说边写入 Pi”不能直接用 Nextalk0.2.18 或 Handy0.9.8 的原生设置完整实现。** 下列试用步骤仍描述旧的 Toggle 流程；新交互的能力边界见下节。

本页依据截至 2026-10-04 的固定版本源码与文档，尚未在 Y9000p 安装或测试；仓库 `setup.sh` 当前不会安装语音软件。

## 长按与实时输入的能力边界

| 要求 | Nextalk0.2.18 | Handy0.9.8 |
| --- | --- | --- |
| 按住 Ctrl 满3秒才开录 | 系统命令/Portal 激活只触发 toggle，没有按键释放与延时门限。 | 有普通按住说话，按下即开录；混合模式的300ms阈值用于松开后判定停/锁定，不是延迟开录。没有原生3秒开录选项。 |
| 松开停止 | 当前热键流程是第二次 toggle 停止。 | 普通PTT可在真实keyup停止；GNOME Wayland用命令绑定的toggle不等于此路径，独立Ctrl配置未得到确认。 |
| 文字实时进入Pi输入框 | Zipformer partial更新胶囊浮窗，停止后才提交整段文字。 | 可有Live overlay，结束后finalize/paste；实时浮窗不是实时输入目标编辑器。 |

Nextalk [控制器](https://github.com/gonewx/nextalk/blob/v0.2.18/voice_capsule/lib/services/hotkey_controller.dart)中，`_onRecognitionResult` 只更新预览，`_onEndpoint` 在PTT模式不提交，`_stopAndSubmit` 等待识别收尾后才发送Fcitx。Handy的[按键状态机](https://github.com/cjpais/Handy/blob/v0.9.8/src-tauri/src/transcription_coordinator.rs)和[录音/粘贴动作](https://github.com/cjpais/Handy/blob/v0.9.8/src-tauri/src/actions.rs)也没有边说边投递目标输入框的通路。

新交互需要本地适配三处机制：按下/抬起事件与3秒计时（正常Ctrl组合键不误触）；稳定识别片段持续进入终端（不重复追加会修订的partial）；录音界面不接管Pi焦点。Nextalk当前最终上屏前会隐藏浮窗、等待焦点恢复，不能仅接一条partial回调就宣称实现实时输入。松手可停止采集，最后一小段识别仍可能稍后完成。全部仍属客户端工作，不需要判断Pi本地/SSH或另造ASR模型。

## 为什么它适合此前的 Toggle 需求

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

## 专业术语与自动纠错

Nextalk这项[后处理](https://github.com/gonewx/nextalk/blob/v0.2.18/voice_capsule/lib/services/text_post_processor.dart)是本地规则，默认替换词典为空，没有LLM语义翻译步骤。它不会自行把某技术概念解释成日常同义词，但规则仍可能改坏格式：例如没有小写英文的整段 `HTTP API` 会变成 `http api`，连续英文重复词会被合并。

识别器本身先将声音转成文字，稀有术语仍可能在这一步被听成常见词；关闭后处理不能修复这个问题。

对工程指令，先使用上面的 `text.auto_correct: false`，它也关闭用户替换词典。需要特定术语纠正时，保留总开关 `true`，关闭 `normalize_case`、`remove_fillers`、`remove_repeated_words`、`normalize_punctuation`，仅添加自己审阅的 `replacements`。英文替换按字母/数字边界忽略大小写，中文按字面替换；避免过短词条，例如单字“派”映射Pi可能误改“派遣”。它不具备词典项的语义消歧能力。

整段规则也会令流式前缀不稳定：早期全大写结果可能被小写化，之后新增一个小写单词就会让该规则不再适用。真正实时上屏时应关闭这类后处理，或只对稳定片段应用受控转换。

## 其他现成候选

| 软件 | 适合什么 | 当前不优先的原因 |
| --- | --- | --- |
| [Handy v0.9.8](https://github.com/cjpais/Handy/releases/tag/v0.9.8) | 跨平台独立应用、明确的 Toggle-only、本地模型管理；已支持 Qwen3、SenseVoice、Whisper。 | GNOME Wayland 的粘贴仍需合适输入工具与配置，不能使用不被 Mutter 支持的 wtype；当前 Ghostty 键位还涉及剪贴板读取路由。可作为 Nextalk 不兼容时的备选，不宣称无法使用。 |
| [Speech Note](https://github.com/mkiol/dsnote) | Linux 离线转录、笔记、朗读等组合功能；有 Flatpak 与中文模型。 | Wayland 活动窗口输出需 ydotool daemon/socket权限，全局快捷键有Portal版本条件；同键开关的具体配置尚未得到足够文档确认。仅为当前简单听写需求，配置不比Nextalk更短。 |

Nextalk 默认使用 SenseVoice，而不是前一阶段提到的 Qwen。先用默认模型完成热键、识别和上屏试用，再根据实际纠错量决定是否需要其他模型或应用。

## 与之前集成方案的关系

[原集成方案](voice-input-plan.md)是此前 Toggle 需求下的备选设计，不是使用 Nextalk 的前置要求。当前新增的延时长按与真实流式投递，需要另做客户端控制和上屏适配；不能把旧应用安装成功或浮窗实时字幕当成该目标已经完成。现有音频/识别引擎和 Fcitx5 通道可以复用，不需要从零重写模型或修改远端 Pi。

证据：Nextalk [tag README](https://github.com/gonewx/nextalk/blob/v0.2.18/README_zh.md)、[发布资产](https://github.com/gonewx/nextalk/releases/expanded_assets/v0.2.18)、[默认配置](https://github.com/gonewx/nextalk/blob/v0.2.18/voice_capsule/lib/constants/settings_constants.dart)、[纠错开关实现](https://github.com/gonewx/nextalk/blob/v0.2.18/voice_capsule/lib/services/text_post_processor.dart)。

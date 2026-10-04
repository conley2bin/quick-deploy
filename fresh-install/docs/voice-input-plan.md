# 客户端语音输入方案

本文是待实施的方案，当前仓库尚无语音安装模块。目标是在面前的电脑短按一个键开始说话，再按同键结束；识别文字进入 Pi 输入框，由用户自行按回车发送。

## 推荐

在仓库已有的 Ubuntu GNOME + Fcitx5 桌面基础上，采用 **fcitx5-vinput v2.4.0 + 本地 Qwen3-ASR 0.6B + 窄的终端文本过滤补丁**。将其包装为独立的客户端模块 `fresh-install/modules/voice-input/`，在 `fresh-install/setup.sh` 显式接入。语音模块依赖已有 `chinese-input-method`，不属于 `pi-agent` 的远端扩展安装。

上游现成包没有终端专用的控制字符过滤开关；首版需要维护、测试这一小处补丁，并向上游反馈。这个维护成本是推荐的一部分，不将“直接安装现成包”当作已经满足所有合同。

```text
面前电脑上的快捷键
  → 本机麦克风
  → 本机语音识别
  → Fcitx5 提交 Unicode 文字
  → 本地终端的输入通道
       ├─ 本机 Pi 输入框
       └─ SSH → 远端 Pi 输入框
  → 用户检查后自行回车
```

**不判断 Pi 是否经 SSH，不发送音频到远端，不安装 Pi 语音扩展，不配置主机名、SSH 别名或语音回连端口。** 客户端已经生成了文字，SSH 传递文字的方式与键盘输入相同。

### 为什么选择输入法路线

Fcitx5 已是仓库的桌面基础。vinput 的 Tap 模式提供同键开关；录音、模型管理及识别无需自建。其识别结果用 `commitString` 提交给本地终端输入上下文，从而避开剪贴板和键盘注入工具。

这与当前 Ghostty 的 Ctrl+Shift+V 不同：后者被仓库改为原始 Ctrl+V，触发 Pi 读取剪贴板。SSH 中读取客户端剪贴板需要现有桥的客户端归属；仅安装本地听写工具并不能建立这项归属。因此它不能作为“换任意客户端，只装本地模块即可使用”的默认机制。

## 候选取舍

| 方案 | 判断 |
| --- | --- |
| **Fcitx5-vinput** | 当前 Ubuntu 桌面首选。复用已有输入法，Unicode 直接进入终端，不依赖远端剪贴板配置。须验证实际 Ghostty/输入法兼容性。 |
| **Handy v0.9.8** | 跨平台备选，录音、取消、模型管理成熟，支持本地 Qwen3-ASR、SenseVoice 与 Whisper。Linux 落字仍需粘贴或模拟键盘；在本项目中要额外处理剪贴板、Wayland 权限及终端键位。 |
| **自建 SenseVoice 客户端** | 不作为首选。Handy 和输入法插件已有本地中文引擎；重写录音与状态机不能单独解决文字如何进入终端的问题。 |
| **Speech Note** | 更适合离线转录、笔记和朗读等组合需求；当前任务只需同键听写，额外功能和 Wayland 输出依赖没有优势。 |
| **Pi 端录音扩展** | 不采用。远端 Pi 会录到远端麦克风，必须另加客户端回连；普通终端输入本来就能传递文字。 |

[Handy v0.9.8 的模型目录](https://github.com/cjpais/Handy/blob/v0.9.8/src-tauri/src/catalog/catalog.json)同时包含 Qwen3-ASR 与 SenseVoice。选择中文模型不要求自建前端。应用前端和 ASR 模型应分开决策。

## 使用合同

- **开始/结束**：候选默认键 F8，允许安装时改为其他单键。设为 Tap，而非长按模式；检查与现有按键冲突，不抢占自定义绑定。
- **状态**：呈现开始、录音、识别与失败提示。保留默认的停止后暂停采集、闲置 15 秒销毁流行为；模型可驻留内存，非录音阶段不继续收集用户语音。立即销毁音频流不作为默认，它会让连续听写每次都冷启动，增加已知开头漏字问题的机会。闲置后的首次录音仍需验证开头完整性；系统麦克风指示与实际设备状态也须实测。
- **文字**：在现有光标处输入，保留原草稿；不触发全选、替换、回车或 Pi 消息提交。采用原始听写模式，关闭 LLM 改写、翻译、命令功能。
- **控制字符**：终端直接输入不是 bracketed paste。上游原始链路只去掉首尾空白，内部控制字符可保留到 `commitString`。计划在两个语音结果提交点使用同一个 UTF-8 文本过滤函数：拒绝无效 UTF-8；将 C0/C1 控制字符及 U+2028/U+2029 转为空格，整理空白；空结果不提交。首版听写段落折叠为空格，不将口述“回车”解释成按键。过滤只作用于语音结果，不改变普通拼音或键盘输入。必须用含 CR/LF/ESC/NUL 的确定性结果测试验证，不依赖模型“通常不输出这些字符”。
- **取消**：默认保留右 Ctrl 为录音取消键，即使命令功能关闭也可取消；空闲时该键只产生“命令已禁用”提示，不开始录音。安装时允许改成不冲突的组合键，并确认不会影响用户常用右 Ctrl 组合。不要清空命令键配置，也不将其设为裸 Escape；Escape 留给 Pi。取消与失败都不得上屏。
- **焦点**：开始前聚焦 Pi，在识别完成前保持目标输入框与光标不变。插件绑定的是本地终端输入上下文，不是远端 Pi 会话 ID；切换同一终端内的 tmux pane 仍会改变文字目的地。
- **剪贴板**：语音路线不读写 CLIPBOARD/PRIMARY，现有图片粘贴和复制功能保持不变。
- **设备/模型错误**：明确提示并结束本次操作，不自动改用云识别、远端麦克风或剪贴板备用路径。

### 只保留语音所需功能

固定版本与本地 ASR，使用原始听写场景。所有场景的 `context_lines` 均设为 0，关闭上下文历史记录、远程输入和网络模型服务；不配置云端或 LLM 凭据。

v2.4.0 的源码在最大 `context_lines <= 0` 时停止记录普通输入和转写历史。这是版本相关行为，不能只在一个场景关闭后假定其他场景也停止记录。

## 模型选择

**保留 Qwen3-ASR 0.6B INT8 为首选试用候选，本地模型包约 867 MB。** [作者评估](https://github.com/QwenLM/Qwen3-ASR#evaluation)包含会议与普通话对话，中文口语能力有公开依据；尚无本人的自然改口、Pi/SSH/tmux 等工程词听写证据。作者的 bfloat16/vLLM 结果不能直接当作此处 INT8 运行效果。

[sherpa-onnx 上游](https://k2-fsa.github.io/sherpa/onnx/qwen3-asr/pretrained.html)提供该模型的 CPU INT8 路线。当前 vinput 使用缓存录音、停止后推理的方式；这符合按键结束后填字的需求，但不是边说边上屏。2026-10-04 读取的模型注册表默认为 CPU、`num_threads=1`，不能据包可运行就承诺客户端响应速度，也不能据电脑有 GPU 就假定这条路线用了 GPU。实施时明确选择运行配置并测冷/热停止到上屏延迟。

**术语提示需要先接通，不能许诺安装后即生效。** [v2.4.0 后端](https://github.com/xifan2333/fcitx5-vinput/blob/v2.4.0/src/daemon/asr/backends/sherpa_offline_backend.cpp)已有 `qwen3_asr.hotwords` 接口，传的是提示词而不是强制词替换；但2026-10-04读取的[注册表](https://github.com/xifan2333/vinput-registry/blob/main/registry/models.json)将这个0.6B INT8条目标为 `supports_hotwords=false`，默认也没有提示词配置。实施时须确认模型元数据、配置入口和实际识别器一致后，再测试短工程词表。不为提示术语开启普通输入历史记录或 LLM 改写。

选择方式：重用约20句本人录音，包含自然口述、受控读句、中英术语、数字、停顿与改口；比较0.6B无提示词与接通后的短词表，并加入词表中未实际说出的词检查误补。若速度不足，以SenseVoice作轻量对照；提示词接通后仍明显识别错误，再核对1.7B本地包作精度对照。以最终修改时间、关键实体、漏字/添加和延迟选择，不根据厂商榜单直接保证商业语音输入体验。

## 安装与平台范围

“任意电脑”首先指任意符合支持条件的物理客户端，不是固定 Y9000p。模块不得包含机器名、IP、SSH Host、固定 UID 或音频设备数字编号。

当前已查明的直接部署基线是 **Ubuntu 24.04 / GNOME / amd64**，分别验证原生 Wayland 和 X11。vinput 的预构建包针对 Ubuntu 24.04；其他 Ubuntu 版本须确认依赖及输入法兼容，必要时构建匹配包。不能仅据基础安装器标注“24.04+”就宣称语音插件也已支持全部后续版本。macOS/Windows 需要对应平台模块，可采用 Handy；不在这份 Linux 安装实现中隐式覆盖。

计划安装职责：

1. 预检发行版、架构、图形会话、Fcitx5 与快捷键冲突。
2. 从固定 v2.4.0 源码应用上述可审阅补丁，构建匹配的插件包，安装依赖并校验来源及散列。不把未含补丁的上游包标记为已满足 no-submit 合同。补丁纳入本模块回归测试，升级时复核两个提交点。
3. 下载一次所选本地模型，固定 revision/散列，支持已有离线模型缓存。
4. 原子合并本模块拥有的配置，备份受管历史版本，保留用户其他输入法设置。
5. 配置图形会话内的用户服务及错误提示。需要 Fcitx5 重启时提示用户在空闲时执行，不在输入中途强制重启。
6. 在固定 `setup.sh` 步骤清单中接入，并支持单独安装、状态检查和卸载。

卸载仅停止本模块服务、移除其创建的配置和快捷键；模型是否删除由用户选择。不卸载共享 Fcitx5，不修改 SSH、Ghostty、tmux、Pi 或剪贴板桥。

## 验收顺序

**先证明文字能正确进入终端，再测试语音模型。**

1. 用现有 Fcitx5 拼音在 Ghostty 内的本机 Pi 与 SSH Pi 输入中文，证明输入法传输路径。历史上 GNOME Wayland/Ghostty 出现过中文输入问题；失败须定位，不能把 XWayland 成功当成原生 Wayland成功。
2. 确定性文字与控制字符测试：已有草稿中间插入长中文、工程词、数字及标点；两侧草稿保持不变。CR/LF/ESC/NUL 输入不能导致提交。连续输入与较长 commit 需覆盖 Ghostty 的缓冲边界。
3. 用户亲自按开始、说话、按结束；本机 Pi、SSH Pi、SSH+tmux 使用完全相同操作。等待后仍无新 agent turn，只有主动按回车才发送。
4. 检查取消、空音频、设备拔除、模型失败和窗口关闭；均不得误投文字或留下录音。
5. 检查识别时切窗口、切 tab/pane 与光标移动，确认并说明实际目标上下文边界。
6. 比较前后剪贴板内容，确认语音没有修改它；确认关闭上下文记录后不保存普通键盘输入。
7. 用本人纯中文、中英混说、工程专有词、数字/路径与长停顿样本比较模型，并测冷/热启动体验。

## 依据与边界

- [fcitx5-vinput v2.4.0 发布（2026-09-22）](https://github.com/xifan2333/fcitx5-vinput/releases/tag/v2.4.0)
- [Tap 与按键处理](https://github.com/xifan2333/fcitx5-vinput/blob/v2.4.0/src/addon/input/vinput_keyevent.cpp)
- [识别结果 commitString 上屏](https://github.com/xifan2333/fcitx5-vinput/blob/v2.4.0/src/addon/dbus/vinput_dbus.cpp)
- [上下文记录关闭条件](https://github.com/xifan2333/fcitx5-vinput/blob/v2.4.0/src/addon/core/vinput.cpp)
- [原始识别结果编解码](https://github.com/xifan2333/fcitx5-vinput/blob/v2.4.0/src/common/asr/recognition_result.cpp)及[后处理](https://github.com/xifan2333/fcitx5-vinput/blob/v2.4.0/src/daemon/postprocess/post_processor.cpp)：去掉首尾空白不能代替终端控制字符过滤。
- [音频采集与停止](https://github.com/xifan2333/fcitx5-vinput/blob/v2.4.0/src/daemon/audio/audio_capture.cpp)：停止录音后关闭采样缓冲写入并暂停流；流存续与实际录音应分开判断。
- [Ghostty v1.3.1 GTK 输入上下文](https://github.com/ghostty-org/ghostty/blob/v1.3.1/src/apprt/gtk/class/surface.zig)
- [Handy v0.9.8 粘贴实现](https://github.com/cjpais/Handy/blob/v0.9.8/src-tauri/src/clipboard.rs)：标准路径先恢复旧剪贴板，CopyToClipboard 再写回转写，最终状态不能证明异步读取期间没有旧值窗口。

以上是源码和网络证据支持的方案；尚无 Y9000p 录音、热键或实际上屏验收。批准实施后，按上述顺序验证，不把包安装成功当作功能成功。

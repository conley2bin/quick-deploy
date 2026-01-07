# Claude Code 系统

新设备快速配置的个人 Claude Code 配置集合。

综合工具包：官方 Claude Code CLI 安装 + 系统级开发准则 + 自动化工具集成。

## 功能特性

- **两步设置**：官方 CLI 安装 + 个人配置
- **系统级 CLAUDE.md**：标准化开发原则
- **工具集成**：Claude Code 生态系统安装
- **交互式设置**：安装过程中选择工具
- **备份保护**：可选配置备份
- **快速部署**：一键安装

## 快速开始

### 步骤1：安装 Claude Code（官方 CLI）

```bash
cd claude-code
./install.sh
```

**脚本功能：**
- 自动安装 Homebrew（如果未安装）
- 通过 Homebrew 安装官方 Claude Code CLI
- 使用 `claude doctor` 验证安装
- 显示使用指南和常用命令

### 步骤2：配置增强功能（可选）

```bash
./setup.sh
```

**安装内容：**
1. 系统级 CLAUDE.md 到 ~/.claude/CLAUDE.md（必需）
2. 提示安装可选组件：
   - 自定义 Slash Commands
   - Claude Code Templates（100+ 模板）
   - SuperClaude Framework
   - Claude Config Editor

## 安装脚本

| 脚本 | 用途 | 必需 |
|------|------|------|
| `install.sh` | 安装官方 Claude Code CLI | 是 |
| `setup.sh` | 安装个人配置和工具 | 否 |

**用法：**
```bash
# 完整设置（推荐）
./install.sh    # 安装 Claude Code
./setup.sh      # 添加增强功能

# 最小安装
./install.sh    # 仅安装 Claude Code
```

## 项目结构

```
claude-code/
├── install.sh                 # Claude Code CLI 安装器
├── setup.sh                   # 配置设置脚本
├── config/
│   └── CLAUDE.md              # 系统级开发准则
├── commands/                  # 自定义斜杠命令
│   ├── audit-compliance.md
│   ├── commit.md
│   ├── remind.md
│   └── work-report.md
├── tools/                     # 增强工具
│   ├── claude-code-templates/
│   ├── claude-config-editor/
│   └── superclaude-framework/
└── README.md
```

## 包含的组件

| 组件 | 类型 | 用途 | Token 成本 |
|------|------|------|-----------|
| 系统 CLAUDE.md | 必需 | 开发准则 | 0 |
| 自定义 Slash Commands | 可选 | 工作流自动化 | 0 |
| Claude Code Templates | 可选 | 100+ 模板 | 0 |
| SuperClaude Framework | 可选 | 元编程 | 30-40K/任务 |
| Claude Config Editor | 可选 | 配置清理 | 0 |

### 1. 系统级 CLAUDE.md（必需）

开发准则：
- Fail-Fast 原则
- Single Source of Truth
- Minimal Code 原则
- DRY / YAGNI 原则
- 通信协议

### 2. 自定义 Slash Commands（可选）

**`/audit-compliance`** - 代码合规性审计

从 ~/.claude/CLAUDE.md 动态提取原则：
- 解析 markdown 结构（必需/禁止模式）
- 从关键词推断严重性（MUST/NEVER → 关键）
- 两种模式：自动修复（默认）或交互式
- Serena MCP 集成进行符号分析

用法：
```bash
/audit-compliance                         # 自动修复违规
/audit-compliance --interactive           # 审查每个更改
/audit-compliance --focus naming          # 特定领域
/audit-compliance --baseline              # 跟踪进度
```

**`/commit`** - 规范化提交格式

使用 conventional commit 格式创建格式良好的提交。

**`/remind`** - 加载 CLAUDE.md 准则

将系统级开发准则加载到会话上下文。

**`/work-report`** - 生成工作报告

生成简洁的工作贡献摘要并保存为 markdown。

### 3. Claude Code Templates（可选）

100+ 即用型模板：
- 48+ 专业 agents
- 21+ slash commands
- MCP 集成
- 设置/钩子

详情：tools/claude-code-templates/README.md

### 4. SuperClaude Framework（可选）

元编程框架：
- 3 个核心插件（PM Agent、Research、Index）
- 16 个智能 agents
- 7 种操作模式
- 8 个 MCP 服务器集成

详情：tools/superclaude-framework/README.md

### 5. Claude Config Editor（可选）

基于 Web 的配置管理：
- 可视化界面进行配置清理
- 批量项目删除（17 MB → 732 KB）
- MCP 服务器管理
- 自动备份支持

详情：tools/claude-config-editor/README.md

## 手动安装工具

如果在 `setup.sh` 中跳过了工具，可手动安装：

```bash
./tools/claude-code-templates/install.sh
./tools/claude-config-editor/install.sh
./tools/superclaude-framework/install.sh
```

## 安装后操作

**验证 Claude Code 安装：**
```bash
claude --version
claude doctor
```

**检查配置：**
```bash
ls -la ~/.claude/CLAUDE.md
ls -la ~/.claude/commands/
```

**开始使用 Claude Code：**
```bash
cd /path/to/your/project
claude
```

**首次设置：**
- 在 Claude Code 中运行 `/login` 进行身份验证
- 或设置 `ANTHROPIC_API_KEY` 环境变量

## 配置

**优先级层次：**
- 系统级：~/.claude/CLAUDE.md（所有项目）
- 项目级：<project>/CLAUDE.md（覆盖系统级）

**备份：**
```bash
cp ~/.claude/CLAUDE.md ~/.claude/CLAUDE.md.backup.$(date +%Y%m%d)
```

**更新 Claude Code：**
```bash
brew upgrade claude-code
```

**卸载：**
```bash
brew uninstall --cask claude-code
rm -rf ~/.claude  # 删除所有配置
```

## 资源

- **官方文档**：https://docs.anthropic.com/en/docs/claude-code
- **Claude Code 设置**：https://code.claude.com/docs/en/setup
- **工具详情**：查看 tools/*/README.md 了解各工具文档

#!/bin/bash
# ============================================================================
# Claude Code Templates 安装脚本
# ============================================================================
#
# 即用型配置集合：AI agents、自定义命令、MCP 集成和项目模板
#
# 核心组件：
#   • 48+ Agents（安全审计、性能优化、架构设计）
#   • 21+ Commands（/generate-tests、/optimize-bundle）
#   • MCP 集成（GitHub、PostgreSQL、Stripe、AWS、OpenAI）
#   • 开发工具（Analytics、Conversation Monitor、Health Check）
#
# 安装：npx claude-code-templates@latest
# 文档：https://github.com/davila7/claude-code-templates
# ============================================================================

# ============================================================================
# Agents - 专业化的 Claude 代理
# ============================================================================
# 【定义】
#   Agent 是独立的 Claude 实例，具有特定领域的专业知识和工具权限
#   在独立会话中运行，Claude 可以主动调用或用户明确要求时调用
#
# 【工作方式】
#   主 Claude → 调用 Task 工具 → 启动 Agent 会话 → Agent 执行任务 → 返回结果
#
# 【典型应用】
#   • 代码审查：security-engineer 审查安全漏洞
#   • 性能优化：performance-engineer 分析性能瓶颈
#   • 架构设计：system-architect 设计系统架构
#
# 【与 Slash Command 的区别】
#   Agent：独立会话，Claude 主动调用，黑盒执行，适合复杂任务
#   Command：当前会话，用户触发，过程可见，适合固定流程
#
# 【配置位置】~/.claude/agents/code-reviewer.md
# ============================================================================
npx claude-code-templates@latest --agent=ai-specialists/prompt-engineer --directory ~ --yes

# ============================================================================
# Slash Commands - 用户可输入的快捷命令
# ============================================================================
# 【定义】
#   用户输入的快捷命令（如 /commit），展开为完整的提示词模板
#   在当前会话中执行，过程对用户可见
#
# 【工作方式】
#   用户输入 /command → 系统读取命令文件 → 内容注入当前对话 → Claude 执行
#
# 【典型应用】
#   • /commit：创建规范的 git commit
#   • /code-review：执行代码审查
#   • /generate-tests：生成测试代码
#   • /refactor：重构代码
#   • /update-docs：更新文档
#
# 【参数支持】
#   /commit "add login feature" → $ARGUMENTS 变量接收参数
#
# 【与 Agent 的区别】
#   Command：当前会话，用户触发 /xxx，过程可见，Token 累加
#   Agent：新会话，Claude 调用，黑盒执行，Token 独立计算
#
# 【配置位置】~/.claude/commands/commit.md
# ============================================================================
npx claude-code-templates@latest --command=utilities/ultra-think --directory ~ --yes
npx claude-code-templates@latest --command=documentation/create-architecture-documentation --directory ~ --yes
npx claude-code-templates@latest --command=team/architecture-review --directory ~ --yes
npx claude-code-templates@latest --command=project-management/todo --directory ~ --yes

# ============================================================================
# Settings - Claude Code 行为配置
# ============================================================================
# 【定义】
#   控制 Claude Code 行为和权限的配置文件（JSON 格式）
#   通过环境变量、权限控制、工具配置等方式影响 Claude 的运行
#
# 【配置位置】（优先级从高到低）
#   1. managed-settings.json       - 企业策略（IT 部门）
#   2. .claude/settings.local.json - 项目个人设置（gitignored）
#   3. .claude/settings.json       - 项目团队设置（共享）
#   4. ~/.claude/settings.json     - 用户全局设置
#
# 【主要配置项】
#   • permissions：权限控制（allow/deny/ask 规则）
#   • env：环境变量（API keys, feature flags）
#   • statusLine：状态栏显示（token 使用率、上下文监控）
#   • mcpServers：MCP 服务器配置
#   • disabledTools：禁用的工具列表
#
# 【典型应用】
#   • context-monitor：实时显示 token 使用率，避免超限
#   • performance-optimization：限制 max tokens，提高响应速度
#   • permission-sets：配置工具使用权限
#
# 【示例配置】
#   {
#     "statusLine": {
#       "type": "command",
#       "command": "python3 .claude/scripts/context-monitor.py"
#     },
#     "env": {
#       "CLAUDE_CODE_MAX_OUTPUT_TOKENS": "8000"
#     }
#   }
# ============================================================================
# npx claude-code-templates@latest --setting=statusline/context-monitor --directory ~ --yes

# ============================================================================
# Hooks - 事件触发的自动化脚本
# ============================================================================
# 【定义】
#   在特定事件发生时自动执行的 shell 脚本
#   通过 settings.json 配置，在工具使用前后、对话开始结束时触发
#
# 【工作方式】
#   事件发生 → 触发 Hook → 执行 shell 命令 → 继续原流程
#
# 【可用的 Hook 事件】
#   • user-prompt-submit：用户发送消息时
#   • tool-use-ask：Claude 请求工具使用权限时
#   • tool-use-allow：工具使用被允许时
#   • tool-use-deny：工具使用被拒绝时
#   • PostToolUse：工具执行完成后（可指定特定工具）
#   • conversation-start/end：对话开始/结束时
#
# 【典型应用】
#   • simple-notifications：工具执行完成后发送桌面通知
#   • smart-commit：Edit/Write 后自动创建 git commit
#   • context-monitor：用户发送消息时检查 token 使用率
#   • auto-backup：对话结束时自动备份会话
#
# 【示例配置】
#   {
#     "hooks": {
#       "PostToolUse": [
#         {
#           "matcher": "Write|Edit",  // 匹配工具
#           "hooks": [
#             {
#               "type": "command",
#               "command": "git add $CLAUDE_TOOL_FILE_PATH"
#             }
#           ]
#         }
#       ]
#     }
#   }
#
# 【环境变量】
#   • $CLAUDE_TOOL_NAME：工具名称
#   • $CLAUDE_TOOL_FILE_PATH：操作的文件路径
#   • $CLAUDE_TOOL_ARGS：工具参数
#   • $PROMPT：用户消息内容
#
# 【配置位置】~/.claude/settings.json 中的 hooks 字段
# ============================================================================
# npx claude-code-templates@latest --hook=automation/simple-notifications --directory ~ --yes

# ============================================================================
# MCPs - Model Context Protocol 服务器
# ============================================================================
# 【定义】
#   MCP (Model Context Protocol) 是标准化协议，让 Claude 连接外部数据源和工具
#   通过 npx 命令启动独立的服务器进程，提供 Resources、Tools、Prompts
#
# 【工作方式】
#   Claude Code 启动 → 读取 settings.json → 启动 MCP 服务器 → Claude 可调用其功能
#
# 【MCP 提供的三种能力】
#   1. Resources（资源）：只读数据源（文件系统、数据库、API 响应）
#   2. Tools（工具）：Claude 可调用的函数（搜索代码、查询数据库、调用 API）
#   3. Prompts（提示词）：预定义的提示词模板
#
# 【典型 MCP 服务器】
#   • memory-integration：持久化记忆，跨会话保存信息
#   • playwright：浏览器自动化，Web 交互和测试
#   • web-fetch：HTTP 客户端，获取网页和 API 数据
#   • context7：查询最新的库文档和 API
#   • serena：语义代码分析和智能编辑
#   • sequential-thinking：多步骤推理和系统分析
#   • tavily：深度网络搜索和信息检索
#
# 【与内置工具的区别】
#   内置工具：Read, Write, Bash 等，Claude Code 自带
#   MCP 工具：需要配置，提供特定领域的扩展能力
#
# 【示例配置】
#   {
#     "mcpServers": {
#       "memory": {
#         "command": "npx",
#         "args": ["-y", "@modelcontextprotocol/server-memory"],
#         "env": {}
#       },
#       "playwright": {
#         "command": "npx",
#         "args": ["-y", "@executeautomation/playwright-mcp-server"]
#       }
#     }
#   }
#
# 【查看 MCP 使用情况】
#   使用 /context 命令查看哪些 MCP 在消耗 token
#
# 【配置位置】~/.claude/settings.json 中的 mcpServers 字段
# ============================================================================
# npx claude-code-templates@latest --mcp=integration/memory-integration --directory ~ --yes

# ============================================================================
# Plugins - 扩展功能包（非官方概念）
# ============================================================================
# 【定义】
#   Plugin 不是 Claude Code 的官方概念，在不同上下文中含义不同：
#   • Claude Code Templates 中：一组相关组件的集合包（Agents + Commands + Settings）
#   • SuperClaude 中：功能模块（PM Agent、Research、Index 等）
#   • 一般理解：可能指 MCP 服务器或一组配置的组合
#
# 【Claude Code Templates 的 Plugin】
#   Plugin 是预构建的组件组合，解决特定领域的完整工作流
#   例如：ai-ml-toolkit 可能包含机器学习相关的 Agents、Commands、Settings
#
# 【与其他概念的关系】
#   Plugin = Agents + Commands + Settings + MCPs 的组合包
#   ├─ Agents：领域专家（如 ml-model-optimizer）
#   ├─ Commands：快捷命令（如 /train-model）
#   ├─ Settings：配置选项（如 GPU 设置）
#   └─ MCPs：外部集成（如 TensorFlow MCP）
#
# 【典型 Plugin】（Claude Code Templates）
#   • ai-ml-toolkit：机器学习工作流
#   • web-dev-suite：Web 开发全栈工具
#   • security-toolkit：安全审计工具集
#   • data-engineering：数据工程工具
#
# 【SuperClaude 的 Plugin】
#   • pm-agent：项目管理和任务协调
#   • research：深度研究和信息收集
#   • index：项目知识库生成
#
# 【安装方式】
#   Claude Code Templates: /plugin install <name>@claude-code-templates
#   SuperClaude: 在安装时选择对应的 plugin 组件
#
# 【注意】
#   Plugin 是社区概念，不是 Claude Code 官方定义
#   实际上是多个组件的便捷安装方式
# ============================================================================
# /plugin install ai-ml-toolkit@claude-code-templates

# ============================================================================
# Skills - 技能/能力（非官方概念）
# ============================================================================
# 【定义】
#   Skill 不是 Claude Code 的官方概念，通常指以下几种含义：
#   1. Agent 的专业能力（如 security-engineer 的漏洞检测技能）
#   2. Slash Command 的别称（某些文档中）
#   3. MCP 提供的功能集（如 filesystem MCP 的文件操作技能）
#   4. Claude Code Templates 中的可执行模板
#
# 【Claude Code Templates 的 Skill】
#   Skill 类似于 Slash Command，但可能更强调可复用的代码片段或工作流
#   例如：skill-creator 可能帮助创建新的 skills/commands
#
# 【与其他概念的区别】
#   • Agent：独立会话的专家系统（整体能力）
#   • Command：用户触发的命令（单次操作）
#   • Skill：可能是 Command 的同义词，或指某种能力模板
#   • MCP Tools：外部服务提供的功能
#
# 【实际理解】
#   在大多数情况下，Skill 可以理解为：
#   • 与 Slash Command 类似的快捷操作
#   • 或者是 Agent/MCP 具备的某项专业能力
#
# 【典型 Skill】（如果存在）
#   • skill-creator：创建新的 skills/commands
#   • code-analyzer：代码分析技能
#   • test-generator：测试生成技能
#
# 【注意】
#   Skill 在 Claude Code 官方文档中没有明确定义
#   不同工具/框架可能有不同的 Skill 概念
#   使用时需要根据具体上下文理解其含义
#
# 【建议】
#   如果看到 "skill"，优先理解为：
#   1. 是否是 Slash Command 的另一种说法？
#   2. 是否是 Agent 的某项能力？
#   3. 是否是 MCP 提供的工具？
# ============================================================================
# npx claude-code-templates@latest --skill=development/skill-creator --directory ~ --yes


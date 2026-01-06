# SuperClaude Framework

> Meta-programming configuration framework for Claude Code

**Version**: v4.2.0 | **License**: MIT | **GitHub Stars**: 17.5K+

## Overview

SuperClaude Framework is a meta-programming configuration framework that transforms Claude Code into a structured development platform through behavior injection and component orchestration. Its core value lies in systemized workflow automation and intelligent tool integration.

## Core Components

### Plugins (3 core plugins)

| Plugin | Purpose |
|--------|---------|
| **PM Agent** | Project management agent, task orchestration and coordination |
| **Research** | Deep web research, multi-step reasoning |
| **Index** | Project indexing and context optimization |

### Intelligent Agents (16 domain experts)

Provides multiple professional AI agents, including:
- Backend Architect
- Frontend Architect
- Performance Engineer
- Security Engineer
- DevOps Architect
- Technical Writer
- And more...

### Operation Modes (7 modes)

- **Quick**: Fast queries (5-10 sources)
- **Standard**: General research (default mode)
- **Deep**: In-depth analysis (20-40 sources)
- **Exhaustive**: Academic-level research (40+ sources)
- **Brainstorm**: Brainstorming mode
- Other specialized modes...

### MCP Server Integration (8 servers)

- **Tavily**: Web search
- **Serena**: Code semantic analysis
- **Mindbase**: Knowledge base
- **Sequential Thinking**: Reasoning enhancement
- **Context7**: Documentation retrieval
- **Morphllm Fast Apply**: Fast code application
- And more...

## Key Features

### 1. Performance Optimization

- **94% Token Reduction**: 58K → 3K
- **2-3x Speed Boost**: With MCP integration
- **30-50% Cost Reduction**: Lower token usage

### 2. Developer Experience

- ✅ **TypeScript Plugin Hot Reload**: Edit and save for immediate effect
- ✅ **Zero-install Process**: Automatic project detection (`.claude-plugin/` directory)
- ✅ **Confidence-driven**: ≥90% threshold intelligent orchestration
- ✅ **Full Functionality Without MCP**: Optional MCP server usage

### 3. Intelligent Research Capabilities

- Multi-step reasoning (up to 5 hops)
- Quality scoring mechanism
- Cross-session learning ability
- Intelligent tool auto-orchestration

## Installation

### Quick Start

```bash
# 1. Clone project
git clone https://github.com/SuperClaude-Org/SuperClaude_Framework.git
cd SuperClaude_Framework

# 2. Start Claude Code
claude  # .claude-plugin/ auto-detected and loaded
```

### Key Commands

```bash
/pm          # Start PM Agent for task orchestration
/research    # Conduct deep web research
/index-repo  # Optimize project context index
```

## Architecture Design

### Plugin Loading Mechanism

```
project-root/
└── .claude-plugin/       # Plugin directory (auto-detected)
    ├── pm-agent.ts       # PM Agent plugin
    ├── research.ts       # Research plugin
    └── index.ts          # Index plugin
```

### SessionStart Hook

- Automatically activates PM Agent
- No manual initialization required
- Intelligent context awareness

### MCP Integration Architecture

```
Claude Code
    ↓
airis-mcp-gateway
    ↓
├── Tavily (search)
├── Serena (code analysis)
├── Mindbase (knowledge base)
└── ... (6 other servers)
```

## Use Cases

| Scenario Type | Recommended Mode | Data Sources | Token Usage |
|--------------|------------------|--------------|-------------|
| Quick Query | Quick | 5-10 | Low |
| Daily Development | Standard | 10-20 | Medium |
| Deep Analysis | Deep | 20-40 | Medium-High |
| Academic Research | Exhaustive | 40+ | High |

### Typical Use Cases

1. **Algorithm Design**: Design PPO reward functions
2. **Problem Diagnosis**: Diagnose training instability
3. **Architecture Optimization**: Optimize neural network architecture
4. **Code Review**: Review complex code logic
5. **Deep Research**: Multi-step reasoning for technical research

## Performance Data

### Token Optimization Comparison

| Scenario | Unoptimized | SuperClaude | Optimization Rate |
|----------|-------------|-------------|-------------------|
| Typical Task | 58K | 3K | 94% ↓ |

### Execution Speed

| Configuration | Full Functionality | Speed | Token Usage |
|---------------|-------------------|-------|-------------|
| Without MCP | ✓ Full support | Baseline | Baseline |
| With MCP | ✓ Full support | 2-3x faster | 30-50% ↓ |

## Important Notes

### V2.0 Major Changes

SuperClaude Framework v2.0 underwent architectural refactoring:

- **From 27 slash commands** → **3 TypeScript plugins**
- Removed global command directory `~/.claude/commands/sc/`
- Adopted project-local detection mechanism (`.claude-plugin/`)

### Upgrade Steps

If upgrading from old version:

```bash
# 1. Remove old global commands
rm -rf ~/.claude/commands/sc/

# 2. Use new project-local plugins
cd your-project
# Ensure .claude-plugin/ directory exists
```

## Resources

- **GitHub**: https://github.com/SuperClaude-Org/SuperClaude_Framework
- **Website**: https://superclaude.netlify.app/
- **PyPI**: https://pypi.org/project/superclaude/
- **NPM**: https://www.npmjs.com/package/@bifrost_inc/superclaude

## Core Value

SuperClaude Framework's core value:

1. **Systemized**: Transforms Claude Code from conversational tool to structured platform
2. **Extensible**: Flexible capability extension through plugins and MCP servers
3. **Efficient**: Dramatically reduces token consumption and improves execution speed
4. **Intelligent**: Confidence-driven intelligent task orchestration
5. **Professional**: 16 domain expert agents covering full-stack development

---

**Last Updated**: 2025-10-30
**Data Source**: https://github.com/SuperClaude-Org/SuperClaude_Framework

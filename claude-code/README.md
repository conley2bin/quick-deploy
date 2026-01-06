# Claude Code System

Personal Claude Code configuration collection for quick setup on new devices.

Comprehensive toolkit streamlining Claude Code configuration across machines: system-level development guidelines + automated tool installation.

## Features

- System-level CLAUDE.md: Standardized development principles
- Tool integration: Claude Code ecosystem installation
- Interactive setup: Select tools during installation
- Backup protection: Optional configuration backup
- Quick deploy: One-command installation

## Quick Start

```bash
git clone <repository-url>
cd claude-code-system
chmod +x install.sh
./install.sh
```

Installer actions:
1. Installs system-level CLAUDE.md to ~/.claude/CLAUDE.md
2. Prompts for optional components:
   - Custom Slash Commands
   - Claude Code Templates (100+ templates)
   - SuperClaude Framework
   - Claude Config Editor

## Project Structure

```
claude-code-system/
├── config/
│   └── CLAUDE.md              # System-level guidelines
├── commands/                  # Slash commands
│   ├── audit-compliance.md
│   ├── commit.md
│   └── remind.md
├── tools/                     # Installation scripts
│   ├── claude-code-templates/
│   ├── claude-config-editor/
│   └── superclaude-framework/
└── install.sh                 # Main installer
```

## Included Components

| Component | Type | Purpose | Token Cost |
|-----------|------|---------|------------|
| System CLAUDE.md | Required | Development guidelines | 0 |
| Custom Slash Commands | Optional | Workflow automation | 0 |
| Claude Code Templates | Optional | 100+ templates | 0 |
| SuperClaude Framework | Optional | Meta-programming | 30-40K/task |
| Claude Config Editor | Optional | Config cleanup | 0 |

### 1. System-Level CLAUDE.md (Required)

Development guidelines:
- Fail-Fast Principle
- Single Source of Truth
- Minimal Code Principle
- DRY / YAGNI Principles
- Communication protocols

### 2. Custom Slash Commands (Optional)

**`/audit-compliance`** - Code compliance audit

Dynamically extracts principles from ~/.claude/CLAUDE.md:
- Parses markdown structure (Required/Forbidden patterns)
- Infers severity from keywords (MUST/NEVER → critical)
- Two modes: Auto-fix (default) or Interactive
- Serena MCP integration for symbolic analysis

Usage:
```bash
/audit-compliance                         # Auto-fix violations
/audit-compliance --interactive           # Review each change
/audit-compliance --focus naming          # Specific domain
/audit-compliance --baseline              # Track progress
```

**`/commit`** - Conventional commit formatting

Creates well-formatted commits using conventional commit format.

**`/remind`** - Load CLAUDE.md guidelines

Loads system-level development guidelines into session context.

### 3. Claude Code Templates (Optional)

100+ ready-to-use templates:
- 48+ specialized agents
- 21+ slash commands
- MCP integrations
- Settings/hooks

Details: tools/claude-code-templates/README.md

### 4. SuperClaude Framework (Optional)

Meta-programming framework:
- 3 core plugins (PM Agent, Research, Index)
- 16 intelligent agents
- 7 operation modes
- 8 MCP server integrations

Details: tools/superclaude-framework/README.md

### 5. Claude Config Editor (Optional)

Web-based configuration management:
- Visual interface for config cleanup
- Bulk project deletion (17 MB → 732 KB reduction)
- MCP server management
- Auto-backup support

Details: tools/claude-config-editor/README.md

## Manual Tool Installation

Install skipped tools manually:

```bash
./tools/claude-code-templates/install.sh
./tools/claude-config-editor/install.sh
./tools/superclaude-framework/install.sh
```

## Configuration

**Priority hierarchy:**
- System-level: ~/.claude/CLAUDE.md (all projects)
- Project-level: <project>/CLAUDE.md (overrides system)

**Backup:**
```bash
cp ~/.claude/CLAUDE.md ~/.claude/CLAUDE.md.backup.$(date +%Y%m%d)
```

**Verification:**
```bash
ls -la ~/.claude/CLAUDE.md
```

Restart Claude Code after installation to apply changes.

## Resources

Claude Code Documentation: https://docs.claude.com/claude-code

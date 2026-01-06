#!/bin/bash
# ============================================================================
# Claude Code System - Main Installation Script
# ============================================================================
#
# Project: Personal Claude Code Configuration Collection
# Purpose: Quick setup of complete Claude Code development environment
#
# Installation Includes:
#   1. System-level CLAUDE.md configuration (required)
#   2. Custom Slash Commands (optional, project-specific commands)
#   3. Claude Code Templates (optional, 100+ templates)
#   4. SuperClaude Framework (optional, meta-programming framework)
#   5. Claude Config Editor (optional, config file cleanup tool)
#
# Usage:
#   chmod +x install.sh
#   ./install.sh
#
# ============================================================================

set -e  # Exit immediately on error

# Color definitions
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Get script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo -e "${BLUE}============================================================================${NC}"
echo -e "${BLUE}  Claude Code System - Main Installation Script${NC}"
echo -e "${BLUE}============================================================================${NC}"
echo ""

# ============================================================================
# Step 1: Install system-level CLAUDE.md
# ============================================================================
echo -e "${GREEN}[1/4]${NC} Installing system-level CLAUDE.md..."
echo ""

# Create ~/.claude directory
mkdir -p ~/.claude

# Check if existing CLAUDE.md exists
if [ -f ~/.claude/CLAUDE.md ]; then
    echo -e "${YELLOW}  Existing CLAUDE.md detected at ~/.claude/CLAUDE.md${NC}"
    echo -e "${YELLOW}  Do you want to backup the existing file? (y/n)${NC}"
    read -r backup_choice

    if [[ "$backup_choice" =~ ^[Yy]$ ]]; then
        BACKUP_FILE=~/.claude/CLAUDE.md.backup.$(date +%Y%m%d_%H%M%S)
        cp ~/.claude/CLAUDE.md "$BACKUP_FILE"
        echo -e "${GREEN}  Backed up to: $BACKUP_FILE${NC}"
    else
        echo -e "${YELLOW}  Skipping backup...${NC}"
    fi
fi

# Copy new CLAUDE.md
if [ -f "$SCRIPT_DIR/config/CLAUDE.md" ]; then
    cp "$SCRIPT_DIR/config/CLAUDE.md" ~/.claude/CLAUDE.md
    echo -e "${GREEN}  CLAUDE.md installed successfully${NC}"
else
    echo -e "${RED}  Error: Cannot find config/CLAUDE.md${NC}"
    exit 1
fi

echo ""

# ============================================================================
# Step 2: Install Custom Slash Commands
# ============================================================================
echo -e "${GREEN}[2/5]${NC} Custom Slash Commands..."
echo ""

if [ -d "$SCRIPT_DIR/commands" ] && [ "$(ls -A $SCRIPT_DIR/commands/*.md 2>/dev/null)" ]; then
    echo -e "${BLUE}  Found custom slash commands in commands/ directory${NC}"
    echo -e "${YELLOW}  Do you want to install custom slash commands? (y/n)${NC}"
    read -r install_commands

    if [[ "$install_commands" =~ ^[Yy]$ ]]; then
        # Create ~/.claude/commands directory if it doesn't exist
        mkdir -p ~/.claude/commands

        # Count .md files (excluding README.md)
        command_count=$(find "$SCRIPT_DIR/commands" -maxdepth 1 -name "*.md" ! -name "README.md" | wc -l)

        if [ "$command_count" -gt 0 ]; then
            echo -e "${BLUE}  Installing $command_count custom slash command(s)...${NC}"

            # Copy all .md files except README.md, overwriting if exists
            find "$SCRIPT_DIR/commands" -maxdepth 1 -name "*.md" ! -name "README.md" -exec cp {} ~/.claude/commands/ \;

            echo -e "${GREEN}  Custom slash commands installed successfully${NC}"
            echo -e "${BLUE}  Installed to: ~/.claude/commands/${NC}"
        else
            echo -e "${YELLOW}  No custom slash commands found (excluding README.md)${NC}"
        fi
    else
        echo -e "${YELLOW}  Skipping custom slash commands installation...${NC}"
    fi
else
    echo -e "${YELLOW}  No custom slash commands directory found, skipping...${NC}"
fi

echo ""

# ============================================================================
# Step 3: Install Claude Code Templates
# ============================================================================
echo -e "${GREEN}[3/5]${NC} Claude Code Templates..."
echo ""
echo -e "${YELLOW}  Do you want to install Claude Code Templates? (y/n)${NC}"
read -r install_templates

if [[ "$install_templates" =~ ^[Yy]$ ]]; then
    if [ -f "$SCRIPT_DIR/tools/claude-code-templates/install.sh" ]; then
        chmod +x "$SCRIPT_DIR/tools/claude-code-templates/install.sh"

        echo -e "${BLUE}  Executing claude-code-templates installation script...${NC}"
        echo ""

        # Execute installation script
        bash "$SCRIPT_DIR/tools/claude-code-templates/install.sh"

        # Fix all relative paths to absolute paths in settings.local.json
        if [ -f ~/.claude/settings.local.json ]; then
            echo -e "${BLUE}  Fixing relative paths to absolute paths in settings...${NC}"

            # Replace all .claude/ references with ~/.claude/
            # Handles: "command": "python3 .claude/scripts/xxx.py"
            #          "command": "bash .claude/hooks/xxx.sh"
            #          Any other .claude/ references
            sed -i.bak 's|"\([^"]*\)\.claude/|"\1~/.claude/|g' ~/.claude/settings.local.json

            echo -e "${GREEN}  All relative paths fixed to absolute paths${NC}"
        fi

        echo ""
        echo -e "${GREEN}  Claude Code Templates installed successfully${NC}"
    else
        echo -e "${RED}  Error: Cannot find tools/claude-code-templates/install.sh${NC}"
        exit 1
    fi
else
    echo -e "${YELLOW}  Skipping Claude Code Templates installation...${NC}"
fi

echo ""

# ============================================================================
# Step 4: Install SuperClaude Framework
# ============================================================================
echo -e "${GREEN}[4/5]${NC} SuperClaude Framework..."
echo ""
echo -e "${YELLOW}  Do you want to install SuperClaude Framework? (y/n)${NC}"
read -r install_superclaude

if [[ "$install_superclaude" =~ ^[Yy]$ ]]; then
    if [ -f "$SCRIPT_DIR/tools/superclaude-framework/install.sh" ]; then
        chmod +x "$SCRIPT_DIR/tools/superclaude-framework/install.sh"

        echo -e "${BLUE}  Executing SuperClaude Framework installation script...${NC}"
        echo ""

        # Execute installation script
        bash "$SCRIPT_DIR/tools/superclaude-framework/install.sh"

        echo ""
        echo -e "${GREEN}  SuperClaude Framework installed successfully${NC}"
    else
        echo -e "${RED}  Error: Cannot find tools/superclaude-framework/install.sh${NC}"
        exit 1
    fi
else
    echo -e "${YELLOW}  Skipping SuperClaude Framework installation...${NC}"
fi

echo ""

# ============================================================================
# Step 5: Install Claude Config Editor
# ============================================================================
echo -e "${GREEN}[5/5]${NC} Claude Config Editor..."
echo ""
echo -e "${YELLOW}  Do you want to install Claude Config Editor? (y/n)${NC}"
read -r install_config_editor

if [[ "$install_config_editor" =~ ^[Yy]$ ]]; then
    if [ -f "$SCRIPT_DIR/tools/claude-config-editor/install.sh" ]; then
        chmod +x "$SCRIPT_DIR/tools/claude-config-editor/install.sh"

        echo -e "${BLUE}  Executing Claude Config Editor installation script...${NC}"
        echo ""

        # Execute installation script
        bash "$SCRIPT_DIR/tools/claude-config-editor/install.sh"

        echo ""
        echo -e "${GREEN}  Claude Config Editor installed successfully${NC}"
    else
        echo -e "${RED}  Error: Cannot find tools/claude-config-editor/install.sh${NC}"
        exit 1
    fi
else
    echo -e "${YELLOW}  Skipping Claude Config Editor installation...${NC}"
fi

echo ""


# ============================================================================
# Installation Complete
# ============================================================================
echo -e "${BLUE}============================================================================${NC}"
echo -e "${GREEN}  All components installed successfully!${NC}"
echo -e "${BLUE}============================================================================${NC}"
echo ""

echo "Installed components:"
echo "  - System-level CLAUDE.md (~/.claude/CLAUDE.md)"

# Show what was installed based on user choices
if [[ "$install_commands" =~ ^[Yy]$ ]]; then
    echo "  - Custom Slash Commands (project-specific commands)"
fi

if [[ "$install_templates" =~ ^[Yy]$ ]]; then
    echo "  - Claude Code Templates (100+ template library)"
fi

if [[ "$install_config_editor" =~ ^[Yy]$ ]]; then
    echo "  - Claude Config Editor (config file cleanup tool)"
fi

if [[ "$install_superclaude" =~ ^[Yy]$ ]]; then
    echo "  - SuperClaude Framework (meta-programming framework)"
fi

echo ""

echo -e "${YELLOW}Next steps:${NC}"
echo "  1. Restart Claude Code to apply configuration"
echo "  2. Check README.md for tool details and comparison"
echo "  3. Configure specific options for each tool as needed"
echo ""

echo -e "${BLUE}Documentation:${NC}"
echo "  - Project overview: $SCRIPT_DIR/README.md"
echo "  - Individual tools: $SCRIPT_DIR/tools/<tool-name>/README.md"
echo ""

echo -e "${GREEN}Installation complete!${NC}"

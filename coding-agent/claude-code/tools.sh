#!/bin/bash
# ============================================================================
# Claude Code Tools - Installation Script
# ============================================================================
#
# Purpose: Install optional Claude Code tools from tools/ directory
#
# Tools Included:
#   1. Claude Code Templates (100+ template library)
#   2. SuperClaude Framework (meta-programming framework)
#   3. Claude Config Editor (config file cleanup tool)
#
# Usage:
#   bash claude-tools.sh [SCRIPT_DIR]
#
# Parameters:
#   SCRIPT_DIR: Optional. Directory containing tools/ subdirectory.
#               If not provided, uses current script's directory.
#
# ============================================================================

set -e  # Exit immediately on error

# Color definitions
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Get script directory from parameter or auto-detect
if [ -n "$1" ]; then
    SCRIPT_DIR="$1"
else
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
fi

echo -e "${BLUE}============================================================================${NC}"
echo -e "${BLUE}  Claude Code Tools - Installation Script${NC}"
echo -e "${BLUE}============================================================================${NC}"
echo ""

# Verify tools directory exists
if [ ! -d "$SCRIPT_DIR/tools" ]; then
    echo -e "${RED}Error: tools/ directory not found at $SCRIPT_DIR/tools${NC}"
    exit 1
fi

# Track installation status
install_templates=""
install_superclaude=""
install_config_editor=""

# ============================================================================
# Step 1: Install Claude Code Templates
# ============================================================================
echo -e "${GREEN}[1/3]${NC} Claude Code Templates..."
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
# Step 2: Install SuperClaude Framework
# ============================================================================
echo -e "${GREEN}[2/3]${NC} SuperClaude Framework..."
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
# Step 3: Install Claude Config Editor
# ============================================================================
echo -e "${GREEN}[3/3]${NC} Claude Config Editor..."
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
# Installation Summary
# ============================================================================
echo -e "${BLUE}============================================================================${NC}"
echo -e "${GREEN}  Tools installation complete!${NC}"
echo -e "${BLUE}============================================================================${NC}"
echo ""

echo "Installed tools:"

if [[ "$install_templates" =~ ^[Yy]$ ]]; then
    echo "  - Claude Code Templates (100+ template library)"
fi

if [[ "$install_superclaude" =~ ^[Yy]$ ]]; then
    echo "  - SuperClaude Framework (meta-programming framework)"
fi

if [[ "$install_config_editor" =~ ^[Yy]$ ]]; then
    echo "  - Claude Config Editor (config file cleanup tool)"
fi

# Check if nothing was installed
if [[ ! "$install_templates" =~ ^[Yy]$ ]] && \
   [[ ! "$install_superclaude" =~ ^[Yy]$ ]] && \
   [[ ! "$install_config_editor" =~ ^[Yy]$ ]]; then
    echo "  (No tools were installed)"
fi

echo ""

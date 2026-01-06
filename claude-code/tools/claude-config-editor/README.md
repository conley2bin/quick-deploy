# Claude Config Editor

> Web-based GUI for managing Claude configuration files

**GitHub**: https://github.com/gagarinyury/claude-config-editor
**Stars**: 400+
**License**: MIT
**Status**: Actively Maintained

---

## 🎯 Purpose

Claude Config Editor solves the "bloated config file" problem:

- **Problem**: Claude stores all conversations in a single JSON file
- **Result**: After weeks of use, files grow to 10-20 MB
- **Impact**: Slow startup times and performance degradation
- **Solution**: This tool provides a visual interface to clean up configs

---

## 🔥 The Problem It Solves

After using Claude Code for a few weeks:

```
📁 87 projects with full chat histories
💾 17 MB of JSON data
🐌 Slow Claude startup (parsing huge file)
🤷 No easy way to clean it up manually
```

---

## ✨ Features

### Core Capabilities

- 🔍 **Visual Dashboard**: See which projects consume the most space (sorted by size)
- 🗑️ **Bulk Deletion**: Delete top 10 biggest projects = 90% space savings
- 💾 **Export Before Delete**: Save important conversation histories
- 🔌 **MCP Server Management**: Edit MCP servers without touching JSON
- 🛡️ **Auto-Backup**: Automatic backups before any changes (real undo button)
- ⚡ **Zero Dependencies**: Pure Python 3.7+ and HTML

### Works With

- ✅ Claude Code
- ✅ Claude Desktop

---

## 📊 Typical Results

**Before**: 17 MB config file
**After**: 732 KB (95.7% reduction!)

**Time Required**: ~30 seconds

---

## 🚀 Quick Start

### Installation

```bash
# Run the installation script
./tools/claude-config-editor/install.sh
```

This will:
1. Clone the repository to project root: `claude-config-editor/`
2. Or update if already installed

### Usage

```bash
# Start the web server (from project root)
python3 claude-config-editor/server.py

# Open your browser to:
# http://localhost:8080
```

### First-Time Recommendations

1. **Export your config** as backup (just in case)
2. **Review project sizes** to identify biggest consumers
3. **Export important conversations** before deleting projects
4. **Delete in batches** to see immediate results

---

## 📁 Installation Location

**Location**: `claude-config-editor/` (in project root directory)

The editor is cloned directly to the project root for easy access.

---

## 🛠️ Technical Details

### How It Works

1. Reads Claude config files from standard locations:
   - Claude Code: `~/.config/claude/`
   - Claude Desktop: Platform-specific locations

2. Provides a web interface to:
   - Analyze project sizes
   - Delete selected projects
   - Manage MCP server configurations
   - Export/import configurations

3. Creates automatic backups before any modifications

### Architecture

- **Backend**: Python 3.7+ (zero external dependencies)
- **Frontend**: Pure HTML/CSS/JavaScript
- **Server**: Built-in Python HTTP server
- **Storage**: JSON file manipulation

---

## ⚠️ Important Notes

### Safety Features

- ✅ **Auto-backup** before every operation
- ✅ **Read-only mode** available
- ✅ **Confirmation prompts** for destructive actions
- ✅ **Export functionality** to preserve data

### Best Practices

1. **Backup first**: Always export your config before major deletions
2. **Test on small batch**: Delete 1-2 projects first to verify
3. **Keep important conversations**: Export before deleting
4. **Regular cleanup**: Run monthly to prevent bloat

---

## 🔗 Resources

- **Official Repository**: https://github.com/gagarinyury/claude-config-editor
- **Reddit Discussion**: See REDDIT_POST.md in the repository
- **Issue Tracker**: https://github.com/gagarinyury/claude-config-editor/issues

---

## 📝 Example Workflow

```bash
# 1. Install
./tools/claude-config-editor/install.sh

# 2. Start server (from project root)
python3 claude-config-editor/server.py

# 3. Open browser
# Navigate to http://localhost:8080

# 4. In the web interface:
#    - Click "Export Config" (backup)
#    - Review project sizes
#    - Select projects to delete
#    - Confirm deletion
#    - Verify Claude startup is faster!
```

---

## 🎓 Why This Tool Matters

Claude's single-file config approach is simple but has a downside:
- Every project conversation is saved
- No automatic cleanup
- File grows indefinitely
- Performance degrades over time

This tool gives you control without manual JSON editing.

---

**Built with ❤️ by the Claude community**

---

**Last Updated**: 2025-11-04

---
allowed-tools: Bash(git log:*), Bash(git show:*), Write
argument-hint: <MM.DD-MM.DD> [--author=name] [--lang=zh|en]
description: Generate concise work contribution summary and save to markdown (defaults: conley, Chinese)
---

# Work Report Generator

Generate concise work contribution summary by analyzing git commit history for specified time period.

## Usage

```bash
/work-report                  # Auto: last Sunday to this Saturday
/work-report 12.1-12.7
/work-report 12.18-1.2 --author="bin zhao"
/work-report 12.1-12.7 --lang=en
```

**Parameters:**
- Time period: `MM.DD-MM.DD` (optional, defaults to last Sunday-Saturday)
- `--author=name` (default: "conley")
- `--lang=zh|en` (default: zh)

## Core Workflow

1. Parse time period (auto-calculate week range if not provided, smart year detection for cross-year ranges)
2. Collect git commits: `git log --since="YYYY-MM-DD" --until="YYYY-MM-DD" --author="<author>" --format=fuller --stat --no-merges`
3. **Filter commits immediately**:
   - **EXCLUDE**: Documentation (docs:, README, comments), pure config changes
   - **INCLUDE**: Features (feat:), refactoring with functional impact, fixes (fix:)
4. **Apply translation rules**: Convert implementation details → functional descriptions
5. Generate summary with 2-5 themes, 1-3 bullet points each
6. Save to `work-report-MMDD-MMDD.md`

**KEY PRINCIPLE: Never copy commit messages directly. Translate implementation details (parameters, classes, files, numbers) into functional behavior.**

## Date Parsing Logic

**Auto mode** (no period specified): Calculate last Sunday to this Saturday using `date` commands.

**Manual mode** (`MM.DD-MM.DD`):
1. Get reference year: `git log -1 --format=%cd --date=format:%Y`
2. Parse dates: `start_month.start_day` and `end_month.end_day`
3. Year logic:
   - `start_month > end_month` → Cross-year: `start_year = ref_year - 1`
   - `start_month <= end_month` → Same-year: `start_year = ref_year`
4. Examples:
   - `12.18-1.2` (ref: 2026) → `2025-12-18` to `2026-01-02`
   - `12.1-12.19` (ref: 2025) → `2025-12-01` to `2025-12-19`

## Filtering & Categorization

**Immediate filtering after collection:**

**EXCLUDE** (example: `96f4184: docs: add comprehensive README` - documentation only):
- Documentation-only commits (docs: prefix, README/docs files)
- Pure configuration changes (parameter additions without logic)

**INCLUDE**:
- Features: New capabilities or behavior
- Refactoring: Architecture improvements with functional impact
- Fixes: Bug fixes changing behavior

**Categorize by theme:**
- Feature Development, Code Quality, Maintenance
- Group related commits into cohesive themes

## Output Format

```markdown
# 工作汇报 (YYYY.MM.DD - YYYY.MM.DD)

根据git提交记录，[author]在[时间段]共提交X次，工作贡献总结如下：

### [主题一]
- [核心要点1：问题+解决方案]
- [核心要点2：影响和效果]

### [主题二]
- [核心要点1]
```

## Translation Rules & Prohibitions

**CRITICAL: Apply these rules before writing any bullet point.**

### What to EXCLUDE (Forbidden):

| Category | Examples (WRONG) |
|---|---|
| **Parameter/Config names** | "添加enable_retry参数", "更新ProtectionConfig字段" |
| **Class/Method names** | "修改_handle_failure方法", "更新MultiRoundManager.execute()" |
| **File paths** | "修改run.sh", "更新config/app.yaml" |
| **Code structures** | "添加if语句检查", "使用循环遍历" |
| **Numeric impact** | "从0个孔洞提升到14个", "检测率从41%→60%" |
| **Code statistics** | "从240行精简到160行", "11个配置项减少到4个" |
| **Documentation commits** | README updates, docs writing, comment additions |

### What to INCLUDE (Required):

| Type | Correct Approach |
|---|---|
| **Functionality** | "实现任务级别重试控制机制" (not "添加enable_retry参数") |
| **Behavior** | "失败时根据配置决定是否重试" (not "修改_handle_failure检查enable_retry") |
| **Impact** | "修复对象检测失败问题" (not "从0个提升到14个") |
| **Simplification** | "简化算法复杂度" (not "从11个配置项减少到4个，240行→160行") |

### Translation Examples:

| Commit Message (Implementation) | Work Report (Functionality) |
|---|---|
| Add enable_retry parameter to control retry | 实现任务级别重试控制机制，支持失败时选择终止或重试 |
| Modify _handle_failure to check enable_retry | 失败处理逻辑根据配置决定重试策略 |
| Remove hardcoded /home/user/conda/envs paths | 移除硬编码环境路径，支持任意Python环境管理工具 |
| Fix detection: 0→14 holes, simplified 240→160 lines | 修复对象检测失败问题，简化算法复杂度 |

### Verification Checklist

Before writing each bullet point:

- [ ] No parameter names (enable_retry, max_attempts)
- [ ] No class/method names (ProtectionConfig, _handle_failure)
- [ ] No file paths (run.sh, config/app.yaml)
- [ ] No code structures (if-statement, loop)
- [ ] No numeric details (0→14, 41%→60%, line counts)
- [ ] No code statistics (240→160 lines, 11→4 params)
- [ ] DOES describe functionality/behavior
- [ ] DOES use action verbs (实现、修复、简化)

## Complete Example

**Scenario: 12.29-1.5 Period with 3 commits**

**Commits:**
```
1. 96f4184: docs: add comprehensive README (creates README.md)
2. ca1c920: refactor: remove hardcoded environment paths
3. 34c164a: feat: add retry control configuration
   - Add enable_retry parameter
   - Modify _handle_failure to check enable_retry
   - Simplify from 11 params to 4, 240 lines to 160
```

**Filtering:**
- Commit 1: **EXCLUDE** (documentation)
- Commit 2: **INCLUDE** (functional refactoring)
- Commit 3: **INCLUDE** (new feature)

**WRONG Output:**
```markdown
### 项目文档完善
- 编写完整的README文档，涵盖系统架构、安装步骤

### 任务重试控制功能
- 新增enable_retry参数控制失败后是否重试
- 简化算法参数，从11个配置项减少到4个，代码从240行精简至160行
```
**Violations:** Included documentation, mentioned parameter name, included code statistics.

**CORRECT Output:**
```markdown
### 任务重试控制功能
- 实现任务级别重试控制机制，支持失败时选择立即终止或继续重试
- 失败时根据配置决定是否进入重试流程或直接抛出异常终止任务

### 脚本环境可移植性改进
- 移除所有硬编码conda环境路径，使脚本兼容任意Python环境管理工具
- 改用当前激活环境的解释器，依赖缺失时快速失败并提供明确错误提示
```
**Correct:** Documentation excluded, functional language, no numbers/parameters.

## Configuration

- Author: "conley" (override with --author)
- Language: Chinese (override with --lang=en)
- Time range: Auto-detect last Sunday-Saturday if not provided
- Year detection: Automatic for cross-year ranges
- Output: `work-report-MMDD-MMDD.md` in project root
- Format: 2-5 themes, 1-3 bullet points each
- Focus: Functional changes only, no documentation/config/statistics

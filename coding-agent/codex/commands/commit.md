---
allowed-tools: Bash(git add:*), Bash(git status:*), Bash(git commit:*), Bash(git diff:*), Bash(git log:*)
argument-hint: [message]
description: Create well-formatted commits with conventional commit format
---

# Smart Git Commit

Invocation of this prompt is an explicit request to create a commit. Proceed with the commit workflow below without asking for additional confirmation.

CRITICAL: NEVER create commits automatically.
CRITICAL: NEVER commit untracked files (git status显示为 ?? 的文件).
CRITICAL: NEVER modify file contents without explicit user permission.

ONLY commit when:
1. User explicitly runs /prompts:commit command
2. User explicitly requests "commit" or "create a commit"

Default behavior: Do not commit. If uncertain, do not commit.

**Git Status Codes (git status --porcelain):**
- `??` = untracked files (从未git add的文件) - DO NOT commit
- `A ` = staged new files (已git add的新文件) - SHOULD commit
- `M ` = staged modifications (已暂存的修改) - SHOULD commit
- ` M` = unstaged modifications (未暂存的修改) - will be staged by git add -u

**Commit logic:**
- Use `git add -u` to stage modifications of tracked files (excludes ?? files)
- If files with `A ` status already exist (user manually added), commit them together
- NEVER `git add` files with `??` status
- Commit all staged changes (`A ` and `M ` status)

---

Create well-formatted commit: $ARGUMENTS

**Parameter usage:**
- `/prompts:commit` → Auto-generate commit message in conventional format
- `/prompts:commit "message"` → Use provided message directly as commit message
- `/prompts:commit subdir/` → Commit changes in specified directory/submodule

## CRITICAL: Handle Directory/Submodule Arguments

**If $ARGUMENTS is provided and is a valid directory path or submodule:**
1. MUST change to that directory FIRST: `cd $ARGUMENTS`
2. ALL subsequent git commands MUST run in that directory
3. Use `cd $ARGUMENTS && git status` NOT just `git status`
4. This is for committing changes INSIDE submodules or subdirectories

**Example:**
- `/prompts:commit mano_assets` → commit changes IN mano_assets submodule
- `/prompts:commit` (no args) → commit changes in current working directory

## Current Repository State

- Git status: !`git status --porcelain`
- Current branch: !`git branch --show-current`
- Staged changes: !`git diff --cached --stat`
- Unstaged changes: !`git diff --stat`
- Recent commits: !`git log --oneline -5 2>/dev/null || echo "No commits yet"`

## What This Command Does

**STEP 0: Determine working directory**
- If $ARGUMENTS is a directory/submodule path → `cd $ARGUMENTS` and work there
- Otherwise → work in current directory (main repository)

**STEP 1-5: Git operations**
1. Stage tracked file modifications: `git add -u` (excludes ?? files)
2. Check staged changes including any `A ` status files: `git diff --cached` and `git status --porcelain`
3. Analyze the diff to determine if multiple logical changes are present
4. If multiple distinct changes detected, suggest splitting into multiple commits
5. Create commit message using conventional commit format and commit all staged changes

**PROHIBITED OPERATIONS:**
- DO NOT modify file contents without asking user first
- If you think a file should be restored/modified during commit:
  1. Explain why the modification is needed
  2. Ask user for explicit permission
  3. Only proceed if user approves
- Use `git reset HEAD <file>` to unstage files (does not modify content)

**ALL git commands MUST be prefixed with `cd $ARGUMENTS &&` if $ARGUMENTS is a directory**

## Best Practices for Commits

- **Atomic commits**: Each commit should contain related changes that serve a single purpose
- **Split large changes**: If changes touch multiple concerns, split them into separate commits
- **Conventional commit format**: Use the format `[type] descr iption` where type is one of:
  - `[feat]`: A new feature
  - `[fix]`: A bug fix
  - `[docs]`: Documentation changes
  - `[style]`: Code style changes (formatting, etc)
  - `[refactor]`: Code changes that neither fix bugs nor add features
  - `[perf]`: Performance improvements
  - `[test]`: Adding or fixing tests
  - `[chore]`: Changes to the build process, tools, etc.
  - `[ci]`: CI/CD improvements
  - `[revert]`: Reverting changes
- **Present tense, imperative mood**: Write commit messages as commands (e.g., "add feature" not "added feature")
- **Concise first line**: Keep the first line under 72 characters

## Commit Type Reference

- `[feat]`: New feature
- `[fix]`: Bug fix
- `[docs]`: Documentation
- `[style]`: Formatting/style
- `[refactor]`: Code refactoring
- `[perf]`: Performance improvements
- `[test]`: Tests
- `[chore]`: Tooling, configuration
- `[ci]`: CI/CD improvements
- `[revert]`: Reverting changes

## Guidelines for Splitting Commits

When analyzing the diff, consider splitting commits based on these criteria:

1. **Different concerns**: Changes to unrelated parts of the codebase
2. **Different types of changes**: Mixing features, fixes, refactoring, etc.
3. **File patterns**: Changes to different types of files (e.g., source code vs documentation)
4. **Logical grouping**: Changes that would be easier to understand or review separately
5. **Size**: Very large changes that would be clearer if broken down

## Examples

Good commit messages:
- [feat] add user authentication system
- [fix] resolve memory leak in rendering process
- [docs] update API documentation with new endpoints
- [refactor] simplify error handling logic in parser
- [fix] resolve linter warnings in component files
- [chore] improve developer tooling setup process
- [feat] implement business logic for transaction validation
- [fix] address minor styling inconsistency in header
- [fix] patch critical security vulnerability in auth flow
- [style] reorganize component structure for better readability
- [refactor] remove deprecated legacy code
- [feat] add input validation for user registration form
- [fix] resolve failing CI pipeline tests
- [feat] implement analytics tracking for user engagement
- [fix] strengthen authentication password requirements
- [feat] improve form accessibility for screen readers

Example of splitting commits:
- First commit: [feat] add new solc version type definitions
- Second commit: [docs] update documentation for new solc versions
- Third commit: [chore] update package.json dependencies
- Fourth commit: [feat] add type definitions for new API endpoints
- Fifth commit: [feat] improve concurrency handling in worker threads
- Sixth commit: [fix] resolve linting issues in new code
- Seventh commit: [test] add unit tests for new solc version features
- Eighth commit: [fix] update dependencies with security vulnerabilities

## Important Notes

- **Git status codes**:
  - `??` = untracked files (DO NOT commit)
  - `A ` = staged new files (COMMIT if already staged by user)
  - `M ` = staged modifications (COMMIT)
  - ` M` = unstaged modifications (will be staged by git add -u)
- **Workflow**: `git add -u` stages tracked modifications, then commit all staged changes
- Commit message constructed based on detected changes
- Reviews diff to identify if multiple commits would be more appropriate
- Suggests splitting into multiple commits if needed

**Summary:**
- Files with `??` status → never touch, never commit
- Files with `A ` status (already staged) → commit together with modifications
- Files with `M ` or ` M` status → stage with `git add -u` and commit

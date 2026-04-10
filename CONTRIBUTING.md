# Contributing to NarraNexus

Thanks for your interest in contributing. This document covers the conventions we use for issues, pull requests, and commits.

---

## Reporting Issues

Before opening an issue, search the existing ones to make sure it hasn't been reported already.

We have two issue templates:

- **Bug Report** -- something is broken or behaving unexpectedly
- **Feature Request** -- a new capability or an improvement to an existing one

Pick the right template when you create an issue. Fill in every section -- incomplete reports take longer to triage.

### What makes a good bug report

- **Steps to reproduce** -- minimal, concrete steps. "It doesn't work" is not useful.
- **Expected vs actual behavior** -- what you thought would happen, and what happened instead.
- **Environment** -- Python version, OS, database type, which LLM provider you're using.
- **Logs/screenshots** -- paste the relevant traceback or attach a screenshot.

### What makes a good feature request

- **Problem statement** -- what pain point or limitation you're running into.
- **Proposed solution** -- how you think it should work. Doesn't need to be code-level; a clear description is enough.
- **Alternatives considered** -- anything else you thought about and why it's not ideal.

---

## Pull Requests

### Workflow

1. Fork the repo and create a branch from `main`.
2. Make your changes. Follow the code style described below.
3. If you added functionality, add or update relevant tests/scripts in `experiment_scripts/`.
4. Make sure the project imports cleanly:
   ```bash
   uv run python -c "import xyz_agent_context.module; import xyz_agent_context.narrative; import xyz_agent_context.services; print('OK')"
   ```
5. Open a PR against `main`.

### PR conventions

- **Title**: short (under 72 chars), imperative mood. Same format as a commit message header.
  - Good: `feat(job): add cron expression validation`
  - Bad: `Updated some stuff in jobs`
- **Description**: explain *what* changed and *why*. Link to the related issue if there is one (`Closes #123`).
- **Keep PRs focused**. One logical change per PR. If you're fixing a bug and also refactoring nearby code, split them.
- **No backward-compatibility hacks**. We don't maintain deprecated code paths right now (see project principles). If something changes, it changes cleanly.

### Review process

- At least one maintainer review is required before merge.
- CI checks must pass (import validation, lint if configured).
- Conversations should be resolved before merging.

---

## Commit Messages

We use [Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/).

### Format

```
<type>(<scope>): <description>

[optional body]

[optional footer(s)]
```

### Types

| Type | When to use |
|------|-------------|
| `feat` | New feature or capability |
| `fix` | Bug fix |
| `refactor` | Code restructuring without behavior change |
| `docs` | Documentation only |
| `test` | Adding or updating tests |
| `perf` | Performance improvement |
| `chore` | Build, CI, dependency updates, housekeeping |
| `style` | Formatting, whitespace, semicolons (no logic change) |

### Scope

Optional, but encouraged. Use the module or subsystem name:

```
feat(narrative): add topic-drift detection
fix(job): prevent duplicate cron triggers
refactor(module): extract instance lifecycle to separate method
docs(readme): add SQLite roadmap note
chore(deps): bump pydantic-settings to 2.5
```

### Rules

- **Subject line**: imperative mood, lowercase, no period at the end, max 72 characters.
- **Body**: wrap at 72 characters. Explain *what* and *why*, not *how*.
- **Breaking changes**: add `!` after type/scope and explain in the body or footer.
  ```
  feat(schema)!: rename agent_message.content to agent_message.body

  BREAKING CHANGE: all existing queries referencing the `content` column
  need to be updated to `body`.
  ```
- **One concern per commit**. Don't mix a feature and a refactor in the same commit.

---

## Code Style

### General

- Python 3.13+. Type hints where they add clarity (no need to annotate every local variable).
- All code comments and docstrings in **English** (CLAUDE.md ironclad rule #1: no Chinese in code). Docstrings follow the format in `CLAUDE.md`.
- No unnecessary abstractions. Three similar lines > a premature helper function.
- No backward-compatibility shims.

### Architecture rules

- **Modules don't import from each other.** Each module under `module/<name>_module/` is self-contained.
- **Private packages stay private.** `_module_impl/`, `_narrative_impl/`, `_event_impl/` are internal. Import from the public `module/` or `narrative/` package instead.
- **Configuration goes through `settings.py`.** No `os.getenv()` or `load_dotenv()` scattered around. Use `from xyz_agent_context.settings import settings` (lazy import in methods if needed to avoid circular deps).
- **Database access goes through `repository/`.** Table management scripts in `utils/database_table_management/` are standalone and should not be imported by application code.
- **Prompts stay generic.** No scenario-specific examples (e.g., sales, customer support) hard-coded in prompts. Agents define their own scenarios via Awareness.

### File headers

Every new Python file should have:

```python
"""
@file_name: xxx.py
@author: Your Name
@date: 2025-xx-xx
@description: What this file does (one line)

Extended description if needed...
"""
```

---

## Development Setup

```bash
# Clone and install
git clone https://github.com/your-org/narra-nexus.git
cd narra-nexus
uv sync

# Copy and configure environment
cp .env.example .env
# Edit .env with your API keys and database config

# Create database tables
uv run python src/xyz_agent_context/utils/database_table_management/create_all_tables.py

# Run the backend (4 processes)
uv run python src/xyz_agent_context/module/module_runner.py mcp       # MCP servers
uv run uvicorn backend.main:app --reload --port 8000    # API
uv run python -m xyz_agent_context.services.module_poller             # Instance poller
uv run python -m xyz_agent_context.module.job_module.job_trigger --interval 60  # Job scheduler

# Run the frontend
cd frontend && npm install && npm run dev
```

### Verify your changes

```bash
# Import check (catches circular imports, missing modules)
uv run python -c "import xyz_agent_context.module; import xyz_agent_context.narrative; import xyz_agent_context.services; print('OK')"

# Sync table schema if you modified any schema
cd src/xyz_agent_context/utils/database_table_management
uv run python sync_all_tables.py --dry-run
```

---

## Adding a New Module

See [README.md - Adding a New Module](./README.md#adding-a-new-module) for the step-by-step guide.

Short version:

1. Create `module/<name>_module/<name>_module.py`, subclass `XYZBaseModule`.
2. Register in `module/__init__.py` (`MODULE_MAP`).
3. Create table scripts in `utils/database_table_management/`.
4. Create a repository in `repository/` if needed.
5. Run the import check.

---

## Questions?

Open an issue with the **question** label, or start a Discussion if the repo has Discussions enabled.

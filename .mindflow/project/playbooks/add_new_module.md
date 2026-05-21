---
title: Add a new Module
audience: contributor (human or AI)
last_verified: 2026-05-18
---

# Playbook — Add a new Module

A Module is a hot-pluggable agent capability (Chat, Awareness, RAG,
Skill, Social Network, etc.). Adding one is the most common
"feature" contribution to NarraNexus. This playbook is the end-to-end
SOP — follow it in order, then check the final list to know you're done.

## When to read this

You want to add a new capability that:

- Has its own data (a table or files)
- Exposes tools to the agent via MCP
- Has a lifecycle (instances created, used, completed)

If you just want to add a tool to an existing module, edit that
module's `_<name>_impl/` directory — no playbook needed.

## Concrete example

We'll pretend you're adding `EmailModule` — sends and receives email.
Replace `email` / `EmailModule` with your actual name throughout.

## Prerequisites — read first

Before writing any code:

1. **`CLAUDE.md`** — sections `## 新建 Module 步骤` and `## 编码规范`.
   The MCP port table (`## MCP 端口分配`) tells you the next available
   port — pick the smallest unused one and use it consistently.
2. **`.mindflow/project/references/module_system.md`** — the deep
   reference. Module base class, Instance lifecycle, three-tier
   Prompts, how MCP tools get per-agent context.
3. **One existing module as a worked example.** Recommended:
   `src/xyz_agent_context/module/awareness_module/` — small, well-mirrored.

## Files you'll create

```
src/xyz_agent_context/
├── module/
│   └── email_module/                          # NEW
│       ├── __init__.py                        # NEW — re-exports
│       ├── email_module.py                    # NEW — class EmailModule(XYZBaseModule)
│       └── _email_impl/                       # NEW — private implementation
│           ├── __init__.py                    # NEW
│           └── <implementation files>.py      # NEW
├── repository/
│   └── email_repository.py                    # NEW (if you have a table)
└── schema/
    └── email_schema.py                        # NEW (Pydantic models)

.mindflow/mirror/src/xyz_agent_context/module/
└── email_module/                              # NEW — one md per .py
    ├── email_module.py.md
    └── _email_impl/
        └── <each .py>.md
```

## Files you'll edit

```
src/xyz_agent_context/module/__init__.py       # add EmailModule to MODULE_MAP
src/xyz_agent_context/utils/schema_registry.py # _register(TableDef(...)) for instance_email_messages
```

## Step-by-step

### 1. Subclass `XYZBaseModule`

`src/xyz_agent_context/module/email_module/email_module.py`:

```python
"""
@file_name: email_module.py
@author: Your Name
@date: 2026-MM-DD
@description: EmailModule — send and receive email on behalf of an agent.
"""
from xyz_agent_context.module.base import XYZBaseModule
from xyz_agent_context.module.config import ModuleConfig


class EmailModule(XYZBaseModule):
    @staticmethod
    def get_config() -> ModuleConfig:
        return ModuleConfig(
            name="EmailModule",          # must match class name + MODULE_MAP key
            priority=10,                 # 0 = Awareness; pick a free integer
            enabled=True,
            description="Send and receive email on behalf of an agent.",
            module_type="capability",    # or "task" if instances are LLM-spawned
        )
```

The instance ID prefix (`email_<random>`) is auto-derived from the
class name — don't set it manually.

### 2. Register in `MODULE_MAP`

`src/xyz_agent_context/module/__init__.py` — add the import and an
entry. Key must equal `EmailModule.get_config().name`.

### 3. Pick an MCP port (if your module exposes tools)

Check `CLAUDE.md`'s `## MCP 端口分配` table. The next available port
goes to your module. Record it in `module_runner.py`'s port map and
in your module's docstring so reviewers can verify.

### 4. Add a database table (if needed)

`src/xyz_agent_context/utils/schema_registry.py`:

```python
_register(TableDef(
    name="instance_email_messages",     # prefix MUST be instance_<name>
    columns=[
        Column("id", "INTEGER", "BIGINT UNSIGNED", nullable=False, primary_key=True, auto_increment=True),
        Column("instance_id", "TEXT", "VARCHAR(128)", nullable=False),
        Column("message_id", "TEXT", "VARCHAR(128)", nullable=False),
        # ... your fields ...
        Column("created_at", "TEXT", "DATETIME(6)", nullable=False, default="(datetime('now'))"),
        Column("updated_at", "TEXT", "DATETIME(6)", nullable=False, default="(datetime('now'))"),
    ],
    indexes=[Index("idx_email_messages_instance", ["instance_id"])],
))
```

Both `sqlite_type` AND `mysql_type` MUST be filled. `auto_migrate()`
will create the table on next process start — no manual migration
needed.

### 5. Add the repository

`src/xyz_agent_context/repository/email_repository.py` — subclass
`BaseRepository[EmailMessage]`. See `instance_repository.py` for
a pattern.

### 6. Implement the module logic

Inside `_email_impl/`. This is private — nothing outside the module
package should import from here.

### 7. Write the Tier-2 mirror md for every new `.py`

For each `.py` you created, create `.mindflow/mirror/<same path>.md`:

```markdown
---
code_file: src/xyz_agent_context/module/email_module/email_module.py
last_verified: 2026-MM-DD
stub: false
---

# email_module.py — EmailModule entry point

## Why this file exists
...

## Upstream / downstream
...

## Design decisions
...

## Gotchas
...
```

This is **binding rule #10** in `CLAUDE.md`. If you're short on time,
add `Mirror-md: needs-maintainer` to your PR and a maintainer will
write the mirrors for you.

### 8. Tests (optional but appreciated)

`tests/email_module/test_*.py`. We don't require 100% coverage on
new modules, but at minimum cover the happy path for any tool your
module exposes via MCP.

### 9. Local sanity check

```bash
make lint && make typecheck
make db-sync-dry             # verify your TableDef parses
make dev-mcp                 # confirm your module's MCP server starts on the new port
```

### 10. Open the PR

Title: `feat(email): add EmailModule for agent email send/receive`

Fill the PR template. The Tier-2 doc sync checkbox should be ticked
because you created mirror md files in step 7.

## Done checklist

- [ ] New `_email_impl/` directory, private — not imported outside the module
- [ ] `MODULE_MAP` entry added in `module/__init__.py`
- [ ] MCP port picked, added to `CLAUDE.md`'s port table in a follow-up PR (CLAUDE.md edits are owner-only — open a separate issue tagged `docs` asking the owner to add the port)
- [ ] `instance_email_messages` table registered in `schema_registry.py`
- [ ] `email_repository.py` with `BaseRepository` subclass
- [ ] Mirror md exists for every new `.py` file
- [ ] `make lint && make typecheck && make db-sync-dry` clean
- [ ] PR opened with proper Conventional Commit title

## Common mistakes

- **Importing another module** — modules cannot cross-import (binding
  rule #3). If `EmailModule` needs to talk to `ChatModule`, it goes
  through Service or Hook layers, not direct imports.
- **Writing to a table from outside `_email_impl/`** — repository is
  the only legal door. The service / hook layer calls repository.
- **Missing `mysql_type` in `Column(...)`** — `auto_migrate` will
  silently skip your column on MySQL. Both dialects always.
- **Forgetting the mirror md** — the CI bot will leave a friendly
  comment, but a missing mirror md is the most common review-bounce
  reason. Have your AI write it as the last step.

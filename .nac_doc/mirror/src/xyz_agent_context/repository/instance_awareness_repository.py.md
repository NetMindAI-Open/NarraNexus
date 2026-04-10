---
code_file: src/xyz_agent_context/repository/instance_awareness_repository.py
last_verified: 2026-04-10
stub: false
---

# instance_awareness_repository.py

## Why it exists

`InstanceAwarenessRepository` manages the `instance_awareness` table, which stores the natural language self-description ("awareness") of each `AwarenessModule` instance. The awareness is a freeform text blob written by the agent creator and read by the agent's system prompt to give the LLM its identity, personality, and operational context. Having a dedicated table (rather than embedding it in the module_instances row) allows the awareness text to be large and independently editable.

Notably, `InstanceAwareness` is defined as a Python `@dataclass` here rather than a Pydantic model — the only such case in the repository layer. This keeps it lightweight since it is a simple two-field entity.

## Upstream / Downstream

`AwarenessModule.hook_data_gathering()` calls `get_by_instance()` to read the awareness text into `ContextData`. The awareness management API route calls `upsert()` when the user edits the agent's self-description in the settings panel. The frontend awareness panel reads the text via `get_by_instance()` through the API.

## Design decisions

**`upsert()` as a check-then-insert-or-update pattern** (not the SQL `INSERT ... ON DUPLICATE KEY UPDATE`): awareness changes are infrequent and low-concurrency — there is no realistic scenario where two clients race to update the same agent's awareness simultaneously. The query-then-write pattern is acceptable here and avoids needing a unique index configured correctly.

**`id_field = "instance_id"`** (the awareness instance's ID, which is also the primary key of the `instance_awareness` table): this means `BaseRepository.get_by_id()` works correctly. However `upsert()` does not use the base class `upsert()` (which requires a unique DB constraint) — it uses its own logic.

**Verbose `info`-level logging in `upsert()`**: the upsert method logs at `logger.info` level (not `debug`) including content previews and row counts. This was added during debugging of an awareness-not-saving issue and was never dialed back to debug level. In high-frequency usage this would be noisy, but awareness updates are rare.

## Gotchas

**`upsert()` returns `bool`, not the number of affected rows**: it returns `True` on success and `False` on exception. This differs from the base class `upsert()` which returns an integer row count. Callers that expect an integer will silently treat `True` as `1` (which happens to be correct in Python), but the semantics are different.

**`update_awareness()` is an alias for `update()` from the base class**: it just calls `self.update(instance_id, {"awareness": awareness})`. Having a named method makes the intent clearer but it is functionally identical to calling the base class method directly.

## New-joiner traps

- There is one awareness record per `AwarenessModule` instance, not one per agent. If an agent has multiple `AwarenessModule` instances (unusual but possible in theory), each has its own awareness text. In practice every agent has exactly one.
- The `InstanceAwareness` dataclass defined in this file is not in `schema/` — it lives in the repository file itself. This is an exception to the pattern of keeping all data models in `schema/`. If you need to import `InstanceAwareness`, import it from `xyz_agent_context.repository.instance_awareness_repository`.

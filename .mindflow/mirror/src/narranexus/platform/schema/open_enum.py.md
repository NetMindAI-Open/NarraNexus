---
code_file: src/narranexus/platform/schema/open_enum.py
last_verified: 2026-09-04
stub: false
---

# schema/open_enum.py — OpenStrEnum

## Intent

`WorkingSource` and `TriggerType` label a turn by the surface that started it. Their core members are fixed platform vocabulary, but IM channels are plugins (builtin ones included since batch 4e), so the platform must not carry a table of channel names inside either enum. `OpenStrEnum` is the shared base: an Enum-shaped `str` (value/name, `Cls("job")`, iteration, membership, pickle, pydantic/JSON) whose channel members are added after import by `register()`. Each subclass gets its own member tables (`__init_subclass__`), so registering a channel into one does not leak into the other — `WorkingSource.register` explicitly registers the `TriggerType` twin.

## Design decisions

- **Unknown attribute is a loud runtime error, a quiet static one.** The metaclass `__getattr__` raises an `AttributeError` that names the fix (register from the descriptor), while typed as `Any` so pyright accepts `WorkingSource.LARK` in channel code without the platform declaring `LARK`. The core members are still declared on the subclasses for type checkers.
- **Registration is idempotent and cannot redefine a core name** — the descriptor, `contributions.register_all` and the data-access seam all register the same value.

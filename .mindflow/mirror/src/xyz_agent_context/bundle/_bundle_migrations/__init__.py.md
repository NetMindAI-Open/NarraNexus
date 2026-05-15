---
code_file: src/xyz_agent_context/bundle/_bundle_migrations/__init__.py
last_verified: 2026-05-08
stub: false
---

# _bundle_migrations/__init__.py — Bundle format migration scaffolding (PRD §8.6)

## 为什么存在

When the on-disk `.nxbundle` format changes incompatibly (new major
version), older bundles must still be importable on newer NarraNexus
instances. This package holds the migration chain.

Today there's only one major (v1) so `MIGRATIONS` is empty. The
scaffolding ships now so future-us doesn't reinvent the wheel.

## 上下游

- **Caller**: `bundle.importer.preflight` calls `apply_migrations(work_dir, manifest)`
  before any name-clash / embedding-compat / write logic, so all
  downstream code can assume manifest is at `CURRENT_BUNDLE_MAJOR`.
- **Migration files**: each new major bump drops a sibling module
  (e.g. `v1_to_v2.py`) and registers it in `MIGRATIONS[(1, 2)]`.

## Contract for a migration callable

```python
async def migrate_v1_to_v2(work_dir: Path, manifest: dict) -> dict:
    # MUST mutate the extracted tree under work_dir as needed
    # MUST return a manifest dict whose bundle_format_version reflects v2
    ...
```

`apply_migrations` chains them step-by-step. If a chain link is missing
it raises `ValueError("No bundle migration registered for X → Y")`.

## Gotcha

- Migrations should be **idempotent** when possible: running v1→v2 on
  an already-v2 bundle should be a no-op (defensive).
- Migrations may need to mutate disk too, not just the dict (e.g. rename
  a folder under `work_dir/agents/`); use `Path` operations.

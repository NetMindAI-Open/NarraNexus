# Breaking Changes

This document tracks breaking changes across releases.
If you are upgrading from a previous version, check the entries below for migration steps.

---

## 2026-02-26

### Workspace directory renamed: `agent-workspace` → `agent_workspace`

The default workspace path changed from `./agent-workspace` (hyphen) to `./agent_workspace` (underscore) to align with Python naming conventions.

**Who is affected:** Anyone using the default `BASE_WORKING_PATH` without an explicit override in `.env`.

**How to fix:**

```bash
# Option A: Rename the directory (recommended — preserves existing agent data)
mv agent-workspace agent_workspace

# Option B: Set the old path explicitly in your .env file
echo 'BASE_WORKING_PATH="./agent-workspace"' >> .env
```

### Removed frontend components

The following components were removed as part of a UI cleanup:

- `frontend/src/components/layout/MainLayout.tsx`
- `frontend/src/components/ui/Badge.tsx`
- `frontend/src/components/ui/Card.tsx`
- `frontend/src/pages/ParticleBackground.tsx`

**Who is affected:** Anyone who imported these components in custom code outside the standard frontend.

**How to fix:** Remove the imports. `MainLayout` was unused. `Badge` and `Card` were replaced by inline Tailwind styles. `ParticleBackground` was removed from the login page.

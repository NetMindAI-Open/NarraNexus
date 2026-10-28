---
code_file: backend/onboarding/__init__.py
last_verified: 2026-08-19
stub: false
---

# __init__.py — deliberately import-free package marker

## Why it exists (and why it is empty)

Consumers import their module directly — the login hooks use
`backend.onboarding.provisioning`, whose import block also registers the
"onboarding" bootstrap profile (side effect). Keeping the package `__init__`
import-free leaves exactly ONE registration point on the production path,
instead of two import paths (package vs module) whose registration timing a
future reader would have to reason about. (Historically it also kept
`naming.py` import-cheap for Arena; naming moved to `backend/naming.py` on
2026-08-19, but the single-registration-point rationale stands on its own.)

## Gotchas

- **The profile registration lives at provisioning.py's import block**
  (`import backend.onboarding.profile  # noqa`), NOT here. Removing that
  import raises nothing: `get_profile("onboarding")` silently falls back to
  the default profile and every guide agent renders the generic blank-slate
  first-run. Two tests pin it:
  `test_onboarding_provisioning.py::test_importing_provisioning_registers_the_profile`
  and the integration test's bilingual-greeting assertion.

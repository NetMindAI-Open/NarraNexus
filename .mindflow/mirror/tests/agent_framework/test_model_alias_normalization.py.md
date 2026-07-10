---
code_file: tests/agent_framework/test_model_alias_normalization.py
last_verified: 2026-07-03
---

# test_model_alias_normalization.py — upstream #57 guard

Locks the transport-boundary rule: CLI family aliases resolve to full ids
on every non-OAuth transport and stay verbatim on OAuth; map targets must
be registered catalog models (typo guard); `to_cli_env` output follows the
same rule. See model_catalog.py.md 2026-07-03 entry.

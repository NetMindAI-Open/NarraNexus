---
code_file: src/narranexus/cli/bindings_cli.py
last_verified: 2026-09-07
stub: false
---

# cli/bindings_cli.py — slots / bind / unbind

`booted_registries()` boots builtins + the user registry on private registries and resolves bindings; `render_catalog` prints the catalog; `bind(regs, path, slot, providers)` validates (slot exists, not distribution-only, providers registered, arity) and rewrites only the `[bindings]` table (undone if the file then fails to resolve); `unbind`; `read/write_bindings_table` keep other tables verbatim.

## 2026-09-07 — read-only boot; re-validation against the booted tree

booted_registries() boots with inspect=True (no marker, no writes). bind() re-resolves the written file against regs.slots (the booted tree, user-declared slots included) non-strictly — the builtin-only tree it used to rebuild rejected every user-plugin slot and, once one such binding existed in narranexus.toml, every later bind. Candidate validation goes through referenced_plugins.

## 2026-09-07 — booted_registries relies on boot alone

register_all is gone.

## 2026-09-07 — bind 逐 token 校验（round-2 K2-I7）

_names_a_candidate：owner / name / owner:name 每个都必须命中已注册条目，空 verb 拒绝；旧实现只查 owner 存在，owner:不存在的名字会被写进 toml 然后静默匹配不到任何东西。_KEY_RE 死代码删除。

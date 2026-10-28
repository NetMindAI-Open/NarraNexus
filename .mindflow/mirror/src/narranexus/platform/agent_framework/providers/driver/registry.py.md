---
code_file: src/narranexus/platform/agent_framework/providers/driver/registry.py
last_verified: 2026-09-07
stub: false
---

# registry.py — driver_type → class lookup over the `model.providers` slot

**There is no `DRIVER_REGISTRY` constant.** Entries live in the kernel's `model.providers` registry
and are reached through the ``driver_registry()`` ACCESSOR, resolved at call time — a module-level
constant bound to the process registries made a private ``Registries()`` in a test invisible to this
module. Nothing is registered at import: ``@register`` attaches the driver's ``Contribution`` to the
class (the symbol ``builtin.providers``' manifest names) and the host boot registers it;
``register_driver(cls, owner=...)`` is the explicit path for a test or an embedding host. The
resolver consults it via ``get_driver_class(driver_type)``;
unknown keys return ``None`` and the resolver raises
``LLMConfigNotConfigured`` — that's intentionally loud so a misconfigured
row never silently routes to a default that bills the wrong account.

Re-registering the same class is a no-op (idempotent). Re-registering
a different class under the same key logs a warning and overwrites —
this only triggers under test fixtures that monkeypatch drivers; in
production every driver registers exactly once.

``SystemDriver`` doesn't use the decorator directly; instead its module
calls ``register(SystemDriver)`` inside an ``if is_cloud_mode():``
block. Local installs never see it in the registry, which means a
``driver_type='system_pool'`` row on a local DB raises the loud error
above instead of half-working.

## 2026-09-03 — `@register` 生成 `Contribution` 并挂到类上；注册表来自内核门面

`DRIVER_REGISTRY` 现在是 `KERNEL_REGISTRIES.registry_for("model.providers")`。`register` 把
`Contribution(driver_type, lambda: cls)` 存到 `cls.contribution`，各 driver 模块以
`CONTRIBUTION = <Cls>.contribution` 暴露给 `builtin.providers` manifest；`system.py` 给
`CONTRIBUTIONS`（本地空元组）。同类重复注册仍 no-op、异类覆盖仍 warning。

## 2026-09-03 — `DRIVER_REGISTRY` 变成内核 `Registry[Type]`（`model.providers` 位）

`@register` 语义逐字保留（同类 no-op、异类覆盖并 warning），底层从 dict 换成
`narranexus.kernel.plugins.registry.Registry`，工厂返回类本身。`get_driver_class` 走 `try_get`。
`"x" in DRIVER_REGISTRY` 仍成立（Registry 实现 `__contains__`），但 `DRIVER_REGISTRY["x"]` 不再
支持——唯一的调用方 `tests/agent_framework/test_codex_oauth_driver.py` 已改用 `get_driver_class`
（rule 2 不留兼容垫片）。`register` 多了 keyword-only `owner`（默认 `builtin.providers`）。

## 2026-09-04 · lazy builtin drivers (batch 6b.2)

`ensure_builtin_drivers()` registers the manifest-named drivers on the first `get_driver_class`; the driver implementations are the `builtin.providers` plugin (`narranexus_plugins.providers`).

## 2026-09-07 — @register attaches, register_driver registers; driver_registry() at call time

The class decorator only attaches the driver's Contribution (the symbol the manifest names) — no import-time registry write; register_driver(cls, owner=) is the explicit path for tests / an embedding host. ensure_builtin_drivers is gone: the host boot populates model.providers from builtin.providers' manifest.

## 2026-09-07（round-2 T2-C6）— the historical notes below use the retired name

The three 2026-09-03/04 sections above talk about `DRIVER_REGISTRY` and `ensure_builtin_drivers` in
the present tense; both are gone (`9314cb77b`). They are kept as the record of HOW the module got
here — read them as history, and read the header for what is true today.

---
code_file: src/narranexus/platform/utils/plugin_services.py
last_verified: 2026-09-10
stub: false
---

## 2026-09-10（review r2 I-B）— `try_job_resume_for_principal()`

与 `try_job_run_once` 同形：`try_require(JOB_RESUME_FOR_PRINCIPAL)`，builtin.job 关闭时返回 None。唯一调用方是
`backend/routes/admin/suspend.py` 的 reinstate（恢复封号暂停的 job）。


## 2026-09-07 — 文档改回事实：服务由 boot 注册，没 boot 就响亮地报错

删掉 `_services()` 里那句 `import narranexus.platform.module_system  # noqa: F401 —
registers the builtins' services (idempotent)`，并重写 docstring 里那句「它们先 import
`module_system`，这样 builtin 的 `register_all` 无论 import 顺序如何都已经跑过」——
`register_all` 全仓零命中。

现在的表述：注册只发生在 host boot；没 boot 的进程 locator 是空的，而这个情况**故意**与
「拥有该服务的 builtin 被禁用」不可区分——两者都从 `require` 抛 `UnknownEntry`，响亮且落
在调用点。有降级路径的调用方用 `try_*`，拿到 `None`。

# utils/plugin_services.py — platform accessors for builtin services

## Intent

Thin wrappers over `KERNEL_REGISTRIES.services` + `narranexus.contracts.services` (`skill_workspace`, `job_instances`, `try_job_run_once`). They import `narranexus.platform.module_system` first so the builtins' `register_all` has run whatever the import order; `require` fails loud (UnknownEntry) when the owning builtin is disabled, `try_*` returns None for callers with a degraded path (Manyfold run-job → `jobs_unavailable`).

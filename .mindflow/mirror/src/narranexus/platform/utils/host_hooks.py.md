---
code_file: src/narranexus/platform/utils/host_hooks.py
last_verified: 2026-09-07
stub: false
---

## 2026-09-07 — 文档改回事实：没有 import 期注册这回事

原来的模块 docstring 写着 builtin 的 hook「由 `module/contributions.register_all` 在
import 时注册」，并说本文件的存在意义就是「保证这些注册在本进程里一定存在，与 import 顺
序无关」。`register_all` 已经被删掉了，随之而来的那个 `import
narranexus.platform.module_system  # noqa: F401` 什么也不注册——两句话都在承诺代码已经不
提供的东西（铁律 #10 意义上的阻塞级失同步）。

现在写的是真的：**只有 boot 会注册**。没 boot 的进程里 hook 注册表是空的，
`call_host_hook` 跑零个实现、返回空 `HookOutcome`，**不报错**——因为这跟「本发行版把这个
事件的所有实现都禁掉了」是同一个答案，advisory 事件的调用方本来就得容忍。这一条被
`tests/channel/test_unbooted_process_fails_loud.py` 明确钉成「故意不改成抛异常」，免得下一
个人把它「修」成 fail-loud 从而改变所有 host 事件的失败时机。真正不能沉默的查询（渠道描
述符、具名服务）走的是另外的路径，它们抛 `UnknownChannel` / `UnknownEntry`。

# utils/host_hooks.py — fire a host event from platform code

## Intent

`call_host_hook(name, **payload)` runs every `backend.hooks` implementation of a host event on the kernel registries (importing `narranexus.platform.module_system` first so builtin hooks exist regardless of import order). Used by the rename transaction, the bundle importer, the Manyfold sync route and `backend/host_events`. Results come back in registration order; errors per owner, never raised.

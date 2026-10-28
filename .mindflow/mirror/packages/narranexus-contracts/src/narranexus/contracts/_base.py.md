---
code_file: packages/narranexus-contracts/src/narranexus/contracts/_base.py
last_verified: 2026-09-07
stub: false
---

## 2026-09-03 — 所有契约共用的原语：Disposable、取消信号、错误层级

三样东西，全部 stdlib-only 以保证 contracts 是叶子包：

- **`Disposable` / `DisposableStack`**（宪章 15「Disposable 一切」，VS Code / Obsidian 先例）：每个
  `register`/`subscribe` 返回一个幂等的释放句柄；`DisposableStack` LIFO 释放并用 `ExceptionGroup`
  聚合失败——**绝不因为一个释放失败而漏掉其余释放**。向已释放的栈 `add` 会立即释放该项，
  防止关停期间的晚到注册泄漏。
- **`CancellationSignal`** 结构化协议（`requested() -> bool`）：在契约层重新声明而不是 import 运行时
  的 token 类，与 NexusPower `contracts/protocols.py` 同一做法。
- **错误层级**：`PluginError` 根；`UnknownEntry` 同时是 `KeyError`（遗留 `memory.spec.get_spec`
  抛 KeyError 的调用方不用改），并覆写 `__str__` 去掉 KeyError 的引号包裹；`ManifestError`
  同时是 `ValueError`。「把错误定义出存在之外」（宪章 4）的另一半在各注册表：重名在启动期炸。

## 2026-09-07 — plugin_id_slug（round-2 K2-I3）

唯一的 id→标识符扁平化函数，table_prefix_for/env_name 共用；文档说明它不是单射，唯一性由安装器与合成包守卫保证。

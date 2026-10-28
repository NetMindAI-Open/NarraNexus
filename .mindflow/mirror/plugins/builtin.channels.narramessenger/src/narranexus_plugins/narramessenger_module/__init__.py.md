---
code_file: plugins/builtin.channels.narramessenger/src/narranexus_plugins/narramessenger_module/__init__.py
last_verified: 2026-09-07
stub: false
---

## 2026-09-07 — WorkingSource 注册改成一次显式调用

原来是 `from .descriptor import DESCRIPTOR as _DESCRIPTOR  # noqa: F401 — registers this
channel's WorkingSource`，靠 descriptor.py 顶层 `SOURCE = WorkingSource.register(...)` 的
副作用。那行副作用已经删掉了，所以这里改成显式的
`register_working_source(DESCRIPTOR)`。

它必须留在这里，而且理由很具体：本包下的模块/trigger 类体里有
`working_source = WorkingSource.NARRAMESSENGER`，那是**类定义时**求值的属性访问，开放枚举里
没有这个成员就直接 AttributeError。所以这个包为**自己**保证前置条件，调的还是 boot 路径
（`contributions_from`）调的同一个幂等函数。

它不再是「随便谁 import 到就注册」的全局副作用：被禁用/被排除的渠道，boot 根本不会 import
它的 contribution，模块类也永远不会被惰性解析，这个包因此不会被 import。除此之外本包
import 期什么也不注册——消息来源由注册表视图从 `DESCRIPTOR` 投影，不在这里注册。

# plugins/builtin.channels.narramessenger/src/narranexus_plugins/narramessenger_module/__init__.py — package marker

package marker of the NarraMessenger channel plugin. No code belongs here: the host boots plugins from their manifests, and an import-time registration would break the lazy-contribution rule and the distribution excludes.

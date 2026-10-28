---
code_file: src/narranexus/kernel/plugins/importer.py
last_verified: 2026-09-07
stub: false
---

## 2026-09-03（批 2b.2）— 隔离导入：`nxplugins.<id>` 合成包 + 每插件私有依赖 finder

插件代码永不进 `sys.path`。一个 `PluginFinder` 插在 `sys.meta_path[0]`，干两件事：`nxplugins.<id>` 解析为
`<backend>/__init__.py` 的 spec（`submodule_search_locations=[backend]`，子模块经包 `__path__` 解析，两个插件
各自的 `utils` 互不干扰）；插件私有依赖只对「导入方属于该插件」的请求、且宿主自己 import 不到该名时才从
deps 目录提供（宿主优先——HA 的 site-packages 互踩教训）。首版把空模块直接塞进 `sys.modules` 导致插件
`__init__` 永不执行，已改为 finder 惰性提供 spec。`import_plugin_module` 在线程池里带超时执行 import，
挂住的 import 被隔离成 `PluginError`。不是独立解释器：已导入的私有依赖照常缓存在 `sys.modules`，
保证的是「不遮蔽宿主、不污染 sys.path」。

## 2026-09-07 — one daemon thread per import; wedged plugins fail fast; PluginImportTimeout

The two-worker pool could be wedged by two hung imports (a future cannot be cancelled), after which every later import timed out and innocent plugins were auto-disabled. Each import now runs on its own daemon thread; a plugin whose import exceeded the deadline is remembered in _WEDGED and refused immediately afterwards; the timeout is its own PluginImportTimeout so boot records it as slow, not crashed. Only ever called from the boot/activation top level (a worker importing back into a module mid-import on the caller's thread would deadlock on the import lock).

## 2026-09-07 — 重复 import 清理（round-2 K2-M2）

threading/typing 重复导入合并。

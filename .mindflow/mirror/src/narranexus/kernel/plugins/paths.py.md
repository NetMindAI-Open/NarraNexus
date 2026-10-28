---
code_file: src/narranexus/kernel/plugins/paths.py
last_verified: 2026-09-03
stub: false
---

## 2026-09-03（批 2b.1）— 用户插件的磁盘布局（纯路径运算）

`~/.narranexus/plugins/` 一棵树：`registry.json` / `registry.lkg.json` / `.booting-<role>` / 各插件目录
（copy 模式，`<publisher>.<name>` 必含点，永不与 `nodejs`/`pyenv` 撞名）；link 模式插件在原地，依赖在
`~/.narranexus/plugin-deps/<id>`。`NARRANEXUS_PLUGIN_HOME` 覆盖整棵树（测试与迁移用）。遗留
`agent_framework/plugin_paths.plugin_home()` 改为委托这里，框架安装器的 `nodejs/pyenv` 子树不动。

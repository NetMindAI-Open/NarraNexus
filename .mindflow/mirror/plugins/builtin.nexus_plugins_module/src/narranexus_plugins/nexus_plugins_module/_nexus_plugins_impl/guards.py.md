---
code_file: plugins/builtin.nexus_plugins_module/src/narranexus_plugins/nexus_plugins_module/_nexus_plugins_impl/guards.py
last_verified: 2026-09-07
stub: false
---

## 2026-09-03（批 2f.1）— 最小护栏集（spec §11.4）为纯函数

id（`<publisher>.<name>`、禁 `builtin.`、禁 protected）、kinds（模板白名单）、路径（必须在
`<workspace>/plugins/<id>` 内；解析已存在前缀跟随 symlink，再拼未存在的尾部，越界/逃逸即拒）、扩展名白名单、
单文件 512 KiB、每 10 分钟窗口 3 次 register/activate、连续两次 rollback 转人工。首版 `check_inside` 在
`plugins/` 目录尚不存在时误判越界（测试实锤），已改为先解析存在前缀再拼尾部。

## 2026-09-07 — is_builtin_id 收编（round-2 P2-I6）

『是否 builtin』只在 contracts.distribution.is_builtin_id 一处判断（BUILTIN_PREFIX 同处）；九处 startswith('builtin.') 副本全部改调它（distribution_scaffold 的保留命名空间检查是另一个判断，未合并）。

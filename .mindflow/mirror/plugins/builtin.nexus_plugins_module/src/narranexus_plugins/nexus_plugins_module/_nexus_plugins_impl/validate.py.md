---
code_file: plugins/builtin.nexus_plugins_module/src/narranexus_plugins/nexus_plugins_module/_nexus_plugins_impl/validate.py
last_verified: 2026-09-04
stub: false
---

## 2026-09-04（批 3b）— manifest 校验改用 `slot_tree_with_builtins()`（含内置声明的位）

## 2026-09-03（批 2f.1）— `plugin_validate`：manifest/契约/依赖/体积 + 「声明 vs 实际」静态扫描

正则扫 py/ts/js：出网（httpx/requests/urllib/aiohttp/socket/fetch）、子进程、`os.environ`、绝对路径文件访问；
命中而 manifest 没声明对应 permission 即 mismatch（用户必须明示接受，spec §10.4）。保守：只能报有、不能证无。
`tree_hash` 排除 pyenv/缓存/`.test-home`/`.test-report.json`/`.plugin-changelog.jsonl`——否则跑测试本身就
改变哈希，register 永远说「文件变了」（实锤）。

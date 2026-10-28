---
code_file: packages/narranexus-contracts/src/narranexus/contracts/settings.py
last_verified: 2026-09-07
stub: false
---

## 2026-09-03（批 2a）— `backend.settings` 位的契约：JSON-Schema 子集

五种类型（string/integer/number/boolean/enum），`secret` 只允许 string（宿主经 secret_box 加密且不回显），
enum 必须给 choices，default 在构造时就用 `coerce` 校验。`coerce` 也是 env 覆盖（`NXP_<ID>_<KEY>`，
字符串）到类型值的唯一转换点，布尔接受 1/true/yes/on。key 限 `[a-z][a-z0-9_]{0,63}`。

## 2026-09-07 — env names are NXP_<ID>__<KEY>

The double underscore between the flattened plugin id and the key makes a.b/c_d and a.b_c/d distinct environment variables.

## 2026-09-07 — env_name 用 plugin_id_slug（round-2）

与表前缀同一扁平化函数。

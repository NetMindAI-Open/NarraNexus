---
code_file: src/narranexus/cli/publish_check.py
last_verified: 2026-09-04
stub: false
---

## 2026-09-04（批 3b）— manifest 校验改用 `slot_tree_with_builtins()`（含内置声明的位）

## 2026-09-03（批 2e）— 发布清单

bronze：manifest 合法、非 builtin 前缀、有 description/license/README、backend 包有 `__init__`、前端入口已
构建且有 integrity；silver+：versions.json 含当前版本、CHANGELOG、声明 permissions、tests。返回问题列表，
CLI 据此给退出码。

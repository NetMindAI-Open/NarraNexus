---
code_file: frontend/src/lib/bootstrapGreeting.ts
last_verified: 2026-09-08
stub: false
---

# bootstrapGreeting.ts — 首程问候本地化

## 2026-09-08 — 新建

后端持久化两种系统问候（通用版、带名字版）的英文原文；这里按英文模板精确匹配后翻译（带名字版用 `{{name}}` 模板转正则抠出名字），场景自定义问候原样透传。`ChatPanel` 虚拟问候与历史行都经此。

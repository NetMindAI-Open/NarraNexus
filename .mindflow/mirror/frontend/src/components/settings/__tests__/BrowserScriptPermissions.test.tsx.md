---
code_file: frontend/src/components/settings/__tests__/BrowserScriptPermissions.test.tsx
last_verified: 2026-09-23
stub: false
---

# 脚本权限回归

界面不再出现网址访问或例外规则。独立验证脚本的明确保存、撤销、失败重试、
请求互斥、切换 agent 后丢弃旧响应，以及新增 origin 仅创建草稿、不立即放开脚本。

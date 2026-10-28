---
code_file: frontend/src/lib/__tests__/no-unauthenticated-api-fetch.test.ts
last_verified: 2026-09-23
stub: false
---

# API 身份接线检查

浏览器设置曾使用裸 fetch，401 被误呈现为“未安装”，且安装按钮无效。组件测试中
替换请求很容易掩盖此问题，因此源码检查要求业务组件统一经过带身份的 API 客户端。
例外必须写明理由；这个轻量扫描并不证明任意动态 URL 的鉴权正确性。

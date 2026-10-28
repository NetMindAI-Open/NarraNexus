---
code_file: frontend/src/components/settings/__tests__/BrowserSettings.policy.test.tsx
last_verified: 2026-09-23
stub: false
---

# 高级脚本设置生产宿主

真实 BrowserSettings 和 store 验证只切换管理对象而不改变当前聊天。页面没有网站访问、允许、
禁止和例外规则；仅 full_cdp_access 能通过共享认证 API 修改。错误 agent 响应不会展示。

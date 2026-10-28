---
code_file: tests/module/test_browser_module_integration.py
last_verified: 2026-10-08
stub: false
---

## 2026-10-08（PR #410 第二轮 review）

新增：工具说明里的参数上限（字符数）必须等于 `limits` 常量，且不出现 KiB。

## 2026-10-08（PR #410 review）

接口改为 `/api/browser/notices`；去掉 request_approval / 审批表断言。

## 2026-09-23 多页面支持

browser_tabs 的列举、选择、关闭和 new_page 参数复用同一 MCP 可信调用 scope，
确保新增工具不会越过缓存会话上的身份绑定。

# 浏览器 MCP 端到端回归

测试跨 ContextRuntime、MCP 身份包装、BrowserService、真实 Session、HTTP 路由和 ArtifactService。
空策略、无记录和历史 deny/ask/会话拒绝均可导航、读、固定操作；每次调用保留可信审计 scope。
任意脚本默认拒绝，保存及撤销后复用会话读取最新策略。截图跨会话成功，非 HTTP(S) 截图不落盘。
登录通知在另一服务实例可见，只有实际人工交还才继续读取，状态仍 unverified；跨站不会再问授权。
浏览器协议使用替身，持久化用隔离 SQLite；真实 Chrome 的运行和画面由 live 测试验证。
错误返回、参数透传、分页说明、插件禁用和取消传播保留原有覆盖。

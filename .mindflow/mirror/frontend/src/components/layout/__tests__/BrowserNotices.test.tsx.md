---
code_file: frontend/src/components/layout/__tests__/BrowserNotices.test.tsx
last_verified: 2026-10-14
stub: false
---

# 登录通知送达

钉住：轮询当前 agent、无请求不渲染、无 agent 不轮询；打开浏览器但不接管；接管期间仍可见、服务端
移除后才消失；别的 agent 的条目不显示；上一个 agent 迟到的响应/错误不能覆盖当前；切换 agent 立即
隐藏；慢轮询不重叠、卸载后停止；轮询失败清掉旧通知并可手动重试；禁用浏览器插件即停止轮询。

## 2026-10-14 — boot gate 与 404 语义

另钉住四条（加上第 5 条）：首个 plugin boot pass 未 settle 前不轮询；发行版排除 browser（factory 列表 `excluded: true`）时永不轮询、
面板被移除；`/api/browser/notices` 返回 404（`ApiError.status`，不看文案）= 功能缺失→无 banner 且停止轮询；5xx 仍是可见、
会重试的失败；后续 `loadPlugins` pass（登录翻转）不会卸载已显示的通知（门是单调的 `bootEverSettled`，不是逐 pass 复位的
`pluginsBootSettled`）。

Harness 规则：组件在首个 boot pass settle 前不渲染轮询，所以文件顶部 `beforeEach` 先 settle 一次（`loadPlugins` 配一个失败的
fetch 即可），任何新增的渲染用例都靠它。`bootEverSettled` 是单调的、按测试文件隔离的模块状态，所以断言「首个 pass 之前」的那条
用例必须是文件里第一个用例，并由名字里的 `first-pass` 标记跳过 `beforeEach` 的 settle。

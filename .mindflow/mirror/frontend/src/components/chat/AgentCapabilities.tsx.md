---
code_file: frontend/src/components/chat/AgentCapabilities.tsx
last_verified: 2026-09-07
stub: false
---

## 2026-09-07（四轮复审）— 文件改名 `AgentCapabilities.tsx`；删对话框壳；「恢复默认」控件

`AgentCapabilitiesPanel`（Dialog 形态）全仓无 import、无测试，按铁律 #2 删除；本组件只剩可内嵌的 `AgentCapabilities`。
显式切换过的行（`explicit`）多一个「恢复默认」按钮，调 `api.resetAgentCapability`（DELETE 路由）——那条路由此前没有任何前端入口。
locale：删 `chat.header.capabilities`（头部入口已随 dev #383 取消）与 `chat.capabilities.title`（对话框标题），加 `chat.capabilities.restoreDefault`。
`__tests__/AgentCapabilities.test.tsx`：锁定行禁用、切换写 API、恢复默认调 DELETE、超预算文案。

# chat/AgentCapabilitiesPanel.tsx — per-agent capability switches

## Intent

The owner's view of `/api/agents/{id}/capabilities` (plugin platform batch 5c): every registered module with its icon, description, builtin/plugin badge and a switch; base modules show a lock. Each toggle writes immediately and reloads; the budget line compares the enabled set's declared prompt cost with the builtin baseline and turns amber past 2×. Opened from the chat header's detail menu next to "Model & framework" (`ChatHeader.onOpenCapabilities`), mounted by `ChatPanel`. Strings under `chat.capabilities.*` in all ten locales.

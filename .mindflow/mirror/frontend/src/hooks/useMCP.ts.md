---
code_file: frontend/src/hooks/useMCP.ts
last_verified: 2026-08-25
stub: false
---

# useMCP.ts — MCP 列表只读查询

## 为什么存在

`AgentOverviewCard` 需要展示当前 agent 的 MCP 连接摘要,但今天唯一读取
MCP 列表的地方是 `MCPManager.tsx` 里自带的 `useState`+`useEffect` 本地
抓取,那套逻辑跟它自己的增删改 + `validateAllMCPs` 轮询绑在一起,直接
复用等于把摘要卡也拖进 mutation 依赖。这个 hook 是故意开的第二条只读
路径,形态照抄 `useSkillsList`(`hooks/useSkills.ts`):同样从
`useConfigStore()` 读全局 `agentId`/`userId`,同样是 TanStack Query 包
一层 `api.listMCPs`。`MCPManager.tsx` 不受影响,继续用自己的抓取逻辑
管理增删改。

## 数据与性能边界

`enabled: !!agentId && !!userId`,跟 `useSkillsList` 一致——agent 未选中
时不发请求。没有 mutation,纯读;缓存 key 是
`['mcp-list', agentId, userId]`,agent 切换时自动重新拉取。

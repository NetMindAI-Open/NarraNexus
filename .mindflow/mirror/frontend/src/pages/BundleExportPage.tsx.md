---
code_file: frontend/src/pages/BundleExportPage.tsx
last_verified: 2026-07-13
stub: false
---

## 2026-07-13 — full-mode checkbox also carries skill secrets

The existing 'include credentials' checkbox now sends both `include_channel_credentials` and `include_skill_secrets` (one 'full mode' opt-in), and the warning text covers skill secrets (env_config + skill credential files).

## 2026-07-13 — opt-in channel-credential export

Added an opt-in `include_channel_credentials` checkbox + a strong plaintext-secret warning. Off by default; when on, the request ships IM channel credentials so a migrated agent's channels work without re-binding.

# BundleExportPage.tsx — Export wizard (subproject 2 §8.13)

7 tabs（Agents / Chat history / Skills & MCP / Social Network / Message Bus / Artifacts / Workspace files）+ Bundle Notes editor + Review Summary modal。

## Tab data flow

1. **Agents tab**：选 agent_ids + （可选）team。其他 tab 的内容范围跟随这里。
2. **Chat history tab**：narrative / event / job 三层勾选；放进同一棵树是因为这三者有 narrative 的 cascade 关系。
3. **Skills & MCP tab**（2026-05-15 改名）：上半段沿用 SkillsTab（url / zip / full_copy / skip）。下半段 `McpSection` 列每个 agent 的 `mcp_urls`，**默认全不勾**（opt-in 设计：MCP URL 经常指私网，bundle 1.1 起 import 时会直接 write-through 到接收方 mcp_urls，不该意外泄露）。数据走 `api.previewMcps(agent_ids)`。
4. **Social Network tab**：双栏 + 分页 + accordion。匹配同 team 名字的默认勾选（最宽匹配规则，议题 7.f.1 接受误报）。
5. **Message Bus tab**：调 `api.previewBusChannels(agent_ids)` 拿候选 channel（owner==self AND ≥1 closure 成员）；用户可勾掉某些 channel。默认全选，与旧版 closure-auto 行为对齐。Full mode 强制全选 + 只读。
6. **Artifacts tab**（2026-05-15 新增）：调 `api.previewArtifacts(agent_ids)` 列每个 agent 的 `instance_artifacts`。默认全选，可单独排除。文案明确告诉用户：底层文件总跟 `workspace.tar.gz` 走，这里只控制 DB 指针行是否入包；接收方导入时 session_id 会被清掉、pinned 强制 1。
7. **Workspace files tab**：从 `api.listFiles` 拿文件列表，sensitive pattern 命中默认 unchecked + warning 标。

## Review Summary modal

强制最后一步（议题 6.7.a-A）。列出 included / stripped / warnings 三块，含"未自动扫描自由文本"的告知（议题 6.5）。

## Gotcha

- `listSkills` / `getSocialNetworkList` / `listFiles` 这些 backend endpoint 的实际返回字段名我没 100% 对齐（A3/A4 不确定项），可能在某些 tab 显示空 → 测试时若空就是字段名不对，调对齐即可。
- `RadioCard` 默认勾选逻辑写在 `useEffect([JSON.stringify(skillsForAgents), ...])`，对深嵌套对象用 stringify 兜底；性能足够。

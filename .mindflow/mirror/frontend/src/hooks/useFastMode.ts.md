---
code_file: frontend/src/hooks/useFastMode.ts
last_verified: 2026-08-14
stub: false
---

# useFastMode.ts — per-agent fast-mode 开关状态（localStorage）

## Why it exists

chat fast mode 的前端状态源。后端刻意零持久化（TurnProfile 只随单 turn 走，
见 turn_profile.py），所以「这个 agent 是否开极速」由浏览器记：
`narra-nexus-fast-mode` 一个 key 存 `{agentId: true}` map，关掉即删项。
per-agent 键控与旁边 ComposerModelBadge 的 per-agent 模型心智一致。

## Design decisions

- **adjust-state-during-render 而非 effect**：agent 切换时在 render 期间比对
  `state.agentId !== agentId` 直接 setState（React 官方模式）。用 effect 版
  会吃 react-hooks lint error（cascading render），且聊天流式渲染频繁，
  省掉一轮多余 render pass。storage 只在 agent 切换时重读。
- **损坏容忍**：JSON 解析失败、非对象、数组（`!Array.isArray` 显式挡）
  一律回退空 map（坏 JSON / 数组 / 标量三种形态都各有测试）；`localStorage.setItem` 抛错
  （隐私模式/配额）吞掉，本 session 内存态照常。
- **agent 切换分支有独立测试**：单 hook 实例 rerender 换 agentId 必须
  重读 storage（防 A 的开关渗到 B——预审 I2 补网，删分支测试变红）。
- **无 agentId 时惰性**：返回 false 且 set 为 no-op，不落任何存储。

## Upstream / Downstream

- **Downstream**: [[ChatPanel]]（唯一消费方）→ ComposerFastToggle 展示 +
  run() 第 6 参 → wsManager 首包 `fast_mode`。
- 测试: frontend/src/hooks/__tests__/useFastMode.test.ts

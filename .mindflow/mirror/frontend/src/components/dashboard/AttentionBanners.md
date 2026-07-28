---
code_file: frontend/src/components/dashboard/AttentionBanners.tsx
last_verified: 2026-04-13
stub: false
---

# AttentionBanners.tsx — Intent

## 为什么存在
把 agent 的**需要用户注意**的状态（failed job / blocked job / paused jobs / slow response）以顶部横幅形式浮出，**不用让用户去读 queue bar 的颜色**。异常"自己跳出来"。

## 上下游
- **上游**：`AgentCard.tsx::OwnedCard` 读 `agent.attention_banners`（服务端 `derive_attention_banners` 生成）
- **下游**：
  - `expandState.ts` 的 `useExpanded` 做 dismiss 状态持久化
  - `bannerKey(agentId, kind, message)` 是 `AgentCard` 计算 `useAllBannersDismissed` 用的**同一 key 格式**——两处必须一致
- **来源**：服务端 `dashboard/_helpers.py::derive_attention_banners(queue, has_slow_response)`

## 设计决策
1. **按 severity 排序**（error > warning > info）在服务端已做，前端按数组顺序渲染即可。
2. **`[×]` dismiss 按钮**（v2.1.1）：
   - 存 `sessionStorage` 而不是 localStorage——agent 状态变化快，跨 session 持久化没意义。
   - Key 格式：`${agentId}:banner:${kind}:${encodeURIComponent(message)}`。message 里嵌了 live count（"1 job failed"），**count 一变 key 就变**，新 banner 重新出现。防止 "dismiss 一次就永远忘"。
3. **stopPropagation**：dismiss 按钮 onClick 必须 `e.stopPropagation()`，否则点击会冒泡到 `AgentCard` 的 card-body click → 顺带展开整个卡片（反直觉）。
4. **级别颜色分离**：`LEVEL_STYLE` 映射 error/warning/info 到 tailwind class + icon。不和 `healthColors.ts` 复用——banner 的紧迫感和 status rail 的持久状态语义不同（banner 是"这件事需要处理"，rail 是"当前整体状态"）。

## Gotcha
- **和 `AgentCard::useAllBannersDismissed` 的 key 约定**必须匹配：若改 key 格式（如加版本前缀），两边同时改，否则 rail dim 判断永远 false。`bannerKey()` 函数封装正是为了防这个分裂。
- Dismiss 状态是**前端私有**——同一用户换浏览器 tab 需要重新 dismiss。可接受。
- `useExpanded(key, false)` 把 dismiss 语义复用在"expanded = true"上（反向语义 hack）；如果后续 useExpanded 加了更多 true 特化行为（动画、重新 fetch 等），要 audit 这里是否受影响。
- `action` 字段（`AttentionBannerAction`）schema 里有但**目前前端没渲染**——设计保留位，未来想加 `[Retry All Failed]` 这类批量按钮时用。

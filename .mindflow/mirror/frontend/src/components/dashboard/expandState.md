---
code_file: frontend/src/components/dashboard/expandState.ts
last_verified: 2026-04-13
stub: false
---

# expandState.ts — Intent

## 为什么存在
统一管理 dashboard 上所有可展开元素的状态——卡片级展开、section 级展开、item 级展开、banner dismiss——全部用**同一个** sessionStorage + 同一个事件通道。

不用 React Context 或 Zustand：展开状态高度局部（每个元素独立），Context 会造成全局 re-render；每个组件自己持 local state 又丢失了跨 re-mount 的持久化。sessionStorage + 自定义 event 是甜点方案。

## 上下游
- **被引用**：`SessionSection / RecentFeed / AttentionBanners`。（`AgentCard` 与 `JobsSection` 已删除；`Sparkline` 现在自行懒加载，不再经过这里。）
- **底层**：`window.sessionStorage` + `window.dispatchEvent('dashboard-expand-changed')` 自定义事件

## 导出
- `useExpanded(key, defaultOpen=false)`：基础 hook，返 `{ expanded, toggle, set }`。所有 section/item 展开复用。
- `useAllBannersDismissed(keys)`：v2.1.2 新增，批量读多个 banner 的 dismiss 状态，全 true 才返 true。用于 AgentCard 根据"banner 都消完了吗"决定 rail 是否淡化。
- `bannerKey(agentId, kind, message)`：key 格式工厂函数——**所有涉及 banner 的地方必须用这个**，不自己拼字符串。保证 `AttentionBanners` 和 `AgentCard` 算的 key 一致。

## 设计决策
1. **sessionStorage 而非 localStorage**：Agent 状态变化快，跨 session 持久化的"展开状态"多半是过时垃圾。关 tab 清空是 feature not bug。
2. **`useSyncExternalStore`**：React 18+ 推荐的外部 store 订阅 API，支持 concurrent mode 安全。比 `useEffect + useState` 响应性更好。
3. **自定义事件 `dashboard-expand-changed` + `storage`**：
   - 本 tab 内多个组件同步（比如 AttentionBanners dismiss 后 AgentCard 立刻 dim rail）→ 自定义事件
   - 跨 tab 同步（同用户开多 tab）→ `storage` 事件（sessionStorage 通过 cross-tab storage event 分发）
4. **`useExpanded` 用 `expanded` 字段名**：但 AttentionBanners 把它**反向用作 dismissed**。语义 hack。在 banner 里 `expanded=true ⇔ dismissed=true`。命名代价但没必要加个 `useDismissed` 重复实现。
5. **`writeAll` 失败 silent**：privacy mode 浏览器禁 sessionStorage 也要能跑——dismiss 当次有效但关 tab 就丢，可接受。

## Gotcha
- **跨 tab 的 storage event 只触发在**其他 tab（不是写入的 tab 自己）。所以必须 `window.dispatchEvent(new Event('dashboard-expand-changed'))` 手动补当前 tab。两个订阅都要挂。
- **key 命名约定**不在代码里强制，靠规则：`${agentId}:section:${section_name}` / `${agentId}:item:${kind}:${id}` / `${agentId}:banner:${kind}:${sig}` / `${agentId}:card`。改约定全部 `useExpanded` 调用点都要扫。
- **`useAllBannersDismissed(empty_list)` 返 false**——"没 banner 要 dismiss"不是"都 dismiss 了"。这是刻意设计：`AgentCard` 用这个结果决定是否 dim rail，空 banner 时 rail 应该反映原始 health 而不是被 dim。
- **bannerKey 用 `encodeURIComponent(message)`**：防止 message 含 `:` 或 `/` 破坏 key 格式。解码不需要（key 只作存储索引）。
- **storage JSON 限 5MB**：一个 agent 最多展开 10 几个 item + 几个 banner，远远不爆。长期运行也不累积（sessionStorage 关 tab 清）。

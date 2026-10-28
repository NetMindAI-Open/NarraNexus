---
code_file: frontend/src/lib/modelBrandIcons.ts
last_verified: 2026-09-04
stub: false
---

## 2026-09-04 — `iconInvertsInDark`：黑底透明品牌图的唯一名单

原来四处各自 `=== OpenAIBrandIcon`。再进一个黑底透明的 mark 时改这里一处，不用每页找。
[[frameworkBrand.ts]] 与两个页面都改问它。

# modelBrandIcons.ts — protocol / model-id → 品牌图标的匹配

## 为什么存在

[[../components/icons/ModelBrandIcons.tsx]] 只能放 React 组件（react-refresh 的
`only-export-components` 规则不让一个文件混合导出组件和普通函数），这两个纯匹配
函数因此拆到这里。**纯逻辑，无 JSX、无 hook**。

## 两个函数覆盖的场景不一样

- `getModelBrandIcon(modelId)` —— 给 Dashboard 智能体目录的 **Model 列**用
  （[[../pages/DashboardPage.tsx]]、[[../pages/AgentProfilePage.tsx]]）。按 model
  id 里的关键字子串匹配（小写化后 `includes`），覆盖 `agentFramework.ts` 的
  `MODEL_SUGGESTION_GROUPS` 全部 8 个厂商分组。自定义 base_url provider 的
  model_id 可能是任意字符串，匹配不上就返回 `null`——调用方对 `null` 退回通用
  `Bot` 图标，不是报错也不是留空洞。
- `getProtocolBrandIcon(protocol)` —— 给 Provider 下拉用。
  `ProviderSummary.protocol` 全仓库只有 `'anthropic' | 'openai'` 两个值，所以只有
  两个分支，不管 `source`（netmind / yunwu / openrouter / oauth 等 8 种来源）——
  来源信息下拉文字里已经写了（"NetMind (Anthropic)" 这种），图标只需要传达底层
  模型家族。

## Gotcha

- **`getProtocolBrandIcon` 目前在本仓库没有调用方**。它的目标消费者是 chat-ui-v4
  分支上按 provider 分步的 Create Agent 向导，那部分没有随 Dashboard 一起迁进来
  （2026-08-27 的迁移范围只有 Manage Agents / Team Management 两个看板）。刻意
  保留而不是删掉：向导落地时就要用，删了只会原样加回来。**别把它当成「有人在用」
  的证据**——改它的行为不会有任何页面变化，也不会有测试变红。
- `o[0-9]` 前缀判断（`/^o[0-9]/.test(id)`）是给 OpenAI 的 o3 / o4-mini 这类
  「o 开头接数字」命名兜底的，纯 `includes('gpt')` 抓不到。OpenAI 以后出新的命名
  系列，这条正则可能要跟着扩。
- 匹配顺序有意义：`claude` 判断在最前，`gpt` 在其后。都是子串匹配，一个同时含两个
  关键字的自定义 model id 会命中先写的那条。

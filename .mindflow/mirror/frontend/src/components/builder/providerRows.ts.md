---
code_file: frontend/src/components/builder/providerRows.ts
last_verified: 2026-09-03
stub: false
---

# providerRows.ts — provider map → 弹窗要画的行

## 为什么单独一个文件

映射能在不挂载任何组件的情况下测试。`api.getProviders()` 返回
`Record<string, unknown>` 是有意的（见 [[api.ts]]）—— 完整 provider schema
在此之前只有 `ProviderSettings` 一个消费者。

## 关键决策：副标签取自 `source`，不是 `auth_type`

`auth_type` 只有 `api_key | bearer_token` 两种值，**区分不出**一个 CLI OAuth
会话和一段手工粘贴的 bearer token。`source` 命名的是 provider driver
（`claude_oauth` / `codex_oauth` / `netmind` / `yunwu` / …），正是设计稿那个
「API Key / CLI sign-in」副标签要画的区别。

`CLI_SIGN_IN_SOURCES` 因此是 driver 名字的集合，加新的 OAuth driver 时要同步。

## 另外两条

- **不重排序**：保留服务端的 `Object.values` 顺序，`ProviderSettings` 也是这个
  顺序。同样一批 provider 在两个界面里顺序不同，读起来就是 bug。
- **`is_active` 缺失视为健康**：不带这个字段的后端不该让每个 provider 都被画成
  故障。
- `source` 字段今天没有渲染消费者，注释只陈述事实，不承诺「以后画品牌图标」。
- 导出的类型叫 `PickerRow`，**不叫 `ProviderRow`** —— 后者是
  `providersApi.ProviderRow`，那是规范全量行，这里只是它的窄化投影。

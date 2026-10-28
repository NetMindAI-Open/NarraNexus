---
code_file: frontend/src/lib/onboardDefaults.ts
last_verified: 2026-10-16
stub: false
---

# onboardDefaults.ts — 加载服务端的 per-source 默认模型表并返回

## 为什么存在
同一个"默认模型"决定原先在前端手抄一份(PR#413 review I2:改一个默认要同步多处,且前端看不到 `netmind_free` 的 env 覆盖)。
`refreshOnboardDefaults()` 调 `api.getProviderCatalog()`(GET `/api/providers/catalog`),取 `onboard_defaults`,**返回** `{ ok, defaults }`。
它不写任何全局状态:两个设置面板(`ModelDefaultsSettings` / `AgentLlmConfigPanel`)把 `defaults` 放进自己的 React state,
再作为显式参数传给 `lib/agentFramework` 的 `defaultAgentModel` / `defaultHelperModel` / `recommendedHelperModel`。

## 校验与失败语义
- 响应字段先被规范化成全新对象:丢弃非对象条目、非字符串/空的模型 id。
- `onboard_defaults` 缺失、为 null、数组或字符串,以及请求异常、`success` 为假,**都算失败**(不是空表):
  `console.warn('onboard defaults unavailable', e)` 并返回新的 `{ ok: false, defaults: {} }`(每次新对象,不共享)。
- **永不 reject**:它在面板 `load()` 的 `Promise.all` 里,reject 会让整个 load 进 catch、连 provider 列表都渲染不出;失败靠 `ok:false` 传达。
- 面板在 `ok:false` 时 `setError(loadFailed)`(既有 key,同 `fwRes?.success` 为假的软失败范式),并保留上一份 state,
  避免 NetMind 卡静默自动选 omni 列表首个。

## 不做什么
不持有任何模型 id;不缓存/节流(调用方是两个面板的 `load()`,频率低);不对老服务端或测试 mock 做兼容(前后端同发布;测试 mock 自行定义 `getProviderCatalog`)。

## Gotcha
- **触发**:新增调用点忽略返回的 `ok` → **症状**:取不到默认表时 NetMind 卡静默选列表首个(omni 模型),无任何提示 → **根因**:失败只通过返回值与一行 `console.warn` 传达,不抛异常。

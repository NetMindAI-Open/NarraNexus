---
code_file: frontend/src/lib/tokenExpiry.ts
last_verified: 2026-08-06
stub: false
---

# tokenExpiry.ts — 会话到期的提前量

## Why it exists

后端签发的 JWT 有效期 7 天（`backend/auth.py` `JWT_EXPIRY_DAYS`），而且
**没有任何续期端点**——到期就只能重新登录。此前前端从不读 `exp`，于是到期
的第一个信号就是用户下一次点击时的 401：会话在时钟选定的时刻结束，而不是
用户选定的时刻，屏幕上没保存的东西一起没。

读 `exp` 就能提前一天（`EXPIRY_WARNING_WINDOW_MS`）在 [[App]] 顶部挂一条
横幅，让用户自己挑时间重登。

## Design decisions

**不验签解析。** 这里没有任何授权判断——每个请求都由后端验签，篡改过的
`exp` 最多误导篡改者自己的横幅。

**解析失败一律返回 null，绝不抛。** 这段代码跑在 app shell 的渲染路径上，
一枚畸形 token 不能掀掉整个界面。null 语义是"没有可到期的东西"，local 模式
（不发 JWT）天然就是这个状态。

**措辞粗粒度**（`formatExpiryDistance`）。滚动到零的倒计时读起来像威胁；
"about 6 hours" 才是用户可以从容处理的信息。

**轮询 10 分钟一次 + `focus` 事件。** 一次 base64 解码，成本可忽略；`focus`
覆盖"合上笔记本几小时再打开"这种两次 tick 之间跨越了整个预警窗的情况。

## Upstream / Downstream

- **调用方**：[[App]]（横幅 state）。
- **数据来源**：[[authHeaders.ts]] 的 `getSessionToken()`。
- **同源信息**：`GET /api/auth/session` 也返回 `expires_at`（[[auth]] 路由），
  用于探针路径；本模块是纯客户端读法，不额外发请求。
- **测试**：`frontend/src/lib/__tests__/tokenExpiry.test.ts`。

## Gotchas

`exp` 是**秒**（JWT 标准），`msUntilExpiry` 返回**毫秒**。已过期返回 0 而不是
负数，横幅逻辑因此不需要额外判负。

这不是续期。真正的滚动续期（`POST /api/auth/refresh`）是单独一张卡：它会把
有效期变成无限滚动，需要绝对上限和多标签页竞态处理，属于认证有效期模型的
变更，不该和这次止血捆在一起。

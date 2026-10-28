---
code_file: backend/routes/channels/catalog.py
last_verified: 2026-09-07
stub: false
---

## 2026-09-07 — 渠道目录端点：前端不再自己维护一张渠道表

`GET /api/plugins/channels` 按 `ingress.channels` 注册表逐条返回
`{name, display_name, owner, ui:{label, icon, order}}`。

**它为什么存在。** 前端 `frontend/src/components/awareness/registerBuiltinChannels.ts` 里
有一张六行的中心表，逐字重复了 Python 侧 `ChannelUi` 已经声明的 label / icon / order——
同一个事实两份副本、两种语言，没有任何东西保证它们一致（实测 `ChannelUi` 这个契约当时
零消费者）。更要命的是第三方渠道插件写出完美的描述符也进不了 Channels 配置页：进得去
的前提是改引擎前端的一个文件。这个端点就是那道接缝。

**挂在 `/api/plugins` 而不是 `/api/channels`。** 它是对插件注册表的一次读取，不是对某个
渠道的操作；`/api/channels/{channel}/…` 那一组全部是 `check_owned` 门控的绑定操作，把一
个 ownership-free 的列表塞进同一个命名空间，等于请下一个读代码的人抄错邻居。

**ownership-free 是刻意的。** 目录是「本部署装了哪些渠道」，跟调用方有哪些 agent 无关，
对每个已认证用户答案相同——和隔壁 `/api/channels/{channel}/schema` 同一性质。凭据、绑定
和一切 per-agent 状态仍然留在 `generic.py` 里做 `check_owned` 的那些路由上。响应里没有任
何密钥：名字、显示名、三个展示字段，加上提供它的插件 id（前端用来把渠道归属到插件）。

**降级策略与其他渠道视图一致**（`_ChannelSpecs._build` / `TriggerMapView._build`）：逐条
隔离、fail-closed——一条建不出来的描述符打一次 warning 后跳过，代价是它自己那一行，不是
整页。没有 `ChannelUi` 的描述符不被省略，而是用 display_name 当 label、order 落到 100
排在最后：「没声明展示」不等于「别显示我」。

排序 `(ui.order, name)` 在服务端做，前端拿到即可直接渲染，两端不会对「谁排前面」有第二
套意见。

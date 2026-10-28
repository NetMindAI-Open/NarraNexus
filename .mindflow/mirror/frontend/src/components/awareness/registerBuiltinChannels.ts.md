---
code_file: frontend/src/components/awareness/registerBuiltinChannels.ts
last_verified: 2026-09-07
stub: false
---

## 2026-09-07 — 六行改成后端目录驱动（批 6c，A2-8）

本文件原来是一张写死的六行表，每行的 `label` / `icon` / `order` 都逐字抄自对应插件
Python 侧的 `ChannelUi(...)`（lark `order: 10` ↔ `descriptor.py` `order=10`，
discord 60 ↔ 60 ……）。一个事实两处拼写、靠人手对齐、没有任何东西会在漂移时报警；
而 `ChannelUi` 这个契约当时**没有任何消费者**。加一个渠道要改的地方也因此多了一处。

现在行来自 `GET /api/plugins/channels`（`api.pluginChannels()`），
也就是宿主 `ingress.channels` 注册表本身。契约终于有了消费者。

刻意仍然绑在代码上的两样：**config 组件**（绑定表单是真 UI，不是数据；
自带表单的第三方 channel 插件用 `GenericChannelConfig` 自己注册），
以及**图标**（`ui.icon` 在线上是 lucide 名字，本地查表解析——
为了支持任意名字而把整个 lucide 打进包，代价远大于我们出货的这六个）。

六个 `if (!CHANNELS.has(id))` 幂等垫片随之删除：被拉黑的 owner 是注册表自己的职责
（`disableBuiltinUi` 在 boot 时就把 owner 拉黑，`Registry.register` 会静默丢弃），
而重复注册同一个 id 现在会抛错——那正是双重注册应得的响亮失败。

注册是**异步**的，模块导出 `builtinChannelsReady` 这个 promise。
`IMChannelsSection` 的 `import './registerBuiltinChannels'` 副作用写法保持不变
（`useRegistryEntries` 会在注册落地时重渲染），测试 await 这个 promise。

# awareness/registerBuiltinChannels.ts — the `ui.channels` rows, from the backend catalog

## Intent

Folds `GET /api/plugins/channels` — the `ingress.channels` registry rendered as data — into the
`ui.channels` registry, one row per channel the backend reports, owned by that channel's plugin id.
Imported for its side effect by `IMChannelsSection`; the registration is a promise
(`builtinChannelsReady`) because the rows arrive over the network, and the section re-renders on
registry change so rows appear when the fetch lands.

## 2026-09-07 — the six-row table is gone; the rows come from the backend

This file used to write out Lark / Slack / Telegram / WeChat / NarraMessenger / Discord by hand,
with a `label` / `icon` / `order` copied verbatim from each plugin's Python `ChannelUi(...)`. That
was two spellings of one fact in two languages with nothing to catch a drift, and it made the
Channels page a **fourth** edit site for anyone adding a channel — a third-party channel plugin
could ship a perfect `ChannelDescriptor` and still not appear, because appearing meant editing a
file in the engine's frontend. `ChannelUi` was a contract with no consumer; now it has one.

Two things stay code-bound on purpose, and the source comment says why:

- **the config component** — a channel's bind form is real UI, not data. A channel with no entry in
  `CONFIGS` gets no row here (its UI ships with its plugin).
- **the icon** — `ui.icon` is a lucide NAME on the wire, resolved through the local `ICONS` lookup,
  because bundling all of lucide so an arbitrary name resolves would cost far more than the six
  icons we ship. An unknown name falls back to a neutral icon, never a crash.

A catalog fetch that fails is swallowed and the section renders empty: the request is a plain GET on
the same origin that already served the SPA, so a failure means the backend is down and every other
panel is failing too — better than letting a dead network break module evaluation of the whole
Channels chunk.

## 2026-09-07 — the per-row `if (!CHANNELS.has(id))` guards are gone

Correcting what the earlier note on this page said. Disabling a builtin channel never went through
those guards at all: `disableBuiltinUi('builtin.channels.<x>')` runs at boot, before this lazily
imported chunk loads, and blacklists the OWNER — `Registry.register` then silently drops any row
that owner tries to add, so `IMChannelsSection` never lists it. The guard was never what made
disabling work.

What the guards actually papered over was a `RegistryConflictError` on RE-registration: registering
an id the registry already holds now throws, and this module can be evaluated more than once (HMR, a
re-imported chunk). Keeping a `has()` check to dodge that would suppress the loud failure a genuine
double-registration deserves, so the guards were removed rather than kept — the blacklist is the
registry's job and the throw is the right answer to the other case.

## 2026-09-04 · one probe (batch 4d.3)

`probe(channel)` reads `api.channelCredential` and maps `enabled` → active/inactive for every row
(Lark included — no more `is_active` special case). Bound + enabled → `active`; bound but disabled →
`inactive`; anything else (unbound, denied, network) → `unbound`.

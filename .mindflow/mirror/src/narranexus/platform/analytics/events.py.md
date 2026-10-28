---
code_file: src/narranexus/platform/analytics/events.py
last_verified: 2026-08-19
stub: false
---

## 2026-08-19 — `PROP_PAYMENT_METHOD` / `PROP_MONTHS`：让 checkout 事件能区分两个商品

`checkout_created` 原来只带 `session_id`，于是**一笔 $19 的信用卡订阅和一笔 $228
的 12 个月一次性购买在漏斗里长得一模一样**。加支付宝/微信这件事值不值得继续投入，
只能去 Stripe 后台手工对账才答得出来。

`months` **只在一次性轨带**：信用卡订阅没有月数，发 `1` 会被读成"有人买了一个月的
卡订阅"，而那不是一个存在的东西。

**这两个维度绝不能进 `event_id`**（幂等键仍是 `checkout_created:{session_id}`）——
同一个 session 会因维度不同而重复计数。有测试钉住这一条。

已知仍未覆盖：`subscription_activated` 不带这两个维度，而一次性的**叠加续期**发生在
同一个 subscription 上、因此一个新事件都不发。所以目前只有**意图**侧可分辨，转化侧
不行 —— 而复购正是一次性商品的全部留存模型。记在这里，不在本次范围内。


# events.py

## 2026-08-10 — end-to-end product and payment vocabulary

The vocabulary covers workspace entry, submit/accept/run/outcome/render
message stages, and click/create/open/activate subscription stages.
`FRONTEND_EVENTS` doubles as the browser ingestion allowlist; backend-only
facts cannot be spoofed through the route.

## Why it exists

Single source of truth for every funnel event name and shared property key.
Without this file, event names would be inline string literals scattered across
routes, services, and tests. A typo or inconsistent capitalisation would split
one business fact across multiple database values and corrupt funnel totals.

Keeping all names here means renaming an event is a one-file change, grep is
trivial, and new capture sites discover the full event vocabulary without
reading every route.

## Upstream / downstream

- **Consumed by**: any route or service that calls `analytics.track()` with an
  event name constant, and tests that assert the persisted fact contains the
  expected event name.
- **No runtime dependencies**: this file is pure constants — no imports beyond
  `__future__`.

## Design decisions

**Setup event ingestion**:

| Constant | Event name | Fires when | Emitted by |
|---|---|---|---|
| `EVENT_SIGNED_UP` | `signed_up` | New user created | `auth.py create_user` (backend) |
| `EVENT_SETUP_ENTERED` | `setup_entered` | Setup page mounted | Frontend via `POST /api/analytics/events` |
| `EVENT_SETUP_SKIPPED` | `setup_skipped` | "Done" clicked with 0 providers | Frontend via `POST /api/analytics/events` |
| `EVENT_SETUP_COMPLETED` | `setup_completed` | "Done" clicked with ≥1 provider | Frontend via `POST /api/analytics/events` |
| `EVENT_MESSAGE_ROUND_TRIP_SUCCEEDED` | `message_round_trip_succeeded` | Full agent response delivered | Background run layer (backend) |

The three setup events are pure UI actions with no backend signal. They share
the authenticated browser ingestion endpoint, whose allowlist is exactly
`FRONTEND_EVENTS`; backend-only event names cannot be submitted there.

**Removed in 2026-06-09 redesign**: `EVENT_TERMINAL_ACCESSED`,
`EVENT_LLM_SLOT_CONFIGURED`, `EVENT_AGENT_CREATED` and their matching property
keys `PROP_SLOT_NAME`, `PROP_MODEL`, `PROP_FIRST_ROUND`, `PROP_PROVIDER_METHOD`
were deleted. The mid-funnel steps proved too noisy and less actionable than the
lean setup-page signals.

**Retained property keys**: `PROP_SURFACE`, `PROP_METHOD` (used by `signed_up`
with value `"create_user"`), `PROP_AGENT_ID`, `PROP_RUN_ID` (used by
`message_round_trip_succeeded`).

**No grouping by event**: all constants are flat at module level. If the list
grows significantly, grouping into dataclasses or Enum subclasses can be
considered, but for five events a flat list reads more clearly.

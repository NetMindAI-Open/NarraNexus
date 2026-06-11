---
code_file: src/xyz_agent_context/analytics/events.py
last_verified: 2026-06-09
stub: false
---

# events.py

## Why it exists

Single source of truth for every funnel event name and shared property key.
Without this file, event names would be inline string literals scattered across
routes, services, and tests. A typo or inconsistent capitalisation would create
a split event in the PostHog dashboard that is invisible until someone notices
the funnel numbers are wrong.

Keeping all names here means renaming an event is a one-file change, grep is
trivial, and new capture sites discover the full event vocabulary without
reading every route.

## Upstream / downstream

- **Consumed by**: any route or service that calls `analytics.track()` with an
  event name constant, and tests that assert `FakeSink.events` contains the
  expected event name.
- **No runtime dependencies**: this file is pure constants — no imports beyond
  `__future__`.

## Design decisions

**Lean 5-event funnel** (redesigned 2026-06-09):

| Constant | Event name | Fires when | Emitted by |
|---|---|---|---|
| `EVENT_SIGNED_UP` | `signed_up` | New user created | `auth.py create_user` (backend) |
| `EVENT_SETUP_ENTERED` | `setup_entered` | Setup page mounted | Frontend via `POST /api/auth/funnel` |
| `EVENT_SETUP_SKIPPED` | `setup_skipped` | "Done" clicked with 0 providers | Frontend via `POST /api/auth/funnel` |
| `EVENT_SETUP_COMPLETED` | `setup_completed` | "Done" clicked with ≥1 provider | Frontend via `POST /api/auth/funnel` |
| `EVENT_MESSAGE_ROUND_TRIP_SUCCEEDED` | `message_round_trip_succeeded` | Full agent response delivered | Background run layer (backend) |

The three setup events are pure UI actions with no backend signal; the frontend
reports them via `POST /api/auth/funnel`. That endpoint whitelists only the
`setup_*` constants defined here, so this file doubles as the access-control
contract for that endpoint.

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

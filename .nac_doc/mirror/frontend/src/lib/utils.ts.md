---
code_file: frontend/src/lib/utils.ts
last_verified: 2026-04-10
stub: false
---

# utils.ts — Shared pure utility functions

## Why it exists

Reusable pure functions that have no dependency on React, stores, or the backend. Collected here to prevent copy-paste across components and to give the UTC timestamp parsing fix a single authoritative location.

## Upstream / Downstream

Used broadly: `cn` is imported by nearly every styled component. `generateId` is used by `chatStore` for message and round IDs. `formatTime`, `formatDate`, `formatRelativeTime` are used by chat and job panel components. `truncate` is used in sidebar and card displays.

## Key design decisions

**`parseUTCTimestamp` exists because the backend omits timezone info.** The backend stores and returns timestamps like `"2026-03-11 09:50:09"` — no `Z`, no offset. JavaScript's `Date` constructor treats timezone-naive strings as local time, which means a user in UTC+8 would see times shifted 8 hours early. `parseUTCTimestamp` appends `Z` after normalizing the space separator to `T`, forcing UTC interpretation. Every other time function in this file routes through it.

**`cn` wraps `clsx` + `tailwind-merge`.** The combination allows conditional Tailwind classes (`clsx`) while correctly resolving conflicting class variants (e.g., `p-4 p-2` → `p-2`) via `tailwind-merge`. Using `clsx` alone would accumulate both `p-4` and `p-2` with unpredictable specificity results.

**`generateId` uses `Date.now()` + random string.** Not cryptographically secure, not globally unique across processes. Sufficient for client-side IDs within a single session (chat message IDs, history round IDs). Do not use for anything that needs backend uniqueness.

## Gotchas

**`formatTime` uses `zh-CN` locale.** The `toLocaleTimeString('zh-CN', ...)` call will format as `HH:MM:SS` in 24-hour format, which is the intended design. Users in locales that default to 12-hour format still see 24-hour times. If the UX needs locale-aware formatting, this would need a change.

**`formatRelativeTime` for very old dates falls back to `formatDate`.** Anything older than 7 days shows the full date string. The threshold is hardcoded — there is no configuration option.

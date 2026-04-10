---
code_file: frontend/src/hooks/useTimezoneSync.ts
last_verified: 2026-04-10
stub: false
---

# useTimezoneSync.ts — One-shot browser timezone sync to backend

## Why it exists

The backend stores timestamps in UTC. When displaying job schedules, notification times, or conversation timestamps, the frontend needs the server to know the user's local timezone so it can format or adjust time-related outputs correctly. Rather than sending the timezone on every API call, it is synced once per session to the user's profile via `PUT /api/auth/timezone`.

## Upstream / Downstream

Reads `isLoggedIn` and `userId` from `useConfigStore`. Calls `api.updateTimezone(userId, timezone)` once per session. Uses `Intl.DateTimeFormat().resolvedOptions().timeZone` to detect the IANA timezone string (e.g., `"America/New_York"`).

Mounted in `App.tsx` so it runs on every page load. The `hasSynced` ref ensures the API call fires at most once per session even if the component re-renders.

## Design decisions

**Session-scoped ref guard.** `hasSynced` is a `useRef` (not state) so the sync runs at most once without causing a re-render. It resets on hard page reload, triggering re-sync — which is appropriate since the user's timezone could have changed.

**Silent failure.** Timezone sync is non-critical. If the backend is unreachable or returns an error, the hook logs a warning and moves on. The absence of timezone data causes minor display issues (UTC times) rather than a broken experience.

**No retry.** If the first sync fails, it does not retry. `hasSynced` remains false, so the next page reload will attempt again.

## Gotchas

**Called in `App.tsx` before any stores are hydrated from localStorage on cold start.** The `useEffect` correctly gates on `isLoggedIn && userId`, so no API call fires until those values are truthy. In local mode (no JWT), the call still fires and sends an empty token — which the backend ignores for `PUT /api/auth/timezone` since that endpoint does not require auth in local mode.

---
code_file: frontend/packages/ui-kit/src/client.ts
last_verified: 2026-09-07
stub: false
---

# @narranexus/ui-kit — client.ts

`ChatClient` (framework-agnostic embedding transport): `history(limit)` reads `/api/agents/{id}/chat-history` (X-User-Id locally, bearer on cloud) and `historyToMessages` turns events (trigger → user, final_output → assistant) into messages; `send(text, onEvent)` opens `/ws/agent/run` (`?x_user_id=` locally, token in payload on cloud), sends the app's run payload, accumulates `agent_response` and `agent_reply_delta` deltas, settles once on `complete`/`error`/`cancelled`/close, and `stop()` sends `{action: "stop"}`. `wsUrl()` derives the socket URL.

## 2026-09-07 — socket close mid-answer settles as `interrupted`, not `complete` (I-7)

The backend does NOT cancel a run when the WS disconnects (an intentional design: the agent keeps
working, and a reconnect or a later poll can still pick up the result) — so a socket close while
text has already streamed used to settle via `finish({ type: 'complete', text: buffer })`, telling
the consumer the answer is done and final when it may not be. Added a new `TurnEvent` variant,
`{ type: 'interrupted'; text: string }`; `onclose` now emits `interrupted` (carrying whatever text
had already streamed) instead of a false `complete` when there is buffered text. `stop()`'s doc
comment was also corrected — the backend answers a stop request with `{"type": "stopping"}`, not
a `cancelled` frame as previously (incorrectly) documented here.

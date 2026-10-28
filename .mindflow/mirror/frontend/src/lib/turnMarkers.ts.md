---
code_file: frontend/src/lib/turnMarkers.ts
last_verified: 2026-09-11
stub: false
---

# turnMarkers.ts — no-reply turn markers and their render-time labels

A turn with no owner-facing reply is persisted by the backend chat module
(`plugins/builtin.chat/.../chat_module.py`, persist path) with one of two
fixed English markers as its assistant content: `(Interrupted by user)`
when the user stopped the turn, `(Agent decided no response needed)` when
the agent chose silence. This module is the frontend's single copy of
those two strings.

- `INTERRUPTED_MARKER` / `NO_RESPONSE_MARKER`: written by
  `chatStore.stopStreaming` for a settled no-reply turn (so live and
  reloaded history carry identical content) and compared by
  `buildTimeline`'s non-chat junk filter.
- `localizeTurnMarker(content, t)`: exact-match lookup to
  `chat.stoppedByUser` / `chat.noResponseNeeded`; any other content passes
  through unchanged. Called only by `MessageBubble` on the render path.

Design rule: the marker is data and never changes with the UI language;
only the renderer translates it. Replacing a stored marker with localized
text would silently break every literal comparison (frontend and backend).
The strings must stay byte-identical to the backend's.

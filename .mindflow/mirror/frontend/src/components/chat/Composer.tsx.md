---
code_file: frontend/src/components/chat/Composer.tsx
last_verified: 2026-06-20
stub: false
---

## 2026-06-20 — ComposerHandle gained setText (suggested-prompt fill)

Added `setText(value)` to the imperative handle: it replaces the textarea
value, reports the empty↔non-empty flip, then focuses the textarea with the
caret at the end. Used by [[OnboardingJourney]]'s suggested-prompt chips —
clicking a chip fills the composer (it does NOT auto-send; the user reviews
then hits Enter). Needed a real ref to the underlying `<textarea>`, so the
component now holds `textareaRef` and forwards it to `Textarea` (which already
forwards refs to the element).

# Composer.tsx — isolated chat message textarea

## Why it exists

Split out of `ChatPanel.tsx` (2026-05-22) to fix chat-input typing lag. The
draft text was `input` state inside ChatPanel, which subscribes to the entire
chat store and renders the whole message timeline — so every keystroke
re-rendered that monolith, and typing during streaming (one-char-per-token
models like DeepSeek via aggregators) collided two re-render storms. Holding
the text in this small memoized child means a keystroke re-renders only here.

## Contract with ChatPanel

- **Imperative handle** (`ComposerHandle`): `getText()` (read on send) and
  `clear()` (after a successful send). ChatPanel never holds the text as state.
- **`onEmptyChange(isEmpty)`** fires ONLY on the empty↔non-empty flip (not per
  keystroke) so the Send button's disabled state stays correct without
  re-rendering ChatPanel per character.
- **`onSubmit`** fires on Enter (no Shift, not mid-IME). ChatPanel passes a
  STABLE wrapper (ref-backed `useCallback`) so this memoized component does not
  re-render when ChatPanel re-renders (e.g. streaming). Same for the drag/paste
  handlers — they're also bound to ChatPanel's wrapper div, so they live in
  ChatPanel and are handed down as stable wrappers.

## Design decisions / gotchas

- **Draft persistence is debounced** (400ms) and flushed on unmount; ChatPanel
  remounts via `key={agentId}` so each agent's draft restores from
  `chatDrafts` on mount. Don't move draft state back up — that reintroduces the
  per-keystroke localStorage write and the lag.
- **IME composition guard**: Enter within 100ms of `compositionend`, or while
  `isComposing`, does not submit (CJK input). The refs live here now.
- The textarea must keep the drag/paste handlers (native default would insert a
  dropped file path / paste-as-text otherwise) — see ChatPanel's wrapper-div
  comment.
- 铁律 #16: this is pure render isolation — no message content is dropped,
  truncated, or throttled.

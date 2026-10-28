---
code_file: frontend/packages/ui-kit/src/ChatWidget.tsx
last_verified: 2026-09-07
stub: false
---

# @narranexus/ui-kit — ChatWidget.tsx

Embeddable React chat for one agent: loads history through `ChatClient`, streams a reply into a single assistant bubble, disables the composer while a turn runs (Stop button), surfaces transport errors, scrolls to the newest message, themable via `--nx-*` CSS variables; no app store or router.

## 2026-09-07 — renders `client.ts`'s new `interrupted` event (I-7)

An `interrupted` state + `.nx-chat__msg--interrupted` CSS class: on `ev.type === 'interrupted'`
the in-flight bubble's text is patched to whatever streamed before the disconnect and a small
notice ("Connection interrupted — the reply above may be incomplete.") renders below it, instead
of silently treating a dropped connection as a normal, complete answer.

---
code_file: frontend/packages/chat-widget/src/index.ts
last_verified: 2026-09-04
stub: false
---

# @narranexus/chat-widget — index.ts

`<narranexus-chat>` custom element (`NarraNexusChatElement`): attributes `base-url`, `agent-id`, `user-id`, `token`, `height`, `placeholder`, `history-limit` map to `ChatWidget` props, are observed (live re-render), and a React root is mounted on connect / unmounted on disconnect; `register(tag)` defines the element once and runs at import. The vite config bundles React + ui-kit into one ESM file.

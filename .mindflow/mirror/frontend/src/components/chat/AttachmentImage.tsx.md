---
code_file: frontend/src/components/chat/AttachmentImage.tsx
last_verified: 2026-04-29
stub: false
---

# AttachmentImage.tsx — Hook-host wrapper around useAttachmentBlobUrl

## Why it exists

`useAttachmentBlobUrl` is a hook, so it can't be invoked inside the `.map()` callback that renders a list of attachments. This component is the per-item hook host: one instance per attachment, hook scoped to that instance, lifetime aligned with the rendered `<img>`. Without this indirection, the alternative is hoisting blob fetching to the parent and storing a `Map<file_id, blobUrl>` — much heavier for what is only an `<img>` swap.

## Upstream / Downstream

- **Used by**: `MessageBubble` (image attachments inside an assistant/user message bubble) and `ChatPanel` (pending-attachments preview row above the textarea).
- **Calls**: `useAttachmentBlobUrl` for the authed fetch.

## Design decisions

**Placeholder while loading.** Returns a styled `<div>` with the lucide `ImageIcon` until the blob URL resolves. Same dimensions / classes as the eventual `<img>`, so the layout doesn't jump when the image fills in.

**`zoomable` prop instead of forcing one wrapping markup.** `MessageBubble` wants `<a target="_blank">` for click-to-zoom; `ChatPanel`'s pending preview is a tiny chip that doesn't open. The boolean keeps the call sites declarative without fragmenting the component into two near-duplicates.

**No download button or filename overlay.** Those concerns are owned by `MessageBubble`'s file-chip path (for non-image attachments) — image attachments are visually self-describing. Adding a filename overlay here would couple `AttachmentImage` to the chat-bubble visual language.

## Gotchas

**`<a href={blobUrl}>` only works while this component stays mounted.** If the user opens the zoom link in a new tab and then closes the chat panel, the blob URL becomes invalid (revoked on unmount) and the new tab shows a 404-ish blob error. Acceptable for inline preview; not a substitute for a real download endpoint.

**`alt={original_name}` leaks the user's filename to assistive tech and SEO crawlers.** Inside the auth wall this is fine — only the logged-in user sees their own attachment list. If chat transcripts ever become publicly shareable, revisit.

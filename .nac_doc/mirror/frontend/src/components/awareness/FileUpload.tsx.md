---
code_file: frontend/src/components/awareness/FileUpload.tsx
last_verified: 2026-04-10
stub: false
---

# FileUpload.tsx — Drag-and-drop file manager for agent workspace

Allows uploading files that the agent can read via its file-access MCP tools. No file-type restriction (unlike `RAGUpload`). Supports both drag-and-drop and the standard file picker dialog.

Files are scoped to `agentId + userId`. Deleting prompts a `confirm()` dialog. No upload progress bar — a full-screen overlay spinner covers the drop zone during upload.

Used only inside `AwarenessPanel`. Does not use `usePreloadStore` — manages its own local `files` state.

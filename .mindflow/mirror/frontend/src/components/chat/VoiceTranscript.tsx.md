---
code_file: frontend/src/components/chat/VoiceTranscript.tsx
last_verified: 2026-05-07
stub: false
---

# VoiceTranscript.tsx — Whisper-transcribed voice memo as text

## Why it exists

Phase 3 of the multimodal-audio rollout was originally going to ship an `<audio>` playback widget plus a transcript line (`AudioMessage`). After the first iteration in production we cut the player entirely: when a user records via the in-browser AudioRecorder, the **transcript is the message**. There is no "verify what I said" loop and no need to keep the audio bytes accessible to the user — Whisper's output stands on its own.

This component owns the voice-memo render path. Plain audio file uploads (Paperclip / drag-drop / paste) deliberately do NOT go through here: those keep the regular file-chip rendering because the user is sharing a file with the agent, not dictating. The discriminator is `att.source === 'recording'`, which the upload route echoes back from the request's `source` query param and which is persisted on the attachment dict so chat history reload still picks the right path.

Both cases (recording AND upload) get Whisper-style transcription on the backend so the agent always receives the spoken content via the system prompt — `source` only changes how the bubble renders. Transcription routes through `agent_framework.transcription.TranscriptionService` (not the deleted `utils/audio_transcription`); resolver picks OpenAI multipart or NetMind submit-poll based on the user's existing provider config.

## Upstream / Downstream

- **Used by**:
  - `MessageBubble` — full layout for voice memos in chat history bubbles. Shown above any typed message content.
  - `ChatPanel` — `compact` layout in the pendingAttachments preview row above the textarea, before the message is sent.
- **Calls**: nothing — this is a pure presentational component. No `useAttachmentBlobUrl`, no API calls. The `transcript` string comes in as a prop, that's the entire input.

## Design decisions

**No `<audio>` playback, period.** Earlier iteration shipped a native player; we removed it because:
1. The transcript IS what the agent receives — playing back the audio doesn't add information once Whisper runs.
2. Two surfaces (player + transcript) made bubbles tall and crowded.
3. The blob-URL fetch for inline `<audio>` doubled the per-bubble HTTP requests for no behavioral benefit.
The audio bytes are still on disk (they always are — that's where the upload landed), just not surfaced in the UI.

**`compact` prop instead of two components.** Same reasoning as before: full and compact share most of their layout, only spacing and the verbose-vs-truncated transcript differ. One component with a boolean keeps callers declarative.

**Compact mode truncates to one line.** Preview row is competing with the textarea for vertical space. Long transcripts get `text-overflow: ellipsis` on the single-line variant. The full bubble version always wraps.

**"transcription unavailable" fallback rendered, not hidden.** When `transcript` is null/empty (recording uploaded but Whisper couldn't run — no provider configured), we show a `MicOff` icon plus a one-liner asking the user to add an OpenAI key. Hiding the chip would make the recorded message look like it was lost; this way the user knows their input was received but couldn't be transcribed.

**Mic icon, not FileAudio.** FileAudio implied "audio file"; Mic conveys "voice input / dictation", which is the actual semantic.

## Gotchas

- The component does NOT receive `agentId` / `userId` / `fileId` — by design. It can't fetch the audio bytes, and that's the point. If you find yourself needing those props, you're probably trying to bring back playback — go re-read the design notes above first.
- `transcript?.trim() ?? ''` is the sole input contract. Pass whatever raw transcript is on the attachment; the component handles whitespace, null, and empty strings uniformly.
- The compact-mode narrow width (`max-w-[260px]`) is tuned to the input bar layout. Changing the textarea or the input row's gap may require a tweak here.

## New-joiner traps

- Regular audio file uploads (Paperclip / drag-drop) DO NOT and MUST NOT route through this component. They DO have a transcript (backend transcribes ALL audio/* uploads regardless of source — `source` is purely a frontend dispatch hint), but rendering them as voice memos would mislead users into thinking the agent dictated the file's contents back to them. The transcript still flows to the agent via `Attachment.synthesize_marker` in the system prompt — it's just hidden in the UI for file uploads.
- Don't add a "play audio" link or button here. If product ever wants playback back, the right move is restoring `AudioMessage` as a separate component, not bolting playback onto the dictation primitive.

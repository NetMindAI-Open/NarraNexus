---
code_file: frontend/src/components/chat/AudioRecorder.tsx
last_verified: 2026-07-18
stub: false
---

## 2026-07-18 — onPreflight 注释去开关化（行为不变）

两处注释举例"user toggled off 'Use free quota'"——该开关已随免费额度偏好
删除（[[provider_resolver]]），改为真实存在的过期场景（额度用尽/provider
被删）。re-probe 机制本身不变且依然必要。

# AudioRecorder.tsx — Voice capture button for the chat input bar

## Why it exists

Phase 2 of the multimodal-audio rollout. Phase 1 already gave the backend a Whisper transcription path for any uploaded `audio/*` file — but the only way to feed it audio was Paperclip / drag-drop / paste. This component closes the gap: a one-button browser recorder that produces a `File` and hands it to the same `uploadAttachments(...)` path. From the backend's perspective a recorded clip and a drag-dropped mp3 are indistinguishable.

Keeping recording logic in its own component is deliberate — `ChatPanel` is already large, and MediaRecorder lifecycle (permission state, track cleanup, MIME picking) is orthogonal to the rest of the chat input.

## Upstream / Downstream

- **Used by**: `ChatPanel` — rendered inline next to the Paperclip button. Receives `onRecorded` (forwards to `uploadAttachments`) and `onError` (surfaced through the same `transcriptionNotice` banner used for "no OpenAI provider configured").
- **Calls**: browser-native `navigator.mediaDevices.getUserMedia` and `MediaRecorder`. No NarraNexus utilities — the produced `File` flows through the existing attachment pipeline.

## Design decisions

**Single button with state-swapped content rather than two buttons.** Idle → mic icon. Recording → red pill with elapsed time + stop glyph. Denied → red AlertCircle that retries on click. This keeps the input bar's column count stable across states, and the recording-state pill is wide enough to read the timer without overlapping the textarea.

**MIME picked dynamically via `MediaRecorder.isTypeSupported`.** Chrome / Edge / Firefox prefer `audio/webm;codecs=opus`; Safari (iOS 14.3+, macOS 14.1+) only supports `audio/mp4`; older Firefox needs `audio/ogg;codecs=opus`. The probe falls through to the empty default (browser-picked) when nothing matches. The chosen extension is what the upload route's libmagic sniffer reads — `.webm`, `.mp4`, `.ogg` are all in `audio_transcription.SUPPORTED_AUDIO_EXTENSIONS`.

**Self-contained state machine, no Zustand store.** Recording state never needs to be observed by other components — only the produced `File` matters. Local `useState` keeps the surface area minimal.

**`File` not `Blob` to the parent.** The upload API expects a `File` (it reads `.name`). We synthesize `voice_${Date.now()}.${ext}` so each clip is uniquely named and the extension survives backend MIME sniffing. Upload sniffer doesn't trust filename alone — it uses libmagic on the bytes — but the extension is the second-line fallback and a clean filename is what the user sees in the chip preview.

**`unsupported` returns `null` (no fallback button).** On iOS < 14.3 / unmaintained browsers, MediaRecorder is missing entirely. Rendering nothing degrades gracefully — Paperclip still works, the layout just loses one column. A "your browser doesn't support recording" toast was considered and rejected as noise; users on those browsers know.

**Permission denial recoverable.** When `getUserMedia` throws `NotAllowedError`, state goes to `denied` and the icon switches to a clickable AlertCircle. Users who fix the permission via browser UI can click again to retry without remounting the component. The `onError` callback also fires so `ChatPanel` shows a banner explaining how to fix it.

## Gotchas

- **Always `track.stop()` on every getUserMedia track.** Otherwise the OS-level mic indicator (red dot in tab title / menu bar) keeps glowing after recording ends. `cleanupStream()` runs from `onstop`, from the catch in `startRecording`, and from `useEffect` cleanup on unmount.
- **Clear `chunksRef.current` on every start.** MediaRecorder fires `ondataavailable` repeatedly and writes into the same array. Reusing the array across sessions would concatenate the previous recording onto the new one.
- **Assemble the blob in `onstop`, not `ondataavailable`.** The last chunk only arrives after `recorder.stop()` schedules the final flush. Building the blob inside `ondataavailable` produces a clip missing its tail.
- **`recorder.start(250)` (250 ms timeslice) gives a steady stream of chunks.** This is mostly aesthetic — without a timeslice the recorder buffers everything internally and only emits at stop, which is fine but offers no opportunity for chunked upload later. Keeping timeslice in place leaves room for streaming-Whisper in a future phase.
- **`MediaRecorder.isTypeSupported` can throw on Safari** for some unusual MIME strings. The probe wraps each call in try/catch so a thrown probe doesn't break the whole picker.

## New-joiner traps

- The button is rendered with the same height (`52px`) as Paperclip and Send so the input bar stays vertically aligned. If you change one, change all three.
- `onRecorded` MUST go through `uploadAttachments` — don't call the upload API directly. The shared path handles the `transcription_available=false` notice, `pendingAttachments` chip, and `uploadingCount` badge. Branching here would split that surface.
- Permission state isn't persisted across sessions. If you find yourself wanting to remember "this user already denied mic", that's a sign you're working around the permission UX rather than fixing it — re-evaluate.

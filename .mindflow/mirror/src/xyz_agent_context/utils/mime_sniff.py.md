---
code_file: src/xyz_agent_context/utils/mime_sniff.py
last_verified: 2026-07-22
stub: false
---

# mime_sniff.py — the single tiered MIME sniffer

## Why it exists

Three entry points receive file bytes plus untrusted naming metadata (browser
chat uploads via [[agents_attachments]], IM-channel downloads via
[[channel_trigger_base]], team-chat uploads via [[teams]]) and each used to
carry its own sniffing copy with subtly different tiering. The divergence was
user-visible: the team-upload copy returned libmagic's
``application/octet-stream`` verdict directly, so a ``.md``/``.csv`` upload
classified as octet-stream on the team path but got its real MIME on the IM
path — and ``mime_type`` drives both the frontend category (thumbnail vs grey
chip) and whether Whisper runs (``audio/*``). PR #141 review consolidated all
three onto this one helper.

## Tiering (first hit wins)

libmagic (an ``octet-stream`` verdict means "no idea" → fall through) →
extension guess → client/platform-supplied type → ``octet-stream``. The
client type is deliberately LAST resort — it's user-controlled — but it also
serves as the audio/video container tiebreaker
(``_audio_video_container_override``, hoisted verbatim from
agents_attachments): WebM/Ogg/MP4 headers look identical for audio-only and
audio+video, so a ``video/<container>`` verdict flips to ``audio/`` when the
client tagged the SAME container as audio. That override is what keeps
in-browser voice memos transcribable.

## Gotcha

For the IM-channel caller the platform ``hint`` used to outrank the extension
guess; under the unified tiering it now plays the client-type role (tiebreaker
+ last resort). The two only disagree when a platform supplies a MIME that
contradicts the file's own extension — and content sniffing still outranks
both.

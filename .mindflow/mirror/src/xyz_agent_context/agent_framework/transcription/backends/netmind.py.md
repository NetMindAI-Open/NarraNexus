---
code_file: src/xyz_agent_context/agent_framework/transcription/backends/netmind.py
last_verified: 2026-05-07
stub: false
---

# netmind.py — NetMind /v1/generation submit+poll backend

## Why it's its own backend

NetMind's STT shape is fundamentally different from OpenAI's:

- Async job model (submit → poll), not one-shot multipart
- Body is JSON `{model, config: {audio_url, ...}}`, not multipart
- Decoder is Python `soundfile`, which doesn't accept webm/opus
- Worker fetches audio over HTTP from a publicly-reachable URL

Trying to share code with the multipart backend would force one or
the other into uncomfortable shapes. We share the credential type
and the never-raise contract; the implementation is its own.

## State machine

```
POST /v1/generation              → 200 { id, status: "pending" }
loop:
  GET /v1/generation/{id}        → 200 { status, result?, logs? }
  status in {pending, initializing}: sleep 0.8s, retry
  status == "completed":           extract result.data[0].text → return
  status in {failed, cancelled, error}: log last logs[].text, return None
overall budget: 55s (well under base.BACKEND_TIMEOUTS_S=60s,
                     leaves room for one extra poll cycle)
```

Timing reference (from 2026-05-07 probe): 14-second mp3 took 7s pending
+ 11s processing = 18s end-to-end. The 55s budget gives 3× headroom
for queue spikes; longer than that and we'd rather return None and let
the user re-record than block the upload route any further.

## Lazy ffmpeg transcode

NetMind's soundfile rejects webm/m4a/mp4 — verified by
2026-05-07-probe-2 with the canonical "Soundfile is either not in the
correct format" error. The backend transcodes those formats to mp3
on demand, caching the output as `{file_path}.with_suffix(".mp3")`.

Native formats (mp3, wav, flac, ogg, oga, aiff) skip transcoding
entirely and are served via `variant="original"` on the public URL.

ffmpeg invocation: `-ac 1 -ar 16000 -c:a libmp3lame -b:a 64k`. Mono
16 kHz mp3 — voice-band audio doesn't benefit from stereo or higher
sample rates, and lower bitrate means smaller bytes for NetMind to
fetch. 30s transcode timeout cap.

## Gotchas

- **`shutil.which("ffmpeg")`** check before invoking. ffmpeg not
  installed → log warning, return None. The deployment story is
  "install ffmpeg in the EC2 image / Dockerfile"; if someone forgets,
  NetMind backend silently degrades rather than every transcription
  failing with a confusing subprocess error.
- **Submit with bad audio_url returns 200**. The error only shows up
  on poll, in `logs[].text`. We log the last log line on failed
  status so triage doesn't have to dig through CloudWatch for the
  "Soundfile malformed" message.
- **Public-URL minting failure → return None silently**. If
  `settings.transcription_hmac_secret` is unset in cloud mode,
  `url_signer.mint` raises RuntimeError. The backend catches and
  degrades — resolver should have skipped this credential but if it
  didn't, we don't crash.
- **Transcode cache reuse is unconditional** when the cached file
  exists with size > 0. Source filenames are immutable
  (`{file_id}.{ext}` only-written-once), so stale-cache concerns
  don't apply.
- **`del language` is wrong** — we DO pass language through to
  NetMind's `config.language` field when set. If you find yourself
  removing it, double-check the request body construction below.

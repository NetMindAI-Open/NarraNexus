---
name: netmind-transcribe
description: Transcribe audio (voice notes, recordings, meeting audio) to text via NetMind's Whisper model. Use when the user shares an audio file or URL and your model cannot hear audio. Zero config for NetMind-powered users — the API key is injected automatically.
version: 1.0.0
metadata:
  clawdbot:
    requires:
      env: ["NETMIND_API_KEY"]
      bins: ["python3"]
---

# NetMind Transcribe — audio-to-text fallback

Transcribe an audio file or audio URL to text using `openai/whisper` served
by NetMind.

## When to use

- The user shares an audio file (wav / flac / ogg / aiff / mp3) or a link to
  one and asks what it says, or you need the content for a larger task.
- Note: voice messages arriving through IM channels are usually transcribed
  by the platform already — use this skill for audio FILES in the workspace
  or audio URLs.

## How to use

```bash
python3 skills/netmind-transcribe/scripts/transcribe.py <audio_path_or_url>
```

Examples:

```bash
python3 skills/netmind-transcribe/scripts/transcribe.py files/meeting.mp3
python3 skills/netmind-transcribe/scripts/transcribe.py "https://example.com/audio.wav"
```

The transcript is printed to stdout.

## Configuration

- `NETMIND_API_KEY` — injected automatically for NetMind-powered users; can
  be overridden in the Skill tab's config panel.
- `NETMIND_NATIVE_BASE` (optional) — defaults to `https://api.netmind.ai`.
- `NETMIND_BASE_URL` (optional) — OpenAI-compatible base, defaults to
  `https://api.netmind.ai/inference-api/openai/v1`.

## Known limitations

- **Audio URLs work reliably** (NetMind's async Whisper worker fetches the
  URL). The URL must be publicly reachable by NetMind's servers.
- **Local files** are attempted through NetMind's OpenAI-compatible
  transcription endpoint, which is currently unstable server-side. If it
  fails, the script says so — offer the user the URL route, or (in cloud
  deployments) rely on the platform's built-in voice transcription.

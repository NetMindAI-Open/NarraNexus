"""Transcribe audio to text via NetMind's Whisper model.

Usage: python3 transcribe.py <audio_path_or_url>

- http(s) URL  -> NetMind native /v1/generation flow (submit + poll); the
  URL must be publicly reachable by NetMind's workers.
- local file   -> OpenAI-compatible /audio/transcriptions multipart. This
  NetMind endpoint is currently unstable server-side; failures are reported
  clearly so the caller can fall back to the URL route.

Stdlib only. Auth from NETMIND_API_KEY (platform-injected for NetMind users).
"""

import json
import mimetypes
import os
import sys
import time
import urllib.error
import urllib.request
import uuid

NATIVE_BASE = os.environ.get("NETMIND_NATIVE_BASE", "https://api.netmind.ai").rstrip("/")
OPENAI_BASE = os.environ.get(
    "NETMIND_BASE_URL", "https://api.netmind.ai/inference-api/openai/v1"
).rstrip("/")
MODEL = "openai/whisper"
POLL_SECONDS = 2.0
POLL_BUDGET_SECONDS = 180


def fail(message: str) -> None:
    print(f"ERROR: {message}", file=sys.stderr)
    sys.exit(1)


def _request(url: str, api_key: str, data=None, content_type=None):
    headers = {"Authorization": f"Bearer {api_key}"}
    if content_type:
        headers["Content-Type"] = content_type
    return urllib.request.Request(url, data=data, headers=headers)


def transcribe_url(audio_url: str, api_key: str) -> str:
    body = json.dumps(
        {"model": MODEL, "config": {"audio_url": audio_url, "task": "transcribe"}}
    ).encode("utf-8")
    with urllib.request.urlopen(
        _request(f"{NATIVE_BASE}/v1/generation", api_key, body, "application/json"),
        timeout=30,
    ) as response:
        job = json.load(response)
    job_id = job.get("id") or (job.get("data") or {}).get("id")
    if not job_id:
        fail(f"unexpected submit response: {json.dumps(job)[:200]}")

    deadline = time.monotonic() + POLL_BUDGET_SECONDS
    status = "pending"
    while time.monotonic() < deadline:
        time.sleep(POLL_SECONDS)
        with urllib.request.urlopen(
            _request(f"{NATIVE_BASE}/v1/generation/{job_id}", api_key), timeout=15
        ) as response:
            state = json.load(response)
        status = state.get("status")
        if status == "completed":
            data = (state.get("result") or {}).get("data") or []
            if data and data[0].get("text") is not None:
                return data[0]["text"]
            fail(f"completed but no transcript in response: {json.dumps(state)[:200]}")
        if status == "failed":
            logs = state.get("logs") or []
            detail = logs[-1].get("text") if logs else "no detail"
            fail(
                f"NetMind transcription job failed: {detail}. "
                "The URL must be publicly reachable by NetMind's servers."
            )
    fail(f"transcription still '{status}' after {POLL_BUDGET_SECONDS}s — try again later")
    return ""  # unreachable


def transcribe_file(path: str, api_key: str) -> str:
    if not os.path.isfile(path):
        fail(f"audio file not found: {path}")
    mime = mimetypes.guess_type(path)[0] or "application/octet-stream"
    with open(path, "rb") as f:
        audio = f.read()

    boundary = f"----narranexus{uuid.uuid4().hex}"
    parts = (
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"model\"\r\n\r\n{MODEL}\r\n"
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; "
        f"filename=\"{os.path.basename(path)}\"\r\nContent-Type: {mime}\r\n\r\n"
    ).encode("utf-8") + audio + f"\r\n--{boundary}--\r\n".encode("utf-8")

    try:
        with urllib.request.urlopen(
            _request(
                f"{OPENAI_BASE}/audio/transcriptions",
                api_key,
                parts,
                f"multipart/form-data; boundary={boundary}",
            ),
            timeout=180,
        ) as response:
            payload = json.load(response)
        return payload.get("text") or json.dumps(payload)
    except urllib.error.HTTPError as exc:
        detail = exc.read()[:200].decode("utf-8", errors="replace")
        fail(
            f"NetMind's file-transcription endpoint returned {exc.code} ({detail}). "
            "This endpoint is currently unstable on NetMind's side. If the audio "
            "is available at a public URL, retry with the URL instead."
        )
    return ""  # unreachable


def main() -> None:
    if len(sys.argv) < 2:
        fail("usage: transcribe.py <audio_path_or_url>")
    api_key = os.environ.get("NETMIND_API_KEY")
    if not api_key:
        fail(
            "NETMIND_API_KEY is not set. Ask the user to enable NetMind Power "
            "in Settings → Models, or configure this skill's key in the Skill tab."
        )
    target = sys.argv[1]
    if target.startswith(("http://", "https://")):
        print(transcribe_url(target, api_key))
    else:
        print(transcribe_file(target, api_key))


if __name__ == "__main__":
    main()

"""Describe or answer questions about an image via NetMind's vision model.

Usage: python3 describe_image.py <image_path> [question]

Uses only the Python standard library. Auth comes from NETMIND_API_KEY,
which the NarraNexus platform injects automatically for NetMind-powered
users (an explicit value in the skill's config panel overrides it).
"""

import base64
import json
import mimetypes
import os
import sys
import urllib.error
import urllib.request

DEFAULT_MODEL = "Qwen/Qwen3-VL-235B-A22B-Instruct"
DEFAULT_BASE = "https://api.netmind.ai/inference-api/openai/v1"
MAX_BYTES = 10 * 1024 * 1024


def fail(message: str) -> None:
    print(f"ERROR: {message}", file=sys.stderr)
    sys.exit(1)


def main() -> None:
    if len(sys.argv) < 2:
        fail("usage: describe_image.py <image_path> [question]")

    api_key = os.environ.get("NETMIND_API_KEY")
    if not api_key:
        fail(
            "NETMIND_API_KEY is not set. Ask the user to enable NetMind Power "
            "in Settings → Models, or configure this skill's key in the Skill tab."
        )

    image_path = sys.argv[1]
    question = sys.argv[2] if len(sys.argv) > 2 else "Describe this image in detail."
    if not os.path.isfile(image_path):
        fail(f"image file not found: {image_path}")
    if os.path.getsize(image_path) > MAX_BYTES:
        fail("image exceeds 10 MB — downscale it first (e.g. with sips or ffmpeg)")

    mime = mimetypes.guess_type(image_path)[0] or "image/png"
    with open(image_path, "rb") as f:
        data_uri = f"data:{mime};base64," + base64.b64encode(f.read()).decode("ascii")

    body = json.dumps(
        {
            "model": os.environ.get("NETMIND_VISION_MODEL", DEFAULT_MODEL),
            "max_tokens": 1500,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": question},
                        {"type": "image_url", "image_url": {"url": data_uri}},
                    ],
                }
            ],
        }
    ).encode("utf-8")

    base = os.environ.get("NETMIND_BASE_URL", DEFAULT_BASE).rstrip("/")
    request = urllib.request.Request(
        f"{base}/chat/completions",
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as exc:
        detail = exc.read()[:300].decode("utf-8", errors="replace")
        fail(f"NetMind API returned {exc.code}: {detail}")
    except Exception as exc:  # noqa: BLE001
        fail(f"request failed: {exc}")

    try:
        print(payload["choices"][0]["message"]["content"])
    except (KeyError, IndexError):
        fail(f"unexpected response shape: {json.dumps(payload)[:300]}")


if __name__ == "__main__":
    main()

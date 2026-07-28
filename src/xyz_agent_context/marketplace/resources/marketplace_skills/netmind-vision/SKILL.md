---
name: netmind-vision
description: Understand images (photos, screenshots, charts, documents) via NetMind's vision model. Use whenever the user shares or references an image file and your own model cannot see images, or when you need a second visual opinion. Zero config for NetMind-powered users — the API key is injected automatically.
version: 1.0.0
metadata:
  clawdbot:
    requires:
      env: ["NETMIND_API_KEY"]
      bins: ["python3"]
---

# NetMind Vision — image understanding fallback

Describe, read, or answer questions about any local image file using a
vision-language model served by NetMind.

## When to use

- The user sent or referenced an image (png / jpg / jpeg / gif / webp / bmp)
  and your model cannot process images natively.
- You need to read text from a screenshot, interpret a chart, or describe a
  photo as part of a larger task.

## How to use

```bash
python3 skills/netmind-vision/scripts/describe_image.py <image_path> "<your question>"
```

Examples:

```bash
python3 skills/netmind-vision/scripts/describe_image.py files/chart.png "Summarize the trend in this chart"
python3 skills/netmind-vision/scripts/describe_image.py shot.jpg "Transcribe all visible text"
```

The script prints the model's answer to stdout. Treat that output as your
"eyes": incorporate it into your reasoning and answer the user in your own
words.

## Configuration

- `NETMIND_API_KEY` — injected automatically for users whose model
  configuration uses NetMind. If the script reports a missing key, tell the
  user to either enable NetMind Power in Settings → Models, or enter an API
  key in the Skill tab's config panel for this skill.
- `NETMIND_VISION_MODEL` (optional) — defaults to
  `Qwen/Qwen3-VL-235B-A22B-Instruct`.
- `NETMIND_BASE_URL` (optional) — defaults to
  `https://api.netmind.ai/inference-api/openai/v1`.

## Notes

- Max image size ~10 MB; larger files should be downscaled first.
- One image per call; for multiple images, call the script once per image.

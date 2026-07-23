---
code_file: marketplace_skills/netmind-vision/scripts/describe_image.py
last_verified: 2026-07-21
stub: false
---

# describe_image.py

Stdlib-only vision call: base64 data-URI into NetMind's OpenAI-compatible
chat/completions (default model Qwen/Qwen3-VL-235B-A22B-Instruct). Verified
live 2026-07-21 (red-square + text-reading tests). Clear operator errors:
missing key → points user to NetMind Power settings; >10MB → downscale hint.

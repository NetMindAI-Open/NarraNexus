---
code_file: scripts/spikes/nexus_power_vs_claude_bench.py
last_verified: 2026-07-29
stub: false
---
# spikes/nexus_power_vs_claude_bench — 双 driver 同上下文对照

Owner 验收门:PR 前 nexus_power 须与 claude_code 表现相当。强制 sqlite+local 环境护栏(repo .env 指向 prod!),读本机 llm_config.json 取 anthropic 协议钥匙,两 driver 同 messages 对跑,量 wall/TTFT/工具行为/usage。2026-07-29 首轮:chat 相当、tool 场景 nexus 快 32% 且上下文足迹约 1/5,首事件慢 ~3s(子进程冷启,待池化)。

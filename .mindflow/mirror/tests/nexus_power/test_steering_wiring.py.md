---
code_file: tests/nexus_power/test_steering_wiring.py
last_verified: 2026-08-21
stub: false
---
# tests/steering_wiring — run_turn_events/serve_turn 穿线

假 loop + 假 model client(monkeypatch)驱动 run_turn_events,锁 steering inlet 被原样挂上 assembly;默认 None→NullSteeringInlet;serve_turn 转发同验。drain 行为不在此(在 loop_e2e)。

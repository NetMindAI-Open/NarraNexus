---
code_file: src/xyz_agent_context/agent_framework/nexus_power/__init__.py
last_verified: 2026-07-29
stub: false
---
# nexus_power — 自研 agent-loop 框架的包入口

框架身份宣言:文本=内心独白、对外一切经工具、停止=不再有动作。公开面只有 assembly(TurnRequest/run_turn_events)+ contracts;消费方是 [[nexus_agent]] driver 与 runner 进程宿主。框架中性:只认识 Expandable/TurnOptions,不认识 module/用户。不 re-export(铁律 #23)。

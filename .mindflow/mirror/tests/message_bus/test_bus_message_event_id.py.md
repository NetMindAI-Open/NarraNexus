---
code_file: tests/message_bus/test_bus_message_event_id.py
last_verified: 2026-07-31
stub: false
---

# test_bus_message_event_id.py

钉住 2026-07-31 "team 成员运行详情" 修缮的核心数据链(issue Step 1 的回归
测试):`send_message(event_id=…)` 在 `bus_messages` 行与 `BusMessage` 模型上
往返;默认 None;以及 trigger 团队分支把 `_invoke_runtime` 返回的 turn
event_id 落到发回房间的回复行上——这条链断了,前端每条消息的 reasoning
展开条就永远不出现(列永远 NULL,静默降级)。

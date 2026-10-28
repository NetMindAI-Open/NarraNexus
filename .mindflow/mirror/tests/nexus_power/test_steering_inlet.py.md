---
code_file: tests/nexus_power/test_steering_inlet.py
last_verified: 2026-08-21
stub: false
---
# tests/steering_inlet — QueueSteeringInlet 单元

drain FIFO 顺序即清空/空队列非阻塞/drain 间隙 put 下次可见/满足 SteeringInlet 协议(isinstance+协程+list)。端到端"注入进下次请求"在 loop_e2e。

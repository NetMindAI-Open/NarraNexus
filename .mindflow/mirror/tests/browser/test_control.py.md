---
code_file: tests/browser/test_control.py
last_verified: 2026-09-24
stub: false
---

# 人与 Agent 的浏览器控制权

保证人工接管必须显式发生，旁观时的输入不会抢占 Agent。接管期间 Agent
等待交回控制，不把等待变成失败或设置时长上限；关闭会话则释放等待者，
避免任务永久悬挂。可序列化状态让界面与执行侧表达同一个控制权事实。

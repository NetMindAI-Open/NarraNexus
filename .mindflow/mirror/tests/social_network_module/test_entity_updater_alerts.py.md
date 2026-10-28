---
code_file: tests/social_network_module/test_entity_updater_alerts.py
stub: false
last_verified: 2026-08-25
---
# test_entity_updater_alerts.py — 记忆写入失败要可分辨、可上报

钉 [[_entity_updater.py]] 的两条性质：

1. **失败可与空结果分辨**：LLM 挂了返回 `None`，跑通了没找到东西返回 `""`，
   且后者**不上报**（干净的空结果不是故障）。
2. **失败会上报**：五个 LLM 调用点各带正确的 `source` 标签；三个 DB 写入点
   走审计行而**不打扰 owner**——一次失败的 UPDATE 不是用户换 key 能修的，
   为它发收件箱通知是 alarm fatigue。

`compress_description` 那条单独存在，因为它**返回值看起来是成功的**
（截断后的文本），却静默丢掉了切口之后的全部内容——这类「假成功」是最难
从调用方发现的一种失败。

`_report_llm_failure` / `_report_write_failure` 两个上报函数在测试里被
monkeypatch 掉，所以断言的是「谁在什么情况下被调用、带什么标签」，而不是
去验证 InboxRepository / ServiceAuditor 的行为——那两者有自己的测试。

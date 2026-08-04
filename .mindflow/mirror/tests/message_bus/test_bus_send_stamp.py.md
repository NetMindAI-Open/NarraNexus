---
code_file: tests/message_bus/test_bus_send_stamp.py
last_verified: 2026-08-03
stub: false
---

## 2026-08-03 — 为什么存在

钉住「bus 消息的 turn-source 章按**发给谁**定，不按整轮定」。

PR #229 review round 4 抓到的自伤：整轮盖 `BUS_ERRAND_TURN_SOURCE` 让 P1 换个
座位复发。平台每轮把跨 channel 的未读注进 context（[[local_bus]]
`get_unread` 是跨成员表 JOIN）并在提示词里要求回答，所以「差事延续轮次里顺手
回答另一个同伴 C」是常规路径；整轮盖章后 C 把**回答**读成提问，于是 C 不再向
自己 owner 回报——正是本 PR 要修的失败。

因此本文件的主测试是那个第三步：`_send_turn_source(to_agent=其他同伴)` 必须
仍是 plain `"message_bus"`，而 `to_agent=差事对手` 才升级。另有 codex 形状
（只发 bearer）、owner 面轮次不升级、无作用域、无 header 各一条。

改动 `_send_turn_source` 或 bearer 字段约定（[[_mcp_identity]]）时，这个文件
是回归网。

---
code_file: tests/message_bus/test_bus_service_protocol.py
last_verified: 2026-08-03
stub: false
---

## 2026-08-03 — 为什么存在

守协议层与实现层的**签名漂移**:PR #229 review 抓到 `sender_turn_source`
只加在 [[local_bus]] 和调用方,[[message_bus_service]] 抽象方法和
[[cloud_bus]] stub 没跟上。这种漂移 import 不报错、mypy 对着协议也看不见,
只有 `inspect.signature` 逐参数比对能拦。三条防线:参数名序 + 默认值逐一
相等、协议层必须带 `sender_turn_source=None`、cloud stub 传该 keyword 时
必须到达 NotImplementedError 而非 TypeError(后者会盖掉真实信号)。

新增 bus send 参数时,这个文件会替你逼三层同步。

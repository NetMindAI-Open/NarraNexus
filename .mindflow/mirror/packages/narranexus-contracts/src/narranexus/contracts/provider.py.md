---
code_file: packages/narranexus-contracts/src/narranexus/contracts/provider.py
last_verified: 2026-09-03
stub: false
---

## 2026-09-03 — provider driver 的结构化契约

`ProviderDriver` 复刻 `providers/driver/base.py::Driver` 的方法面（`driver_type` 类方法、五个
`build_*_config`、`probe`、`models`），但返回类型全用 `Any`：具体 config 类型
（ClaudeConfig/OpenAIConfig/...）仍在遗留包 `api_config`，契约层是叶子不能 import 它们。
这不是偷懒——Protocol 本来就是结构化的，方法名与签名才是契约；类型在批 3 抽取
`builtin.providers` 时随 `api_config` 一起进契约层。`base.Driver` 暂不改（批 1 迁注册表时
让它 re-export 本 Protocol）。

---
code_file: packages/narranexus-contracts/src/narranexus/contracts/testing/llm_client.py
last_verified: 2026-09-07
stub: false
---

## 2026-09-03 — `llm_client` 契约的可执行定义（预审补齐）

预审指出 llm_client 契约原先只有同义反复的断言。基类比较实现的 `llm_function`/`llm_stream`
参数名列表与 `LlmClient` Protocol 完全一致（参数漂移在这里红，而不是在三层之外的调用点），并
断言前者是协程函数、后者是异步生成器函数。三个内置 helper SDK 在
`tests/nx_kernel/contracts/test_kind_contracts.py` 里逐个跑。

## 2026-09-07（round-2 🟡-1）— the base drives the implementation, not just its shape

Round 1 asked for executable contract suites; what landed only read `isinstance` / `hasattr` /
`iscoroutinefunction` / `inspect.signature`, so any behavioural regression stayed green.
The llm_client base gains an optional `model_resolver` and drives it: the configured slot model must win over the client's own default, and the `default` sentinel must never be sent as a model id. Signature comparison cannot see a client that quietly prefers a hardcoded model while the settings page shows another.

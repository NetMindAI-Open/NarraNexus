---
code_file: packages/narranexus-contracts/src/narranexus/contracts/testing/provider.py
last_verified: 2026-09-07
stub: false
---

## 2026-09-03 — `provider` 契约的可执行定义

测试模块子类化本基类并指定实现（工厂或类），继承同一组检查；内置实现在
`tests/nx_kernel/contracts/test_kind_contracts.py` 里就是这样跑的（nexus_power 与 remote driver、
NetMind provider、event memory kind）。检查只看结构（签名、关键字参数形态、词表），不需要
凭据与网络，所以第三方在 CI 里零配置可跑。

## 2026-09-07（round-2 🟡-1）— the base drives the implementation, not just its shape

Round 1 asked for executable contract suites; what landed only read `isinstance` / `hasattr` /
`iscoroutinefunction` / `inspect.signature`, so any behavioural regression stayed green.
The provider base gains an optional `card_factory`; when set it builds a driver and calls every `build_*_config` with a sentinel model, asserting the model comes back out. `NotImplementedError` stays conformant ("this card cannot fill that slot"); a config for a DIFFERENT model is the silent substitution the shape checks could never see. `models()` is driven too. All pure translation — no credentials, no network.

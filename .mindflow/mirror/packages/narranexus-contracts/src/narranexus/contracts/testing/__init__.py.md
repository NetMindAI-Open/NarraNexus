---
code_file: packages/narranexus-contracts/src/narranexus/contracts/testing/__init__.py
last_verified: 2026-09-03
stub: false
---

## 2026-09-03 — 契约测试基类的包

「契约是可执行的」的落点：每个 kind 一个 `Abstract<Kind>ContractTests` 基类，内置实现与第三方
实现跑同一套（spec §5.5、参考文档 §E-25）。这里不 import pytest：基类是普通类，测试模块
子类化后由 pytest 收集其 `test_*` 方法，这样 SDK 使用者不必依赖我们的测试栈版本。

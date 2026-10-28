---
code_file: packages/narranexus-sdk/src/narranexus/sdk/__init__.py
last_verified: 2026-09-07
stub: false
---

## 2026-09-07 — channel 作者表面惰性 re-export（批 6c，G2-I6）

`narranexus plugin new --kinds channel` 生成的代码里，8 个 import 有 7 个指向
`narranexus.platform.*`——`docs/API_POLICY.md` §1 说那不是公开 API，
`scripts/dev/api_check.sh`（griffe，只 diff contracts + sdk）也看不见它们变化。
换句话说：我们把非公开 API 打包发给外部作者，然后在一次 patch 升级里静默打断他们。
现在 9 个名字从这里 re-export，门禁因此能看见它们。

配套的 `if TYPE_CHECKING:` 块不是给 pyright 的装饰——PEP 562 的 `__getattr__` 对静态分析
完全不透明，没有那个块，griffe 根本看不见这九个名字，「re-export 让门禁看见它们」就成了
一句门禁并不兑现的话。

**惰性**（PEP 562 `__getattr__`）是必须的：这些类住在 `narranexus.platform`，
会拖起数据库栈和 module system。而 SDK 同时也是只装了 contracts 的消费者会 import 的东西
（CLI 的 scaffold 检查、插件自己的单测），让每次 `import narranexus.sdk` 都付这个代价
正好是批 6 花预算在反的方向。

`GenericCredentialStore` 是里面最不干净的一个：它是插件手动 new 的平台**类**。
正确形态是 `ServiceRef` / `PluginContext` 上的「给我这个 channel 在这个 agent 上的凭据」，
但那要动 `platform/channel/channel_module_base.py`（批 4 的地盘），
所以记为 follow-up（expires 2026-12-31，写在源码注释里），先原样 re-export
让模板有唯一 import 根。

## 2026-09-03（批 2e）— Python SDK：插件作者的唯一门

re-export 契约类型、`Contribution`、`hookimpl`、`ServiceRef`、`PluginContext`——不是第二份拷贝，是稳定的门。
插件只 import 这里（或 `narranexus.contracts`），永不 import `narranexus.kernel` 内部。独立 PyPI 包
`narranexus-sdk` 是批 6 的事。

## 2026-09-07 — 再导出流水线/上下文/渠道契约（B10）

新增 Stage/StageStrategy/PipelineProfile/ContextProvider/ChannelDescriptor/ChannelUi/CredentialField/CredentialSchema/TriggerSpec：stage_strategy/pipeline_profile/context_provider/channel 四个脚手架模板只从 narranexus.sdk 取名字。

## 2026-09-07（round-2 A2-6）— the framework contracts are exported

`AgentLoopDriver`, `FrameworkMeta`, `FrameworkInstall`, `InstallComponent` and
`CAPABILITY_VOCABULARY` are re-exported. "Swap the agent-loop framework" is the headline extension
point and the one ToB request Owner keeps raising, yet an author had to reach past the SDK into
`narranexus.contracts.framework` for it — the import line was telling them the seam was unsupported.
`templates/framework` scaffolds against exactly these names.

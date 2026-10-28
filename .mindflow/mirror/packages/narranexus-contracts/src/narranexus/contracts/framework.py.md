---
code_file: packages/narranexus-contracts/src/narranexus/contracts/framework.py
last_verified: 2026-09-07
stub: false
---

## 2026-09-07 — `InstallComponent` docstring 例子改中性串

原例子写死了当时的 pin（`claude-agent-sdk==0.1.43`），pin 一 bump 契约文档就失真；现在用 `pkg==1.2.3` 形式，
并指明真 pin 只在各框架插件的 `FrameworkInstall` 一处。

## 2026-09-03（批 1）— `FrameworkInstall` / `InstallComponent`（D7 吸收）

框架的安装配方（pip/npm 组件与钉死版本、探测包名、展示哪个组件的版本、体积提示）成为契约数据
`FrameworkMeta.install`，随 `Contribution.meta["framework"]` 进注册表。dev 分支
`backend/integrations/plugins/registry.py` 那张手写的两条表现在从注册表派生
（`build_plugin_specs`），钉死版本只存在于 `agent_framework/__init__.py` 一处。
`InstallSpec` 改名 `InstallComponent`（与 backend 既有 `InstallComponent` 同名同义）。

## 2026-09-03（补注）— Protocol 里 `capabilities()` 不再带 `return set()` 默认体

Protocol 方法体是 `...`；每个 adapter 都自己实现 `capabilities()`，`runtime_checkable` 只查存在性，
所以没有消费者依赖旧的默认返回。刻意去掉，避免「继承 Protocol 就白得一个空实现」的错觉。

## 2026-09-03 — `AgentLoopDriver` 的正式家（从 loop/driver.py 搬来）

批 0 把 agent-loop 框架的 Protocol 从 `narranexus/platform/agent_framework/loop/driver.py`
搬到契约层，旧模块 re-export 同一个对象（`tests/nx_kernel/contracts/test_kind_contracts.py`
钉住 `driver.AgentLoopDriver is AgentLoopDriver`）。Protocol 正文与 docstring 逐字保留：
`agent_loop(messages, mcp_servers, *, streaming, extra_env, cancellation, **kwargs)` 异步生成器 +
`capabilities()`。新增两样：`CAPABILITY_VOCABULARY` frozenset（原来只是 docstring 里的词表，
现在是可断言的常量，契约测试基类用它）和 `FrameworkMeta/InstallSpec`（框架的静态描述，
吸收 dev 分支 `backend/integrations/plugins/spec.py` 的 pip/npm 安装描述，D7）。
`AgentEvent` TypedDict（response_processor 的隐式契约）留批 1。

## 2026-09-07 — FrameworkMeta 成为框架事实的唯一家（B6）

新增 protocol(anthropic|openai|any)/oauth_source/runtime_name/login_marker 四个带默认值的字段与 agent_protocols/self_description 两个派生属性。动机：宿主侧曾有七张按框架名硬编码的表（resolver 的 _KNOWN_AGENT_FRAMEWORKS、user_service 的 _SUPPORTED_AGENT_FRAMEWORKS、provider_schema 的 AGENT_FRAMEWORK_REQUIRED_PROTOCOLS 与 CLI_FRAMEWORK_BY_OAUTH_SOURCE、model_identity 的 FRAMEWORK_DISPLAY_NAMES、plugin_paths 的 _FRAMEWORK_PACKAGE、backend plugins service 的 _LOGIN_MARKERS），第三方框架注册了也进不了这些表。现在这些事实随 Contribution.meta['framework'] 进注册表，宿主在调用期派生（loop/driver.py 的 framework_meta 一族）。字段全带默认值=契约 additive（ui/framework api 版本不动）；没有 meta 的裸注册（测试假驱动）被视作 protocol=any 的宿主内置框架。cloud_policy 的 CLOUD_ALLOWED_FRAMEWORKS 刻意不进 meta：那是部署策略（未知框架默认锁云=fail-closed），不是框架能力。

## 2026-09-07（round-2 P2-I3 / G2-I7 / A2-5）— `FrameworkMeta` gains capabilities and the cloud-safety fact

Two fields, both fail-closed by default:

* `capabilities: frozenset[str]` — the STATIC twin of `AgentLoopDriver.capabilities()`, validated
  against `CAPABILITY_VOCABULARY` in `__post_init__` (a word outside it is a `ValueError` at
  construction, not a mystery later). It exists because some hosts must know a framework's capability
  WITHOUT constructing its driver: the remote executor shell decides whether to carry live steering
  over the hop, and history projection decides whether a turn can be replayed natively, both before
  any driver exists in that process. `native_replay` is the new vocabulary word for the second.
* `uses_shared_cli_login: bool = True` — can this framework authenticate from a credential FILE in
  the host's single HOME? Default `True` means a framework that never considered the question is
  treated as a credential rider, which is what the cloud gate refuses for non-staff. Only the
  operator's exemption list (`cloud_policy`) can widen that; a plugin must never be able to attest
  its own cloud safety.

The class docstring's claim that "every host-side table keyed on a framework name is derived from the
registry" was half true when written (three frozensets and one `!=` survived); it is true now, and
the docstring says which single thing a framework may NOT attest about itself.

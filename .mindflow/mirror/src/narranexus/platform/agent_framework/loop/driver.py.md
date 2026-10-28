---
code_file: src/narranexus/platform/agent_framework/loop/driver.py
last_verified: 2026-09-11
stub: false
---

## 2026-09-11 — the not-installed run error names Model Defaults too; one registry walk

- `FrameworkNotInstalledError`'s message now names both ways out: install
  the plugin (Settings → Plugins) or switch to an installed framework
  (Settings → Model Defaults, or the agent's Model & framework panel). This
  is the self-heal path for local users whose slot was bound to an
  uninstalled on-demand framework before onboarding skipped those: no
  migration rewrites stored `user_slots.agent_framework` (a silent rewrite
  would run their agents on a framework they did not choose), so the run
  error has to point at the fix, and the Model Defaults page can save that
  framework-only change even with the slot unbound.
- `default_framework_for_protocol` filters with `_meta_installed(meta)` (the
  probe behind `framework_installed`, taking the already-resolved meta), so
  `framework_metas()` is walked once instead of once per candidate.

## 2026-09-11 — `default_framework_for_protocol` only picks INSTALLED frameworks

Candidates are now filtered through `framework_installed` (the same probe the
driver's fail-closed gate uses) before the locked-protocol / protocol-agnostic
preference runs. Before this, on the lightweight local build (Claude Code /
Codex registered but shipped as on-demand plugins) a fresh user's first
anthropic card — official key, NetMind, free tier, Arena — landed on
`claude_code`, and every turn then raised `FrameworkNotInstalledError`: the
product was unusable out of the box. Now it lands on the first installed
anthropic-locked framework if one exists, else the first installed
protocol-agnostic one (host-shipped `nexus_power`). Where the plugins are
installed (cloud images, a desktop that installed them) the historical pairing
(anthropic → claude_code, openai → codex_cli) is unchanged. Availability is
derived, never a name list. Only caller: `UserProviderService.onboard_one_key`.

## 2026-09-03（预审修订）— 注册表路径 `turn.pipeline.act.framework`

同日早些的 `turn.act.framework` 因嵌套规则改成 `turn.pipeline` 的后代路径；对象仍来自
`KERNEL_REGISTRIES.registry_for(...)`。

## 2026-09-03 — `FRAMEWORK_REGISTRY` 改从 `KERNEL_REGISTRIES.registry_for("turn.act.framework")` 取

同一个对象，不再在本模块构造；契约版本与大小写归一化由内核门面按扩展位提供。

## 2026-09-03 — 注册表改用内核 `Registry[T]`（`FRAMEWORK_REGISTRY`），行为不变

`_REGISTRY: dict` 换成 `narranexus.kernel.plugins.registry.Registry`，键仍大小写不敏感、
选择优先级/错误文案不变（`UnknownEntry` 被翻译回原来的 `ValueError` 文案，调用方无感）。
两处可见变化：`register_agent_loop_driver` 现在**返回 `Disposable`**（测试夹具用它撤销注册，
取代直接 `_REGISTRY.pop`），并多一个 keyword-only `owner`（默认 `builtin.frameworks`，插件
loader 会传自己的 id）。覆盖注册仍允许（`replace=True`，spec 记为遗留口子，批 1 收紧）。
快照 `tests/snapshots/golden/registries.json` 证明三个框架名与顺序未变。

## 2026-09-03 — `AgentLoopDriver` Protocol 搬到 `narranexus.contracts.framework`，本模块 re-export

插件平台批 0：Protocol 正文（含 `capabilities()` 全部 docstring）逐字迁入契约层，本模块顶部
`from narranexus.contracts.framework import AgentLoopDriver` 保证 `driver.AgentLoopDriver` 仍是同一个
对象（契约测试钉住）。注册表 `_REGISTRY`、选择优先级、`FrameworkNotInstalledError`、executor seam
全部不动；注册表改用内核 `Registry[T]` 是下一提交的事。词表从 docstring 升格为
`CAPABILITY_VOCABULARY` 常量，`test_driver_contract.py` 的「只准声明已知词」以后应改为引用它。

## 2026-08-28 — fail-closed：未装插件的框架拒绝构建

新增异常 `FrameworkNotInstalledError(RuntimeError)`（带 `framework` 属性），与
「未知框架」的 `ValueError` 区分：名字合法但其可选 SDK 插件
（claude-agent-sdk/openai-codex）在轻量本地版尚未安装。`get_agent_loop_driver`
在 **remote-executor 短路之后、in-process 建 driver 之前**，**仅对
[[plugin_paths]] `PLUGIN_FRAMEWORKS`（claude_code/codex_cli）成员**查
`framework_installed(name)`，假则抛此异常（nexus_power 与任何自定义注册的 driver
不受门禁——注册即可用）——**绝不静默回落到别的
框架**（否则用户的 agent 会跑在没选的框架上）。只有 in-process 路径会到这；云端走
remote executor 且镜像预装，`framework_installed` 恒真。路由层捕获它、给前端
「去 设置→插件 安装」提示（`framework` 供按框架本地化）。`framework_installed`
用函数内 import 以避与本包 __init__ 的循环依赖。

## 2026-08-24 — remote driver **按 framework 声明 steering**(取代 2026-08-22 节「remote 空集/降级」)

2026-08-22 节的「remote HTTP driver 刻意保持空集、remote run 降级成新 turn」**已反转**。`capabilities()` 的 Protocol docstring 相应改了:remote driver 现对**可 steer 的 framework(nexus_power)返回 `{"steering"}`**、其余返回空集——它经 executor `/steer` + `steer_consumed` 帧承载 steering(见 [[remote_driver.py]] / [[executor_service.py]]),故答案**按 framework 而定**,不是一刀切空集。register 判据 `"steering" in driver.capabilities()` 不变;真正变的是 remote 那条臂现在对 nexus_power 为真。契约测试改名为 `test_steering_capability_is_declared_where_it_can_be_honored`(nexus_power remote 有、claude_code/codex_cli remote 无)。本文件本轮**只改 docstring**。

## 2026-08-22 — 空协商缝有了第一个消费者:steering

PR #351 起,`capabilities()` 不再是"全员空集"。`NexusAgent` 声明
`{"event_log", "steering"}`(见 [[nexus_agent.py]]),remote HTTP driver **刻意**保持空集(活 steer channel
过不了网络,见 [[remote_driver.py]])。三点契约:

- **缝有了消费者**:orchestrator 以 `"steering" in driver.capabilities()` 为 register 判据——本地/桌面(可载活
  channel)起可 steer 的 run,remote 降级成新起 turn。判据是**活的**,不再是"预留空缝"。
- **声明字符串本身就是契约**:拼错(`"Steering"`/`"steer"`)不会报错,只会让 orchestrator 静默判"这个 driver
  不支持 steering",本地路径也一起退化——所以 `driver.py` docstring 把 vocabulary 从"参考"改成了**强制**约束。
- **契约测试形态变了**:`test_driver_contract.py` 那条 `assert caps == set()`(本节旧版说的"钉住整个表面")已
  换成 `test_all_drivers_declare_only_known_vocabulary`(只准声明 planned vocabulary 内的字符串)+
  `test_steering_capability_is_declared_where_it_can_be_honored`(正反两侧钉死;远程臂在 2026-08-24 已从"必须空"
  改成"按 framework:nexus_power 有、claude/codex 无",见上方新节)。`driver.py` 本轮**只改 docstring、零行为改动**,不触发铁律 #10 的阻塞条件——本节是纠正下方 2026-07-27
  节里已被 #351 变假的"全部返回空集/钉住整个表面"两句陈述(Tier-2 是时间序日志,加新节覆盖,不改写历史节)。

## 2026-07-27 — driver 表面一致化：capabilities() 空协商缝 + 签名整形

三个 driver（claude / codex v1+v2 / remote）统一新增 `capabilities() ->
set[str]`（全部返回空集 = 今天的行为；词汇表见 driver.py 注释，只在能力
真正实现的同一变更里声明）。`streaming` 全员改 keyword-only（所有调用点
本就关键字传参，零行为变化）。codex v2 的 `del kwargs` 改为显式 WARNING
（此前 `disallowed_tools` 被静默丢弃——调用方以为约束生效了）。契约测试
`tests/agent_framework/test_driver_contract.py` 钉住整个表面。

## 2026-07-15 — MCP 管道改名 `mcp_urls`/`mcp_server_urls` → `mcp_servers`

值类型从 url 字符串升级为 spec 对象 `{"url": str, "headers": {str:str}?}`，
支撑用户 MCP 自定义请求头（Authorization 等）贯穿全链路。本文件仅机械跟随
改名/类型，职责不变。

## 2026-06-17 — Executor seam:per-user `executor_url` 优先,`AGENT_EXECUTOR_URL` 兜底

`get_agent_loop_driver` 新增 keyword-only 参数 `executor_url`:非空时返回
`RemoteAgentLoopDriver` 打到**该用户**的 Executor 容器(由 broker 现取,见
`broker_client.py` + step_3)。未传则回退到静态 env `AGENT_EXECUTOR_URL`;
都没有(本地/桌面,或 executor 容器自身)→ 注册表里的本地 driver,行为不变
(铁律 #7)。优先级:`executor_url` 参数 > `AGENT_EXECUTOR_URL` env > 本地。
这是把 step-3 的 claude/codex spawn 收敛进**每用户隔离容器**的接缝
(铁律 #20 控制面/数据面分离 + per-user 工作区挂载隔离)。

## 2026-06-17 — 默认 framework 名 "claude" → "claude_code"

`DEFAULT_AGENT_LOOP_FRAMEWORK` 从 `"claude"` 改名为 `"claude_code"`，文档串里的 平台默认自 2026-08-20 起为 `nexus_power`（免费/默认用户跑自研 loop）。
fallback 说明同步更新。意图是把默认 driver 的名字对齐到实际注册的
claude-code agent-loop driver（与新引入的 `codex_oauth` 等 provider 形成清晰的
命名空间），避免「默认值写的名字根本没人注册」导致 `get_agent_loop_driver`
当场 ValueError。纯重命名，注册/选择优先级机制不变。

# loop/driver.py — 可插拔 Agent 框架的注册表（铁律 #9 的落地点）

## 为什么存在

step_3 过去直接 `ClaudeAgentSDK(...).agent_loop(...)`，把整个平台焊死在一个
agent 框架上——这正是铁律 #9 警告的"一个开关就崩"。本模块把它变成
「名字 → 工厂」的注册表：以后接入第二个框架（完整的 OpenAI Agents loop、
LangGraph、自研 loop）只需 `register_agent_loop_driver("name", Factory)`，
绝不再改 step_3。

## 两条正交的轴，别混

- **provider 轴**（`provider_driver/`）：用谁的 endpoint / key。
- **framework 轴**（本模块）：用哪套 agent-loop 协议。

两者组合：framework driver 仍通过 provider 层解析 model/endpoint。换模型供应商
动 provider_driver；换 agent SDK 动这里。

## 选择优先级（越具体越优先）

1. `get_agent_loop_driver(framework=...)` 显式传入——per-agent 扩展点。
2. 环境变量 `AGENT_LOOP_FRAMEWORK`。
3. `DEFAULT_AGENT_LOOP_FRAMEWORK` = "claude"。

## 坑

- `ClaudeAgentSDK` 在 `agent_framework` 包导入时（`__init__.py`）自注册为
  "claude"，类本身即工厂（`__init__(working_path=...)` 已符合工厂契约）。
  **导入包这件事才会填充注册表**——定义了但没被导入的 driver 找不到。
- 未知 framework 名 → `get_agent_loop_driver` 抛 ValueError，**不会**静默回退到
  claude。配置写错要当场炸，而不是伪装成默认值。
- `AgentLoopDriver` Protocol 的签名精确镜像 `ClaudeAgentSDK.agent_loop`；那个方法
  就是每个新适配器必须对齐的参考形状（yield 原始事件 dict 给 ResponseProcessor）。

## 2026-08-28 补(auto-review I6) — FrameworkNotInstalledError docstring 改实话

原 docstring 谎称 'route 层 catch + 前端按 framework 本地化'——全 PR 无任何 route catch、无本地化分支。改成实话:配置时(选择器 disabled + POST 409)是主门,本异常是运行时 backstop(升级用户既有绑定/卸载后),经正常 run-error 面带英文可读消息冒泡;暂无专门 route catch 与按框架本地化(todo)。

Batch 6b.2b: `ensure_builtin_frameworks()` registers the three builtin framework contributions via the kernel manifest before any lookup; the platform holds no framework import.

2026-09-07: `bound_default_framework()` — a `turn.pipeline.act.framework` binding names the default framework for agents without an explicit one (env `AGENT_LOOP_FRAMEWORK` still wins).

## 2026-09-07 — bound_default_framework is loud on a misbinding

The hand-rolled owner/name match with a bare 'return DEFAULT' fallback ran the turn on nexus_power when the bound framework was missing while the settings page said otherwise — the substitution FrameworkNotInstalledError exists to refuse. Now: an UNBOUND slot (layer DEFAULT) still means the code default; a bound-but-unregistered provider raises FrameworkNotInstalledError through kernel.plugins.bound.bound_entry (the one owner/name/owner:name matcher). Docstring precedence list updated; available_agent_loop_frameworks' docstring restored above the lazy call.

## 2026-09-07 — framework_registry() resolved at call time; no lazy population

FRAMEWORK_REGISTRY (a module constant bound to the process registries at import) became framework_registry(registries=None): private registries in tests are visible, and the registry is populated by the host boot from the framework plugins' manifests — ensure_builtin_frameworks and its 'registry non-empty' done-flag are gone.

## 2026-09-07 — 注册表派生的框架事实 helper（B6）

framework_metas/framework_meta/default_framework_for_protocol/framework_for_oauth_source/framework_installed 集中于此：整个宿主只有这一处读 FrameworkMeta。framework_meta 对未知名抛 UnknownEntry（绝不悄悄回落默认）；default_framework_for_protocol 偏好『锁定该协议的第一个注册框架→第一个 any 框架→bound 默认』，注册顺序即 builtin manifest 顺序，所以 anthropic→claude_code、openai→codex_cli 的历史配对在这些插件启用时保持，不启用时自然退化为发行版实际装载的框架。framework_installed 从 plugin_paths 搬来：宿主内置（install=None）即可用，按需框架用 meta.install.probe_package 走 plugin_paths.package_installed 探测；get_agent_loop_driver 的 fail-closed 门不再依赖 PLUGIN_FRAMEWORKS 名单。

## 2026-09-07（round-2 P2-I1 / P2-I3）— one accessor for the default, one accessor for capabilities

`resolve_framework_name()` is now the ONLY answer to "which framework, given no explicit choice".
`model_identity.DEFAULT_AGENT_FRAMEWORK` (a second constant with the same value) and the bare
`"nexus_power"` literal in `slot_service.set_agent_slot` are gone: with a distribution binding
`turn.pipeline.act.framework` to another plugin, those three disagreed, and the user's symptom was a
card the UI accepted and a turn that then failed with an unrelated `LLMConfigNotConfigured`.

`framework_capabilities(name)` / `framework_has_capability(name, cap)` are the same idea for the
capability axis: every host that must answer "can this framework be steered / replayed natively"
BEFORE a driver exists reads `FrameworkMeta.capabilities` through them. Fail-closed on an unknown or
misbound name (empty set) — the orchestrator gates features ON the answer, so a wrong "yes" leaves a
user's interjection queued with nothing draining it. These replaced three name-keyed frozensets
(`_STEER_CAPABLE_FRAMEWORKS`, `NATIVE_REPLAY_FRAMEWORKS`, `CLOUD_ALLOWED_FRAMEWORKS`) and one
`framework != "nexus_power"`, each of which silently downgraded every third-party framework.

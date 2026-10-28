---
code_file: plugins/builtin.frameworks.nexus_power/src/narranexus_plugins/frameworks_nexus_power/core/contracts/options.py
last_verified: 2026-09-03
stub: false
---

## 2026-09-03（批 2a.5）— `deferred_tools`

插件工具中不常驻的名字（dispatcher 视角的名字）；默认空。

## 2026-08-24 — `steerable: bool`(可控性跨边界的显式载体)

新增 `steerable`(默认 False)。这一轮 run 是否可控(orchestrator 是否给它注册了 `SteerChannel`)必须**显式**过序列化边界:subprocess `runner.main()` 每轮无条件挂 `QueueSteeringInlet`,所以"有没有挂 inlet"在生产上恒真、推不出可控性。`wait_for_input` 工具**仅当** `steerable=True` 才暴露(见 [[assembly.py]] `_steer_channels`)——否则 agent 一调就在无人喂的队列上阻塞满 clamp。写入方=`nexus_agent._build_request_payload`(`steer_channel is not None`)。默认 False:旧 payload 无此字段→安全降级为"不暴露 wait"(不会误挂 300s 阻塞)。DRAIN 与它正交。

## 2026-08-17 — `origin_declaration`

一行已渲染好的来源声明（见 [[message_source_handler]] 的 `render_origin_declaration`）。
框架拿到的是成品句子而不是原料，这样 CLI driver 和本框架发出的字符完全一致。空 = 本轮
没有已声明的回复面，那就对回复只字不提。


## 2026-08-13 — expression_nudge 开关

TurnOptions 新增 `expression_nudge: bool = False`：opt-in 哑轮补救（见 loop.py.md
同日条目）。TurnProfile.voice_fast() 置 True，经 nexus_agent 适配器映射进 options，再由 assembly.py 的 build 段接进 LoopAssembly（那才是唯一接线点）。

## 2026-08-10 (review 修正) — 字段改名 `extra_readable_roots` → `extra_accessible_roots`

纯改名，语义不变：这份授予同时管写与删（confinement 层检查 `file_path` 与 shell 路径），
旧名名不副实。详见 [[policy.py]]。

## 2026-08-07 — TurnOptions.extra_readable_roots

框架外部调用面新增：本回合额外可读的绝对根，由调用方（知道 user/team 的那一层）决定。
保持框架通用性（铁律 #9）——这里不出现 team/user 概念，只有「这些根也允许」。
缺省空 = 纯 workspace 收敛。


## 2026-08-06 — voice fast mode: TurnProfile 管道（缺省=现状）

新增 `prompt_mode: Literal["full","minimal","none"]="full"`（字符串字面量，wire 层禁 import PromptMode 枚举；assembler 侧转换）。

## 2026-07-31 — 回复契约:投递面由平台声明(expressive seam)

`ExpandableSpec` 新增 `expressive_tools`(wire 形状,随包声明投递工具);
assembly 翻译进 `Expandable`。`TurnOptions.expressive_tools` 语义强化:
**首位=默认回复工具**(constitution 例子),平台按优先级序传入。

# contracts/options — TurnOptions 对外参数面(pydantic)

深度对齐 claude-agent-sdk / codex exec(cwd/output_mode/output_schema/permission_mode/subagents/expandables;映射表在 docstring)。故意没有 max_turns(铁律 #14,永不提供)。重大坑:pydantic v2 的 model_* 是保留命名空间——曾用 model_extra 撞上 BaseModel 内建属性(恒 None),已改名 llm_extra;今后新增字段禁用 model_ 前缀。

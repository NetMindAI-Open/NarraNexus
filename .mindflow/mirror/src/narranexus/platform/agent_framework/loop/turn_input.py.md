---
code_file: src/narranexus/platform/agent_framework/loop/turn_input.py
last_verified: 2026-09-04
stub: false
---

## 2026-09-03（批 2a.5）— `deferred_tools`

新字段，语义「可搜可调、不进 up-front 清单」；`driver_kwargs()` 只在非空时放键（保持 legacy 调用形状
逐字节相同）。没有 tool_search 接缝的框架忽略它。

## 2026-08-17 — `origin_declaration`：已渲染好的一行，两个框架逐字照发

新增字段 `origin_declaration`，装的是 **已经渲染完成** 的来源声明字符串，在 step 层
（唯一同时握有 working_source 和刚声明的工具的地方）合成。driver 只负责摆位置，不负责
措辞——否则就要把 working_source 传给每个 driver，再指望各自措辞一致，那正是这行字要
消灭的漂移。

空值**不发 kwarg**（和 `expressive_tools` / `extra_accessible_roots` 同一条规矩）：没听说过
这个字段的 driver 保持自己的默认，听说过的也不会把 `""` 误认成一份真声明。


## 2026-08-10 (review 修正) — 字段改名 `extra_readable_roots` → `extra_accessible_roots`

纯改名，语义不变：这份授予同时管写与删（confinement 层检查 `file_path` 与 shell 路径），
旧名名不副实。详见 [[policy.py]]。

## 2026-08-07 — extra_readable_roots（空则不发键）

bundle 新增 `extra_readable_roots`：本回合 workspace 之外还可读的绝对根（如团队共享目录，
它是每个 agent workspace 的 sibling）。沿用 `expressive_tools` 的约定——**空则不发键**，
保证不授予任何东西的回合产出逐字不变的 legacy kwargs。只有真正执行 workspace 收敛的
driver（NexusPower）会消费它；CLI driver 无此层，经 **kwargs 忽略。


## 2026-08-06 — voice fast mode: TurnProfile 管道（缺省=现状）

bundle 新增 `turn_profile` 字段（None 时 driver_kwargs 不发键——legacy 形状不变）；这是 fast-mode 旋钮束进 driver 的载体。

## 2026-07-31 — 回复契约:投递面由平台声明(expressive seam)

新增 `agent_id`(恒发;NexusPower 落 ToolContext,CLI driver 经 **kwargs 忽略)
与 `expressive_tools`(空则**不发键**——mute 保持 driver 默认;非空发 list)。
来源:[[context_runtime.py]] 模块声明收集 → step_3 组包。

## 2026-07-29 (二次) — 删除 resume_session_id 字段(T6)

字段与 `driver_kwargs()` 里的条件发射一起删。上游已无生产者:[[step_3_agent_loop]]
的句柄决策(T5)和 [[executor_protocol]] 的协议字段(T6)都已移除,claude adapter
自己生成并使用 session id,不经过这个 bundle。

`test_turn_input.py` 保留一条**断言它不存在**的用例,而不是简单删掉:这个 key 一旦
被重新引入,CodexSDKv2 的 ignored-kwargs WARNING 会重新开始每轮刷屏。

## 2026-07-29 — resume_session_id 的来源换了(注释同步)

字段本身保留,但**上游不再设置它**:[[step_3_agent_loop]] 的句柄决策已删除(T5)。
现在唯一的填充者是 claude adapter 自己——它每轮写一份 transcript 再 resume
([[transcript]])。字段注释里指向 `_resolve_resume_session_id` 的那句已失效,改为
指向 transcript。

字段之所以还留着:remote 路径仍把它序列化进 `/agent-loop` body(executor 侧
`authorize_resume_session_id` 读它)。那条协议缝隙连同 resume HMAC 一起在 T6 处理。

# loop/turn_input.py — 物化层 turn 输入的显式打包

## 2026-07-28 — 新增 resume_session_id 字段（resume 化 R2）

resume 走 TurnInput 字段而不是调用点裸 kwarg（两案二选一，选前者）：
bundle 本来就是「本轮 driver 输入」的家，且 driver_kwargs() 是唯一出口，
threading 可测。**只在非 None 时发键**：冷启动轮 kwargs 与旧形状字节级
一致；codex 系 driver 永远收不到这个键（step_3 只为 claude_code 解析句
柄），所以 CodexSDKv2 的 ignored-kwargs WARNING 不会被一个恒 None 的字
段每轮刷屏——这正是选 TurnInput + 条件发键而非无条件 kwarg 的原因。
frozen dataclass ⇒ step_3 在 resume 决策之后才构造 TurnInput。

## 为什么存在

step_3 历史上把一个 turn 的输入以四个散落 local（messages / mcp_servers /
skill_env_vars / extra_disallowed_tools）逐个塞给 driver——「物化层」只
存在于调用点的局部变量里。TurnInput 把它变成一个显式对象：所有 driver
可证明地吃同一份东西，且 bundle 有了生长的位置。

## 设计约束

- `driver_kwargs()` **逐字节复刻**历史调用形状：messages/mcp_servers 按
  引用传（step_3 在调用前还会 merge mcp_servers）、空 extra_env /
  disallowed_tools 归一为 None 让 driver 默认值生效。
- `refs` 是预留的引用层字段（TurnContext 双层输入的另一半：自研 loop
  凭可序列化 ID 自己投影上下文）——在有 driver 真正消费之前**恒为
  None**。声明没人实现的字段正是 schema 诚实原则要防的坑。
- `cancellation` 刻意不进 TurnInput：它是 runtime 拥有的每-run 控制流，
  不是 turn 内容。

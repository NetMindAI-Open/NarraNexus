---
code_file: src/xyz_agent_context/agent_framework/adapters/claude/sdk.py
last_verified: 2026-08-04
stub: false
---

## 2026-08-04 — 消费 expressive_tools：回复面 reminder 上 user message 末尾

此前 CLI 驱动完全忽略 TurnInput.expressive_tools，回复指令只存在于遥远的
system prompt——正是 NexusPower 尾部机制要修的 far-from-generation 失效。
CLI 无 per-step 缝，最近可控位置 = 本轮 user message 末尾：split_for_argv
后经 [[prompts]] 的 append_reply_reminder 追加。只上活跃 prompt 输入，
不进 transcript/history，不跨轮累积；user message 本就逐轮变化，
不伤缓存前缀。

## 2026-07-29 (五次) — code review 修复:重试判据排除凭据失败

`except` 分支的重试条件加上 `is_credential_error(e)` 排除。

上一版把条件从"零输出 **且** stderr 含特定短语"放宽成"零输出",目的是覆盖我方
transcript 的任何 bug(短语匹配当天差点漏掉 slug bug)。但凭据失败**也**在产出任何
内容前死掉,所以类型无关的规则会重试它——而那次重试注定同样失败,代价是第二次 CLI
spawn 和用户看到真实错误前的双倍等待。

**重试存在的目的是覆盖我们自己的 transcript bug;凭据失效不是那类问题,重试多少次
也不会变成那类问题。**

流内那条分支不需要改:`_is_zero_output_error_event` 只匹配 `error_type == "no_output"`,
鉴权错误不会命中它。

transcript 的决策/落盘/清理与 git 查询已移出本文件,见 [[transcript]] 同日条目。

## 2026-07-29 (四次) — session id 只有一个来源了(T6)

`resume_session_id` 不再从 `kwargs` 读。上游的两个生产者都已删除:
[[step_3_agent_loop]] 的句柄决策(T5)与 [[executor_protocol]] 的协议字段(T6)。
现在它初始化为 `None`,**唯一的赋值点是我们自己写的 transcript**。

直接后果:句柄不可能过期、不可能被并发 run 争用、不可能锚定在中途变更的叙事上——
这三样正是被删掉的那套机制存在的全部理由。

## 2026-07-29 (三次) — 重试判据不再匹配字符串(T5)

删除 `_RESUME_STALE_STDERR_PHRASE` / `_stderr_reports_stale_resume` /
`_failure_indicates_stale_resume` / `_resume_failed_marker_event`。同轮冷重试**保留**,
但判据简化成"产出任何内容之前 CLI 拒绝 resume"。

原来要求 stderr 出现 `No conversation found`,因为句柄来自上一轮、只有**过期**的才
值得重试。现在句柄是我们几秒前刚写的 transcript,任何拒绝都是我方 bug,冷启动在
任何情况下都是对的答案 —— 匹配特定短语只会变成"漏掉我们自己的一部分 bug"的机制。

这不是假设:cwd slug 的 bug 当天上线,**能活下来纯粹因为 CLI 恰好说了那句话**。

界限没变:最多一次、且仅在尚未产出内容前;产出之后失败照旧抛出(重跑会重复内容)。

## 2026-07-29 — 每轮自建 resume transcript(T2)

历史不再依赖"CLI 是否还记得某个会话":有历史时,adapter 自己写一份 transcript
([[transcript]])、用一个**每轮全新的 uuid4** resume 它、turn 结束时删掉。
`assemble_argv_prompt(base, [])` 于是走的是既有的 resume 分支——**每一轮都变成
resume 轮**。

**为什么这解决的是句柄式 resume 剩下的那笔成本。** 冷轮把历史放在 system prompt
里(实测 63,603–66,023 字符)、resume 轮不放(63,244),两个提示词因此不同,所以
任何冷轮之后的第一个 resume 轮必然从 `system` 开始 miss(约 49K 全价)。而冷启动
的触发原因全在缓存控制之外:还没句柄、叙事变了、句柄过期。自己写则 system prompt
从第一轮起就逐字节相同。

**为什么每轮换 id 而不是派生一个稳定的。** 文件在 `finally` 里被删,所以共用
`CLAUDE_CONFIG_DIR` 里不留任何东西给"无鉴权 `/agent-loop` + 猜句柄"去读;可猜的
派生 id 会把那个洞重新打开。T0 实测信封字段(含 `sessionId`)不进请求,所以换 id
不花任何缓存代价。它同时让并发天然无冲突——这正是现有那个进程级 lease 存在的唯一
理由。

**`try` 开在第一个 run 之前,不是围着每个 run。** 写完文件到启动 CLI 之间若失败,
文件就会被遗留;而每轮用全新 id,**没有任何后续流程会回来清理它**。删除是**同步
的**,所以 `aclose` 时 `GeneratorExit` 落在内部 yield 上、`finally` 立刻执行,而不
是等 GC——与 lease 释放同一条推理。覆盖四条出口:正常完成(含两个 `return`)、
except、取消、aclose。

**我方 transcript 覆盖上游给的句柄。** 我们的是刚写的、完整的;上游那个可能已过期。

**`_working_git_branch` 带 `lru_cache`。** 每轮 spawn 一次 `git` 是热路径上的真实
成本,而这个字段只供 CLI 自己显示用。工作区通常根本不是 git 仓库,那时返回空串。

## 2026-07-29 — 显式指定 CLI 二进制(`cli_path`)

`options_kwargs` 新增条件项 `cli_path`,值来自 [[cli_binary]] 的
`resolve_cli_path()`;`None` 表示没有经校验的候选,保持 SDK 自带的二进制。

不加这一项的后果不是报错而是**静默降级**:SDK 的 `_find_cli()` 先查它 wheel
里自带的副本(SDK 0.1.43 = CLI 2.1.56),PATH 上再新的版本也永远轮不到。而
2.1.56 会**每一轮重排请求的 `tools` 数组**,把它后面的整个缓存前缀(含我方
6 万余字符 system prompt)全部作废——实验 E3/E3c 实测,`--resume` 路径同样如此。

放在 `options_kwargs` 里而不是两个构造点各写一遍:重试路径(`_run_once` 的
stale-handle 冷重试)复用同一个 dict,所以一处即覆盖两处。

## 2026-07-28 — 优雅关停的两个活性缺口（MEDIUM review findings）

`_graceful_cli_shutdown` 原来只 bound 了 `process.wait()`。两处补齐：

1. **`end_input()` 之前是无界的。** 它只裹在 `with suppress(Exception)` 里，而
   `suppress` 对**挂死**毫无作用——vendored SDK 的 `end_input()` 要拿
   `transport._write_lock`，若有并发写卡住持锁，这个 await 永不返回，整个 turn
   的 generator 就吊死在本该有上限的关停步骤上（比 `_GRACEFUL_CLI_EXIT_SECONDS`
   更糟：它压根到不了那一步）。现在单独用 `_GRACEFUL_END_INPUT_SECONDS = 2.0`
   兜住（健康情况下关 stdin 是微秒级 syscall，2s 已是纯余量），超时就落到既有
   SIGTERM/SIGKILL 拆卸路径。仍是 best-effort，永不抛。

2. **进入优雅等待后不再理会取消。** 取消闸门在调用点只查**一次**；Stop 若在那
   之后一毫秒按下，用户就得白等最多 10s。现在等待内部把 `process.wait()` 与
   `cancellation.await_cancelled()` **赛跑**（照抄 receive loop 已有的 race 形
   状），取消胜出即短路到快速拆卸。函数因此多了 `cancellation` 形参。

**必须守住的不变量**：正常完成（无取消）时**仍然**要等 CLI 自己干净退出——那次
等待就是 transcript flush，丢掉它就是 2026-07-25 那次"下一轮 --resume 找不到会话"
的回归。测试里同时钉住三件事：end_input 挂死→有界且 turn 照常完成、优雅等待中
取消→快速拆卸（断言没有 10s 等待、`returncode is None`）、正常完成→调用序仍是
`connect/query/end_input/process_wait/disconnect`。
（tests/agent_framework/test_claude_sdk_resume.py）

## 2026-07-28 — R4c：sys_sha256 权威发射点 + MCP config 排序 + 冷/热结构审计

（本条为 R4 系列在新 dev 结构上的重放；原始实现 2026-07-25 于 feat/cli-session-capture 分支，该历史不在本分支 mirror 中，条目自含。重放适配：老分支的发射点挂在 `_assemble_system_prompt` 调用点，dev 新结构该函数已被 `adapters/materializer.py` 的 `assemble_argv_prompt` 取代，发射点随之挂到两处 `assemble_argv_prompt` 调用点之后。）

E2 实验（`…/specs/2026-07-25-e2-request-capture-findings.md`）后的三处校准：

1. **`_log_sysprompt_sha(system_prompt, resume_session_id)`**（新模块级
   helper）：在两处 `assemble_argv_prompt` 调用点之后（主路径 + 陈旧句柄
   冷重试路径）发射 `[SYSPROMPT-SHA] chars=… resume=… sys_sha256=<12hex>`。
   哈希对象 = 交给 SDK `options.system_prompt` 的**完整字符串** = 请求里的
   system[2]。此前 context_runtime 的哈希漏掉两类适配器新增字节（冷启动轮
   Chat History 尾段、逐 system message 的 "\n" 拼接），那边已改标
   `ctx_sha256`。哨兵读法：连续 resume 轮同值；**紧跟"带历史的冷启动轮"的
   第一个 resume 轮必然与冷轮不同值**（见下条，预期内）。
2. **冷/热结构审计结论（无代码改动）**：适配器层 cold vs resume 的 system
   prompt 唯一结构差 = `=== Chat History ===` 尾段（仅带历史的冷轮出现）；
   options/tools/env 全同，`options.resume` 不是 prompt 字节。后果：冷轮后的
   首个 resume 轮支付一次全额 cache 写——有界、by design；把历史折回 resume
   轮的 system[2] 会使 resume 失去意义，故保持现状。注释写在 Step 0-2 决策块。
3. **`_build_claude_mcp_config` 按 server 名 sorted**（E2 §4 工具洗牌断点）：
   本字典序列化进 CLI 的 MCP config，是我们能控制的最后一环（也兜住上游
   `pass_mcp_servers` merge 序）；codex 两条路径本就 sorted。**残余不可控**：
   CLI 并发连接各 MCP server、按完成序合并 tools 数组——那是 CLI 内部行为。
   Tests：`test_sysprompt_sha.py`（哈希覆盖尾段/格式/稳定性）、
   `test_mcp_headers_plumbing.py`（排序不随插入序）。


## 2026-07-28 — resume 注入 + 跳过历史 + 陈旧句柄同轮冷启动重试 + transcript 冲刷（resume 化 R2/R3，dev 新结构重实现）

旧分支 feat/cli-session-capture 的 be9c8ecd + c40f1ad3 在 dev 新结构上的
重做。E1 已证伪"SDK 不支持多轮"（spec:
`reference/self_notebook/specs/2026-07-23-e1-resume-feasibility.md`；SDK
0.1.43 `ClaudeAgentOptions.resume` → CLI `--resume`，跨进程可用、轮间缓
存真实命中；DeepSeek × NetMind bearer 亦 PASS——**没有**
`_is_claude_native` 门禁，resume 对 claude_code 框架下所有模型生效）。

**与旧实现的结构差异**（行为等价，接口换了）：
- 提示组装不再是本文件私有 `_assemble_system_prompt`，而是
  [[materializer.py]] 的两阶段 `split_for_argv`（pop 恰好一次）+
  `assemble_argv_prompt`（预算/驱逐/双上限）；resume 轮
  `assemble_argv_prompt(base, [])`，冷启动轮传全量 entries；
  `base_system_prompt` + `history_entries` 留在局部供 R3 重试重组。
- 取消判断统一走 `CancellationView(cancellation).requested()`（graceful
  跳过判断也是），事件常量走 [[events.py]]（marker 用
  `DATA_TYPE_RESUME_FAILED`）。

**R2（注入）**：`resume_session_id = kwargs.get("resume_session_id")`
（TurnInput.driver_kwargs() 只在非 None 时发键，见 [[turn_input.py]]）；
upstream（step_3）已做四重校验，适配器不再验。`options_kwargs` 加
`resume=`（None = SDK 缺省 = 冷启动）。Provider config INFO 行追加
`resume=<sid 前 12 位|cold>`。**system prompt 每轮照传**（模块指令可合法
变化，变了只损失当轮缓存，E1 T4 证安全）。

**R3（唯一失败兜底）**：Step 2 主体（dev 现行逻辑逐字保留：race-with-
cancel 接收循环、inline assistant error 三通道、零输出事件、有界
disconnect+SIGKILL）提为内嵌 `_run_once(run_options)`。外层驱动：冷启动
= 恰好一跑；resume = 一跑 + **当且仅当**「零内容失败 + 证据含
`"No conversation found"`（`_RESUME_STALE_STDERR_PHRASE`，E1 T3 实测）」
→ 同轮冷启动重试恰好一次：yield `response.resume_failed` marker（内部信
号，绝不转 ErrorMessage，铁律 #16）→ stderr 列表原地 clear 复位 →
重组带历史 prompt、`resume=None` 再跑。三处 `async for` 都包
`aclosing(...)`（提前 close 时 in-flight `_run_once` 的 finally 同步执
行）。判据 `_failure_indicates_stale_resume` 查三通道（str(exc) /
exc.stderr / 捕获 stderr）；`_drain_stderr_after_failure`（~200ms，仅错
误路径）补 stderr pump 输给快崩的竞态。

**transcript 冲刷（c40f1ad3 并入）**：`_graceful_cli_shutdown(client)`——
自然完成（非取消）时 finally 之前执行：`end_input()` 关 stdin → CLI 自
行冲刷 transcript 并 exit 0 → 有界等待（10s，收尾等待非 loop 上限，铁律
#14 安全）→ `close()` 见 returncode 已置整个跳过 SIGTERM。根因：SDK
`transport.close()` 关 stdin 后立即 SIGTERM，CLI 的 transcript 惰性冲
刷输给竞态（2026-07-25 实测：冷启动轮 JSONL 零条会话记录 → 下轮
`--resume` 报 "No conversation found"）。取消/异常路径保留今天的同步快
速清收。

测试：tests/agent_framework/test_claude_sdk_resume.py（stub transport；
resume 置位/跳历史、冷启动不变、陈旧句柄重试 + marker、短语不符不重试、
有内容后崩不重试、判据三通道、graceful 时序
connect→query→end_input→process_wait→disconnect、取消跳过 graceful）。

## 2026-07-28 — inline 错误再补一路：assistant text

`_inline_assistant_error_event` 原本只在 **CLI stderr 非空**时才接管；
stderr 为空就放行给 output_transfer，用户只看到 `Claude API error: unknown`。
但 CLI 在够不着模型时是**带内**回答的：一条普通 AssistantMessage，正文写着
`API Error: 400 {"detail":"balance not enough..."}`，stderr 一个字都没有。
于是最具体的失败描述只进了日志，用户面前是黑盒——正是这个函数存在的目的。

改法：新增 `_assistant_error_text(message)` 抽 TextBlock 正文，detail 通道
按 **stderr → assistant text** 顺序取（stderr 更富，带 litellm 的 token 数
字），两者都空才退回裸 enum 句子。串起来后
`classify_self_serviceable` 能把它认成 `insufficient_balance`，用户拿到
"充值或换这个 slot 的 provider"，而不是"出错了"。

顺带修掉一个更难看的表现：走这条分支会 `continue`，output_transfer 不再把
那段正文当作 **agent_response** 发出去——计费失败本来会显示成 agent 自己在
说 "API Error: 400 ..."。

（2026-07-28 现场：新用户 agent 被钉在零余额 NetMind 账户上，见
[[auth.py]] 同日条目。）

## 2026-07-27（补）— review 修复：@timed 归位 agent_loop

PR #167 review 抓到：插入 capabilities() 时错位到了 @timed 装饰器与
agent_loop 之间，`llm.claude.agent_loop`/`llm.codex.agent_loop` 延迟埋
点静默丢失且指标被误挂到 capabilities()。已把 capabilities() 移到装饰
器上方；契约测试新增 `test_agent_loop_keeps_timed_instrumentation`
（断言 agent_loop 有 __wrapped__、capabilities 没有）防回归。


## 2026-07-27 — 内联压平块上提到 adapters/materializer.py（flatten_for_argv）

agent_loop 里 ~110 行的「system 拼接 + 历史压平 + source-aware 驱逐 +
字符/字节双截断」内联块逐字搬到 [[materializer.py]]，调用点一行 + 指路
注释。argv 上限 rationale 注释随函数走。行为由金样测试钉住字节级等价。
pop-调用方列表的变异语义原样保留（step_3 fallback 依赖）。


## 2026-07-27 — 取消检查统一走 CancellationView（codex v2 死代码修复）

轮询式取消检查改为 `CancellationView(cancellation).requested()`。对
claude/cli_sdk/remote 是等价替换；对 codex v2 是 bug 修复——原
`getattr(cancellation, "is_set", lambda: False)()` 对真实
CancellationToken 恒 False（token 只有 is_cancelled property），进程内
codex turn 此前根本无法被打断。测试
`tests/agent_framework/test_cancellation_view.py` 含该回归用例。


## 2026-07-27 — driver 表面一致化：capabilities() 空协商缝 + 签名整形

三个 driver（claude / codex v1+v2 / remote）统一新增 `capabilities() ->
set[str]`（全部返回空集 = 今天的行为；词汇表见 driver.py 注释，只在能力
真正实现的同一变更里声明）。`streaming` 全员改 keyword-only（所有调用点
本就关键字传参，零行为变化）。codex v2 的 `del kwargs` 改为显式 WARNING
（此前 `disallowed_tools` 被静默丢弃——调用方以为约束生效了）。契约测试
`tests/agent_framework/test_driver_contract.py` 钉住整个表面。

## 2026-07-26 — `_is_claude_native` 并入 `oauth_token`

setup-token 运输层（oauth_token）恒为官方 Anthropic 后端，native 判定显式
包含它——不再只靠 alias 模型名兜底（用户手填非 alias 模型串时语义仍正确）。
staging 门 `auth_type == "oauth"` **保持不变是有意的**：token 模式无凭据
文件可 stage，走 to_cli_env 的 env 注入（见 [[api_config]]）。

## 2026-07-24 — kwargs `disallowed_tools` 合并进本地列表（B++）

`agent_loop` 读 kwargs 里的 `disallowed_tools`（未绑定 channel 的工具，来自
[[step_3_agent_loop.py]]），**merge 不 replace** 进本地列表——WebSearch 守卫
必须存活。已验证（2026-07-24）：CLI disallowedTools 会把工具 schema 从模型
上下文移除（11 个 builtin → prefix −4.1K），所以这不只是禁调用，是真省 token。

## 2026-07-23 — 免费额度会话票凭证由后端注入（本文件无网关逻辑）

免费额度改成"主钥匙下沉 LiteLLM 网关、每次运行签会话票"后，**签票/注入/作废发生在
后端 orchestrator（[[step_3_agent_loop]]）**，不在本文件。原因：cloud 下本文件的
`agent_loop` 跑在**用户 executor 容器**里（用户可控代码），那里既拿不到 `provider_source`
（executor 只收 `provider_configs`，不收 source），也**绝不能持有网关 admin key**。
后端把会话票写进 `ClaudeConfig` ContextVar，随 `provider_configs` 送到 executor，本文件的
`to_cli_env()` 照常把 `api_key`→`ANTHROPIC_AUTH_TOKEN` 注入子进程——**机制未改，只是拿到
的值是会话票**。所以本文件对网关是无感的。凭据链路详见 [[gateway_key_service]] /
[[system_provider_service]]。

## 2026-07-21 — 子进程 provider 可观测日志(Lark bug #1)

`agent_loop` 组装完 `cli_env`(所有覆盖之后、build options 之前)加一条 INFO,打印
**生效**的 `base_url`/auth 种类/`CLAUDE_CONFIG_DIR`。与之前那条"配置意图"日志区分:
这条是**实际注入子进程的 env**。用途:个人 `~/.claude/settings.json` 的 env-block
可把 `ANTHROPIC_BASE_URL` 悄悄改道到私有 relay,以前无迹可查(2026-07-08 事故靠 30+
次黑盒探测才定位);现在配置 vs 实际一 grep 即比对。纯可观测,无行为变更;只打
base_url 与 auth 种类(token/key/none),不打凭据本身。CLI helper 侧有对称日志
(见 [[cli_helper]])。

## 2026-07-15 — MCP spec 带自定义 headers（`_build_claude_mcp_config`）

`agent_loop` 第二参数改为 `mcp_servers: {name: {"url", "headers"?}}`。新增模块级
`_build_claude_mcp_config()`：spec → `McpSSEServerConfig`，有 headers 才带
`headers` 键（SDK 0.1.43 起原生支持，连接时随请求发送）。模块内部 MCP 不带
headers，行为不变。headers 值是密钥，函数内不打日志。

## 2026-07-14 — inline `AssistantMessage.error` 把 CLI stderr 折进错误事件（病A / "黑盒" P1）

`AssistantMessage.error` 是**只有 6 个值的枚举**（auth/billing/rate_limit/
invalid_request/server_error/unknown）。真正的 provider 原因——例如 litellm
`ContextWindowExceededError: inputs 75307 > 32769`——被 CLI 压成这个枚举，
数字只活在 **CLI stderr** 里。原本 inline error 分支只 `logger.error` 打日志，
让 `output_transfer` 输出干巴巴的 `Claude API error: unknown`，真相丢失。

现在:inline error 分支里，当 `cli_stderr_lines` 非空，改为 yield
`_inline_assistant_error_event(message.error, cli_stderr_lines)` 并 `continue`
（跳过 output_transfer 的枚举事件）。该 helper 保留 `error_type`=枚举原值、把
stderr 尾部折进 `error_message`——复用 `_zero_output_error_event` 的
`_stderr_tail_detail` 共享写法。这样下游
`llm.failure.classify_self_serviceable` 能从 message 文本认出 context-window /
余额 / 模型错误。stderr 为空时不加东西，让 output_transfer 的枚举事件照常走
（有些 inline error 的 stderr 本就是空的）。

## 2026-07-12 — macOS 上**陈旧 host 文件遮蔽 Keychain**:凭据来源改为 Keychain 优先

**症状**:本地版重新 `claude login` 后,Nexus 仍报 "coding-agent login has expired";
Backend log 里 CLI 实为 `AssistantMessage(text='Not logged in · Please run /login')` +
`error='authentication_failed'`。本地 `claude` CLI 正常,唯独 Nexus agent 槽失败。

**真正根因**(比"stage-once"更底层):这台机器**同时存在两份凭据**——
- 陈旧 host 文件 `~/.claude/.credentials.json`(6-25、`expiresAt` 已过期、只有 3 个 key 的旧格式);
- 新鲜 Keychain(`Claude Code-credentials`、`expiresAt` 未过期、6 个 key 的现代格式,含
  `scopes/subscriptionType/rateLimitTier`)。

现代 macOS Claude Code 只写 **Keychain**;那个 `~/.claude/.credentials.json` 是老版本 CLI 的
**遗留物**。但 `_stage_claude_oauth_credentials` 原逻辑是"host 文件存在就用它,否则才回退
Keychain":`if source.is_file(): <copy2 file>`。于是 `source.is_file()==True`(陈旧遗留物)
**永远遮蔽** Keychain,把 6-25 的过期 token `copy2`(保留 mtime,故隔离副本 mtime 也是 6-25)
进隔离目录 → 隔离 CLI 读到过期文件 → "Not logged in"。用户自己的 `claude` 读 Keychain 所以正常。

**修复**:macOS 上 **Keychain 是唯一权威**,host 文件仅当 Keychain 无 entry 时才作后备。
- 新增 `_oauth_expires_at(blob)`:解析 `claudeAiOauth.expiresAt`(epoch-ms;**绝不 log blob**)。
- 新增 `_read_keychain_blob()`:`security find-generic-password -s "Claude Code-credentials" -w`
  的可 mock 封装,无 entry / 读失败 → None。
- 新增 `_stage_blob_newest_wins(dir, blob, sourced_from=…)`:按 `expiresAt` 的 newest-wins 原子
  写(0600)。仅当源严格更新才重导;隔离副本 expiresAt >= 源 → 保留(护住 CLI 就地刷新,不重新
  注入已消费 refresh token,仍规避 #76 登出);源无 expiresAt → 绝不覆盖好副本。
- `_stage_claude_oauth_credentials`:`if sys.platform=="darwin": kc=_read_keychain_blob();
  if kc: _stage_blob_newest_wins(...); return`——Keychain 有就用,永不被陈旧 host 文件遮蔽;
  否则落到原 host-file 路径(copy2 + mtime newest-wins),**Linux/云端逐字不变**(无 Keychain)。

**代价**:macOS 每次 spawn 多跑一次 `security`(约 10ms)。为正确性接受。
守卫测试(`tests/agent_framework/test_claude_config_isolation.py`):
`test_darwin_keychain_wins_over_stale_host_file`(本次回归 · 核心)、
`test_darwin_falls_back_to_host_file_when_keychain_empty`(老版 CLI 后备)、
`test_stage_blob_newest_wins_restages_when_newer` /
`test_stage_blob_preserves_inplace_refresh`(newest-wins 两个方向)、
host-file 两测已 mock `_read_keychain_blob` → None 以在 dev Mac 上确定性走文件路径。

## 2026-07-09 — macOS: OAuth 凭据从 Keychain 导出进隔离目录(#76 的 macOS 补丁)

#76 把 claude OAuth 隔离进独立 `CLAUDE_CONFIG_DIR`(`claude_oauth_config_path`)
并把 `~/.claude/.credentials.json` **拷**进去。但 macOS 上 claude 把 OAuth token 存
**Keychain、没有那个文件** → 文件拷贝 no-op、隔离目录空;而显式设了 `CLAUDE_CONFIG_DIR`
又让 CLI 走文件模式忽略 Keychain → "Not logged in"(真机实测)。

新增 `_stage_claude_oauth_from_keychain(config_dir)`:`_stage_claude_oauth_credentials`
在**源文件缺失且 `sys.platform=="darwin"`** 时调它——用 `security find-generic-password
-s "Claude Code-credentials" -w` 读出 Keychain 凭据,原子写成隔离目录里的
`.credentials.json`(0600,**绝不 log 内容**)。**darwin-only**:Linux/云端那个源文件存在,
永远走不到此分支,行为与 #76 逐字一致(零云端影响)。

**stage-once**(非 newest-wins):~~Keychain 无 mtime 可比,且每次 spawn 重导会覆盖 CLI
在隔离文件里刷新过的 token(重新注入已消费的 refresh token → 登出,正是 #76 newest-wins
要避免的)。故仅在隔离文件缺失时导出一次。~~ **⚠️ 2026-07-12 起已废弃 stage-once,改为按
`expiresAt` 的 newest-wins,见文件顶部条目**——原设计的"代价"(重新 `claude login` 后需手删
隔离目录)正是那次的修复目标。安全面:token 是本人本机、0600,与 codex 的明文
`~/.codex/auth.json`、claude-on-Linux 的 `.credentials.json` 同级。

`CliHelperSDK._run_claude_oneshot` 也会调 `_stage_claude_oauth_credentials`(见
[[cli_helper]]),使 claude helper 自足——agent 槽是 codex 或后台单独调 helper 时
隔离目录也能被 seed。

## 2026-07-09 — `_stage_claude_oauth_credentials`(OAuth 隔离目录的凭据搬运)

OAuth 的 `CLAUDE_CONFIG_DIR` 现在指向独立目录
`settings.claude_oauth_config_path`(见 [[api_config]] 2026-07-09 条),不再是
宿主 `~/.claude`。`agent_loop` 在 `to_cli_env()` 之后、spawn 之前,若
`auth_type == "oauth"` 就调用这个新的模块级函数,把宿主
`~/.claude/.credentials.json`(经 `provider_driver.derive.resolve_claude_credentials_path`
解析,尊重 `CLAUDE_CLI_CREDENTIALS_PATH`/`CLAUDE_CLI_HOME` 覆盖)**单文件**拷进隔离目录。
只拷 `.credentials.json`、绝不拷 `settings.json` —— 后者的 `env` 块正是劫持源。

**newest-wins**:仅当宿主副本比已暂存副本更新(或副本缺失)才覆盖;否则保留 CLI 在
隔离目录里就地刷新过的 token(避免把已轮转作废的旧 refresh token 回灌、把用户登出)。
宿主无凭据文件 → warn + no-op,不抛错。对齐 Codex 的 `_stage_codex_oauth_credentials`
(那边是 per-run temp `CODEX_HOME`;Claude 这边用持久隔离目录,与 keyed 路径同风格,
故用 newest-wins 而非每次覆盖)。守卫测试见
`tests/agent_framework/test_claude_config_isolation.py`。

**原子落盘(必须)**:`claude_oauth_config_path` 是**所有 OAuth agent_loop 共用的固定
目录**(不是 Codex 那种 per-run temp),staging 那一刻隔离目录里可能正好有一个 CLI 在读
`.credentials.json`。裸 `shutil.copy2(source, dest)` 会先 truncate `dest` 再写,重新打开
了本 fix 要堵的「半读 / 并发写」窗口(与当初 `~/.claude/.claude.json` 在 55KB↔50 字节
反复横跳同形)。所以落盘走**同目录临时文件 + `os.replace`**(POSIX 原子 rename);`copy2`
保留 mtime,rename 后 newest-wins 仍成立;`chmod(0o600)` 在 rename **之前**做,避免 `dest`
短暂出现 0644。

**已知代价 — 宿主可能被登出(单向拷贝的取舍)**:staging 是**单向** 宿主 → 隔离目录,
没有回写。若隔离目录里的 CLI 就地刷新了 OAuth token,宿主 `~/.claude/.credentials.json`
仍留着已被服务端轮转作废的旧 refresh token,用户自己的交互式 `claude` 在 access token
过期后拿旧 refresh token 去刷 → 401 → 被登出、需重新 `claude auth login`。DMG 模式下
agent_loop 与宿主是同一个人,体感尤其差,且只在数小时后 token 过期时才炸、难归因。
这是**已接受的取舍**,与 Codex 单向 `_stage_codex_oauth_credentials` 一致——当前不做
token 回写(真要回写,也必须走同样的原子 rename,否则回到上面「原子落盘」那条)。下一个
碰到「宿主被登出」的人:这是设计取舍,不是 bug,别再重推一遍这条链。

## 2026-07-03 — MAX_SYSTEM_PROMPT_LENGTH bumped 100K → 115K

Symptom-treatment for a bloated system_prompt observed on live agent
`agent_62cf67080ad4`: assembled prompts clocked in at 91–93K chars
across five consecutive turns, leaving only ~5–8K of the 100K char
budget for history. Source-aware eviction was dropping 20–23 of ~29
history rows on every turn, starving the LLM of NarraMessenger
context (silent-ingested rows in particular, since they're keyed
`_source != "chat"` and drop in Tier-1).

Direct cause: `SKIP_MODULE_DECISION_LLM = True` forces the loader to
inline all 15 modules' `get_instructions()` on every turn, regardless
of relevance. Sampled sizes: ChatModule 13K, CommonTools 8K, Slack 8K,
MessageBus 6K, Telegram 6K, Skill 4K, Discord 3K, Lark 2K,
NarraMessenger 0.8K, WeChat 0.7K, plus BasicInfo / SocialNetwork /
Awareness / Job (not measurable without an active ctx_data but ~15K
combined in production). Total steadily >90K.

115K keeps mixed-CJK content comfortably below
`MAX_SYSTEM_PROMPT_BYTES = 120 KiB` and the 128 KiB argv hard limit,
and gives history 20–30K of budget instead of 5–8K — enough to
retain the last full turn on IM channels where history rows are
long. This is TREATMENT, not cure; the root fix is a
module-selection loader that only inlines instructions relevant to
this turn's channel/context (deferred as a separate follow-up per
the design note added inline at the constant's block comment).

## 2026-07-03 — 0-message run emits a classifiable error (no more silent fallback)

When the Claude CLI yields 0 messages (expired OAuth / not logged in / crash /
quota) the generator used to only log and end, so the pipeline read no-messages
as "agent chose not to reply" and the helper-LLM fabricated a hollow fallback —
the Owner reported "mysterious fallback, no error". It now yields
_zero_output_error_event (a response.error carrying the raw CLI stderr).
Classification stays in response_processor._is_auth_failure: an auth/login
stderr becomes a fatal AUTH_EXPIRED (re-login prompt, no_reply fallback skipped);
anything else stays a recoverable no-output error. The base sentence is kept
auth-phrase-free so an empty stderr is never misclassified as auth. Guarded by
tests/agent_framework/test_zero_output_error_event.py.

## 2026-07-03 — main-loop model normalized via `resolve_cli_alias` (upstream #57)

`options_kwargs["model"]` passes through `resolve_cli_alias(model,
auth_type)`: bare family aliases become full ids on api_key/bearer
transports, stay verbatim on OAuth. Complements the earlier
`_is_claude_native` fix (906312b5) which only adjusted tool policy, not
the model string itself.

## 2026-06-11 — thinking 走 --effort,绝不发 --max-thinking-tokens 正数

CC 在当前代 Claude 模型上每轮 400(`"thinking.type.enabled" is not supported
for this model`,被 `AGENT-LOOP-RECOVERABLE: Claude API error: unknown` 盖住)。
**根因不在我们的 API 形状,而在 SDK→CLI 的翻译链**(2026-06-11 实测
`claude_agent_sdk/_internal/transport/subprocess_cli.py` + CLI 2.1.x):

1. SDK **把 `ClaudeAgentOptions.thinking` 全翻成 `--max-thinking-tokens N`**
   (adaptive→32000、enabled→budget、disabled→0),**从不发 `--thinking
   adaptive`**;
2. Claude Code CLI 把**正数的 `--max-thinking-tokens`** 当成旧版
   `thinking:{type:"enabled",budget_tokens:N}` 发给 API → 当前模型 400;
3. CLI 唯一的 adaptive 开关是 **`--effort <level>`**(`--help` 仅此一个;
   无 `--thinking`)。给 `--effort` 且不给 `--max-thinking-tokens` → adaptive;
   什么都不给 → 退回被拒的 enabled。

> 之前一版误以为"把 `thinking` 设成 adaptive dict"就行——错。SDK 会把它
> 变成 `--max-thinking-tokens 32000`,CLI 照样发 enabled,还是 400。

**正解**(`_resolve_reasoning_options`):
- **on / auto / 未知** → 只回 `{"effort": <level>}`,**不带 `thinking` 键**
  (SDK 因此不发 --max-thinking-tokens,CLI 走 adaptive)。auto/未知 effort
  兜底 `"high"`(Anthropic server 默认),**保证 --effort 一定在**——没有任何
  flag 时 CLI 会退回 enabled。
- **off** → `{"thinking": {"type": "disabled"}}`(→ --max-thinking-tokens 0,
  唯一不 400 的 max-thinking-tokens 值;off 时不带 effort)。

我们任何路径都不产生正数 --max-thinking-tokens,故永不发 enabled。
版本背景:PATH `claude` 2.1.39 / SDK bundled 2.1.56,两者都靠 --effort 走
adaptive,此改法版本无关。局限:故意 pin 只认 enabled+budget_tokens 的旧模型
(如 Sonnet 4.5)在此拿不到思考预算——平台面向当前模型。测试:
tests/agent_framework/test_claude_reasoning_mapping.py。

## 2026-06-10 — Neutral reasoning params → Claude dialect (L1c)

`_resolve_reasoning_options(thinking, reasoning_effort)` maps the
framework-neutral slot params (carried on `ClaudeConfig` from the agent
slot) to ClaudeAgentOptions kwargs: `on`→`{"type":"adaptive"}`,
`off`→`{"type":"disabled"}`, effort low/medium/high/max passes 1:1 via
`effort=`; `""` (auto) emits nothing so the CLI keeps its defaults —
byte-identical behavior to before when unconfigured. Out-of-vocabulary
values (corrupted state) degrade to auto with a warning, never raise.
Per rule #15 the values are passed even to non-Claude proxies; the
`Provider config` log line now includes `thinking=`/`effort=` for
post-hoc grep. Tests: tests/agent_framework/test_claude_reasoning_mapping.py.

## 2026-06-10 — L1a cleanup (SDK 0.1.43 alignment)

Three obsolete-workaround removals after auditing the installed
claude-agent-sdk 0.1.43 against the official Agent SDK docs (2026-06-10
adapter research, author-local):

1. **`_safe_parse_message` monkey-patch DELETED.** 0.1.43's parser
   natively returns `None` for unknown message types ("Forward-compatible:
   skip unrecognized message types") and both call sites filter `None`.
   The patch was also ineffective on the main path: `_internal/client.py`
   binds `parse_message` at import time, so reassigning the module
   attribute never reached it. Removing it also drops both
   `claude_agent_sdk._internal` imports from this file's import block.
2. **`max_turns=0` → `None`.** The transport emits `--max-turns` only for
   truthy values, so 0 meant "unlimited" by accident. If upstream ever
   switches to `is not None`, 0 becomes a zero-turn hard cap on
   agent_loop (铁律 #14 violation). None is the documented unlimited.
3. **pyproject pin `>=0.1.6` → `~=0.1.43`.** This file still deliberately
   reaches into SDK internals in two places (`_transport._process` for the
   stall probe and the SIGKILL disconnect fallback — both re-verified as
   still necessary on 0.1.43: `transport.close()` remains terminate() +
   unbounded wait()). A loose pin lets `uv lock` drift the SDK (and its
   bundled CLI — 0.1.43 ships CLI 2.1.56) under those private-attr reads.
   Upgrades are now explicit via `uv lock --upgrade-package`.

## 2026-05-22 — stall health-probe diagnostic (#7, partial)

The silent-probe cadence (`IDLE_PROBE_SECONDS`) now reads
`settings.llm_stall_probe_after_seconds` (.env-tunable). When a run is truly
silent that long AND the CLI subprocess is alive, we now ALSO fire
`_probe_provider_reachable(base_url, …)` — a cheap out-of-band request to the
provider endpoint — and log "provider REACHABLE (model thinking)" vs
"UNREACHABLE (connection dead)". This is the dead-vs-thinking signal for a
prolonged silence. **Diagnostic only — it never force-stops the run** (铁律
#14); the transport-level recovery is the per-request `API_TIMEOUT_MS` + CLI
retry (set via `api_config.to_cli_env`).

**Deferred (NOT yet implemented):** the active `interrupt()` + re-issue
auto-recovery on a confirmed-dead provider (the discussed "路径2"). It's risky
surgery on this shared streaming loop and needs integration testing against a
mock stalled provider before shipping (author-local todo).

## 2026-05-19 — Source-aware history truncation

Replaced the old "append history → `[:100_000]` the whole string" eviction
with a source-aware loop that PROTECTS the system prompt. Background
trigger rows (`_source ∈ {job, message_bus, lark, callback}`) are
dropped oldest-first; chat rows are only dropped once all background
rows are gone. Implementation reads `_source` (set by
[[context_runtime.py]] from `meta_data.working_source`) — DB rows are
never modified, this only governs what gets sent to the LLM this turn.
Belt-and-braces char + UTF-8 byte ceilings stay as last-resort guards
for the case where the system prompt itself overruns argv. Fixes the
"system instructions tail gets chopped" bug observed when history grew
large enough to push the combined string past 100K chars.

## 2026-05-19 — IDLE_TIMEOUT replaced with IDLE_PROBE (铁律 #14)

`IDLE_TIMEOUT_SECONDS = 600` used to `raise TimeoutError(...)` whenever
the CLI emitted no message for 10 minutes. This was a hard cap on
`agent_loop` and violated 铁律 #14 — DeepSeek-V4-Pro CoT and other
long-thinking models legitimately produce minutes-long silent passes,
and memory `agent_long_silence_deepseek` (2026-04 notes) already
recorded this as a known false positive.

Renamed to `IDLE_PROBE_SECONDS` and turned into a *probe* cadence
rather than a kill switch:

1. Every IDLE_PROBE_SECONDS of silence, peek at the CLI subprocess
   `_transport._process.returncode`.
2. `returncode is None` (alive) → `logger.warning("...continuing to wait")`
   and re-enter `asyncio.wait` with the **same** in-flight
   `message_task` (so the SDK's `__anext__()` isn't lost across the
   probe).
3. `returncode is not None` (subprocess actually exited) → log ERROR
   and `raise RuntimeError(...)` — this is a genuine failure, not LLM
   thinking time.

Mechanical changes that follow from "keep message_task across
iterations":

- The per-loop `finally:` now cancels only `cancel_task` (per-iteration);
  `message_task` is owned by the outer function-scope `try`.
- The function-scope `try` hoists `message_task: asyncio.Task | None =
  None` before its first use so the outer `finally:` can cancel + drain
  it without NameError even if `connect()` raised early.
- `message_task = None` is assigned at every consume site (after
  `.result()`, after `StopAsyncIteration`, after cancellation, after
  the subprocess-dead path) so the next iteration creates a fresh task.

## 2026-05-13 — Phase A C1+C2 (race-with-cancel + SIGKILL fallback)

### Race-with-cancel receive loop

Receive loop 从 `asyncio.wait_for(__anext__(), IDLE_TIMEOUT_SECONDS)`
改成 `asyncio.wait([message_task, cancel_task], FIRST_COMPLETED, timeout=IDLE_TIMEOUT_SECONDS)`。

- 两个 awaitable：`response_iter.__anext__()` 和 `cancellation.await_cancelled()`
- 先完成的赢；未完成的在 finally 里强制 `task.cancel()` 避免悬挂
- 如果 cancel 赢了 → `is_cancelled` 是 True → break
- 如果都没在 timeout 内完成 → 旧的 idle-timeout 兜底（认为 CLI 卡死，raise TimeoutError）
- 如果 message 赢了 → 正常 `.result()` 取出（包括 StopAsyncIteration 自然结束）

**关键修复 effect**：cancel 在 tool call 进行中（没有 message 流出）也能即时
检测到。Xiong 那种 13min run 中途 stop 不再被 receive loop 卡住。

### SIGKILL fallback in disconnect

`finally: await client.disconnect()` 改成 `await asyncio.wait_for(client.disconnect(), 5.0)`，
TimeoutError 时通过 `client._transport._process.kill()` 直接 SIGKILL Claude CLI 子进程。

原因：claude_agent_sdk transport.close() 内部 `terminate()` + 无限 `wait()` —
如果 Claude CLI 忽略 SIGTERM 或卡 cleanup 永远不返回。代价是 reach into 第三方
SDK 的私有属性（transport._process），但这是唯一保证 finite-time 子进程回收的
方式。

# adapters/claude/sdk.py — Claude Code CLI 主 Agent Loop 适配层

## 2026-07-27 — 事件类型字面量收敛到 loop/events.py 常量

六种事件形状的字符串字面量改为 import `loop/events.py` 的常量
（TYPE_RAW_RESPONSE_EVENT 等），值逐字节不变——纯机械替换，行为零变化。
事件契约自此有唯一事实源，详见 events.py.md。


## 为什么存在

Claude Code CLI 是一个独立的命令行工具，通过 `claude_agent_sdk` Python SDK 以子进程方式驱动。这个文件把 SDK 的低级接口（connect/query/receive_response）封装为系统期望的 `async generator` 接口，并处理：多轮对话历史拼接到 system prompt（CLI 不原生支持多轮）、流式消息格式转换（通过 `output_transfer.py`）、`tool_call_id` 去重（`include_partial_messages=True` 导致的重复事件）、取消信号传播、空消息检测、idle timeout。

## 上下游关系

被 `step_3_agent_loop.py` 调用，在 Step 3.4 中启动 agent loop，接收所有流式事件并 yield 给上层。上层拿到的事件由 `response_processor.py` 解析为类型化消息。

配置通过 `api_config.claude_config`（ContextVar proxy）获取，确保每个 asyncio task 使用 owner 的配置。MCP 服务器 URL 由调用方传入（`mcp_server_urls`），包含所有激活 Module 的 MCP 端点。

`output_transfer.py` 是直接依赖，把每条 Claude SDK 消息转换为事件列表后才 yield。

## 设计决策

**多轮对话拼接到 system prompt**：~~Claude Code CLI 的 `ClaudeAgentOptions` 不支持 messages 数组，只有 `system_prompt` 和单条 `query`。所以所有历史对话都被格式化为文本追加到 system prompt 末尾，超出 60KB 时截断保留最近部分。这是已知限制，等 SDK 支持 multi-turn 后可以去掉。~~ **⚠️ 2026-07-28 起已部分过时**："SDK 不支持多轮"已被 E1 证伪（`ClaudeAgentOptions.resume` 跨进程可用）。现在只有**冷启动轮**（无有效句柄）仍走历史拼接（materializer 的 `assemble_argv_prompt`）；resume 轮历史在 CLI session 文件里，prompt 只带 system 指令。见顶部 2026-07-28 resume 条目。

**`_safe_parse_message` monkey-patch**：已于 2026-06-10 删除（见顶部 L1a 条目）——SDK 0.1.43 原生跳过未知消息类型，patch 在主路径上本就未生效。

**`NO_PROXY` 和 `CLAUDECODE` 环境变量注入**：系统代理可能导致 Claude CLI 子进程访问 localhost MCP 服务器走代理返回 502。`CLAUDECODE=""` 是为了防止嵌套 Claude Code 会话检测阻止子进程启动（当后端在 Claude Code 终端内运行时）。

**`max_buffer_size=50MB`**：MCP 工具（如 PDF 解析）可能返回大量内容，默认 buffer 太小会导致响应被截断。

**600 秒 idle timeout**（Bug 20, 2026-04-20 从 1200s 下调）：用 `asyncio.wait_for` 包装每次 `__anext__()`，超过 10 分钟 CLI 静默则抛 TimeoutError。原来 1200s 是基于"给 MCP tool call 足够空间"的保守估计；事故后每个 MCP 工具 handler 通过 `with_mcp_timeout` 自限在 ≤60s，Claude CLI 内置 tool 自己有更短 timeout，**真实工作下 600s 静默 = 一定出 bug**，早点 TimeoutError 让错误更快现形。

**两道 system_prompt 上限：char ceiling + UTF-8 byte ceiling**（2026-04-22 调整）：
Python SDK 用 `--system-prompt <str>` argv 传 prompt 给 `claude` CLI；Linux
`MAX_ARG_STRLEN = 128 KiB`（x86_64 典型）。旧版只按 `len()` 字符数限制到 60K，对
纯英文安全，但对中文（UTF-8 3 bytes/char）理论最坏只能承载 ~42K 字节。T8 禁用
ToolSearch 后，非 Claude 模型的 system prompt 常态化到 60-80K chars（全量 MCP
工具 schema），60K 限制频繁截断。现在改成两道闸：
- **MAX_SYSTEM_PROMPT_LENGTH = 100_000 chars**：给 T8 场景留出 20-40K 余量
- **MAX_SYSTEM_PROMPT_BYTES = 120 KiB**：encode('utf-8') 后超出则按字节二次截断，
  `decode('utf-8', errors='ignore')` 丢掉被截断的半字符，保证输出始终是合法 UTF-8。
- **MAX_HISTORY_LENGTH = 50_000 chars**（从 30K 上调）：让 MiniMax 多轮场景保留
  更多历史。history 在进入 system_prompt 前单独预截断，与总长限制正交。

**按模型名决定是否启用 ToolSearch / deferred tool loading**（2026-04-22 引入）：Claude Code CLI 在工具总量超过 `ToolSearchCharThreshold` 时自动启用 deferred tool loading —— 给 LLM 一个工具索引，具体 schema 通过 `ToolSearch(select:X)` 按需加载并以 `tool_reference` block 返回。这个协议是 Claude Sonnet-4+ / Opus-4+ 的扩展，**非 Claude 模型（MiniMax / GPT / Gemini 等）通过 Anthropic-compatible 代理调用时看不懂 `tool_reference`**，表现为 LLM thinking 里抱怨 "the tool registry is not finding the chat module send_message tool"、整段 turn 静默结束（Pattern A 的硬证据见 TODO-2026-04-22 T7）。现在根据 `claude_config.model` 是否以 `claude-` 开头在 `cli_env` 组装时做决策：Claude 原生模型走 CLI 默认 `auto` 模式继续享受 deferred 省 token 收益；非 Claude 模型显式 `ENABLE_TOOL_SEARCH=false`，CLI 把所有工具全量暴露给 LLM、不再依赖 `tool_reference`，MiniMax 等模型可稳定 invoke。决策同步写进 `Provider config` 日志行的 `tool_search=` 字段，方便事后 grep。

**`build_tool_policy_guard` 注入 PreToolUse hook 做沙箱**：CLI 本身没有工作空间隔离概念，也不知道 WebSearch 需要 Anthropic 服务端工具。我们在这里装一个 hook（`_tool_policy_guard.py`），在云端部署下强制 Read/Glob/Grep 只能访问 workspace、Bash 不允许全局安装（brew/npm -g/apt/sudo/裸 pip），在任何模式下把 `lark-cli` shell-out 重定向到 MCP、把无 server-tool 的 provider 调 WebSearch 拦下来改用 WebFetch。hook 在 `permission_mode="bypassPermissions"` 之前触发，所以即使 bypass 也生效。`HookMatcher` 的 `matcher` 必须覆盖 `Read|Glob|Grep|WebSearch|Bash`。

## Gotcha / 边界情况

- `include_partial_messages=True` 导致 partial 和 complete `AssistantMessage` 都携带 `ToolUseBlock`，同一 `tool_call_id` 会出现两次。去重通过 `seen_tool_call_ids` set 在这里处理，`output_transfer.py` 不处理去重。
- 0 条消息收到时 log error 但不抛出异常——调用方会收到一个空 `final_output` 的 `PathExecutionResult`。这是静默降级，可能让用户看到空回复而不是错误提示。
- `client.disconnect()` 在 cancel scope 错误时被静默忽略（anyio cancel scope 兼容性问题），正常 RuntimeError 仍会抛出。
- **ToolSearch 判断依赖 `claude_config.model` 非 None 且以小写 `claude-` 开头**。如果某调用路径没给 model（slot 配置缺失 / 默认 fallback），`(claude_config.model or "")` 为空 → `startswith("claude-")` 为 False → 走非 Claude 分支禁用 ToolSearch。这是安全方向 fallback：宁可多烧一点 token 也要保证工具可调用。若未来接了大写写法或别名的 provider，需扩展这条判断而不是简单复制。

## 新人易踩的坑

- `this_turn_user_message = (messages.pop())["content"]`：这里假设最后一条消息是 user message。如果调用方构建 messages 时最后一条不是 user message，会产生逻辑错误。代码注释里也标注了这个 TODO。
- 直接在本地测试时，`claude` CLI 必须已经登录（`claude auth login`），否则会收到 0 条消息且没有明显错误——只有 stderr log 里有认证失败信息。

## 2026-07-29 (二次) — 自建 transcript 时,任何 resume 失败都必须回落

`stale_handle` 的判定原来要求 CLI stderr 出现
`No conversation found` 这个短语。自建 transcript 时**取消这个要求**:句柄是我们
刚写的、按构造就是有效的,所以 CLI 拒绝 resume 只能是我方 bug,而冷启动重试在
任何情况下都是对的答案。

这是被上线首日打出来的。cwd slug 没转换点和下划线,文件落到了隔壁目录,turn
之所以没死,**纯粹因为 CLI 恰好回的就是这个断言在 grep 的那句话**。换成任何别的
拒绝理由(记录畸形、CLI 升级换格式),turn 会直接失败并把错误显示给用户——铁律
#14 和 #16 同时禁止。安全网不该依赖一个为**另一种**故障写的字符串匹配。

界限没变:仍然**最多重试一次**,且仅在尚未产出任何内容时。产出之后的失败照旧抛出
(重跑会重复已发出的内容)。

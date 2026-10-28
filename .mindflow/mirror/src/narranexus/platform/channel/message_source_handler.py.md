---
code_file: src/narranexus/platform/channel/message_source_handler.py
last_verified: 2026-09-07
stub: false
---

## 2026-09-07 — 全局字典换成注册表视图；import 期不再有任何注册

`MessageSourceRegistry._handlers` 这张类变量字典没了。它是内核注册表的影子：同样的职责，
却由九处模块顶层 `register(...)` 写入，八处外面裹着 `except ValueError: pass`——那个
`pass` 把「两个插件抢同一个 source 名」这个真冲突静音成了「谁先 import 谁赢」，而
`register` 自己的 docstring 还写着「重复注册抛异常是故意的」。

现在 `MessageSourceView` 在**调用时**读两张注册表，按各自 `names()` 元组缓存（照
`TriggerMapView` / `_ChannelSpecs` 的形状，因为 `get()` 每轮要调好几次、每渲染一行历史
还要再调一次）：

- `ingress.channels` — 每个 `ChannelDescriptor` 投影出自己的 handler；
- `ingress.message_sources` — 非渠道来源的 `MessageSourceSpec`。

`MessageSourceRegistry` 保留成一个无状态的命名空间（十几个调用点写法不变），**没有
`register()`**：一个来源只能通过它所属插件的 contribution 进来，这正是让 owner、
`builtin_overrides` 禁用、发行版排除对它生效的原因。

**默认 handler 的语义收紧了。** 它现在只答 `SOURCELESS_SOURCES`（owner 聊天 / `a2a` /
`callback` / `skill_study`）——这四个是平台自己那张表，写成数据而不是「凡是查不到的」，
就是为了把它们和「本该有 handler 却没有」区分开。一个**已注册的渠道**落到默认 handler
会按名字去重地打 WARNING：那正是原来无声吞掉的场景（IM 回复被判成没回复、行前缀变成
`[NarraNexus UI]`），现在它是信号。

`_lazy_extractor` 把 `"pkg.mod:function"` 包成首次调用才 import 的闭包——构建视图是读操
作，急切解析会让「问一句有哪些来源」拖进每个渠道的 SDK，那正是本次要拆掉的耦合。

`im_channel_prefixes()` 因此也自动跟着注册表走：渠道的描述符一进注册表就被覆盖，发行版
一排除就消失，不再依赖「谁 import 过谁」。

## 2026-08-17 — 来源声明从这里渲染；owner 工具判定收归一处

新增三样：

1. `display_label` + `label` 属性——面向 **agent** 的来源名。用 agent 的世界模型措辞：
   平台内部一律 `NarraNexus`（bus 是它看不见的基础设施，冒出「Message Bus」等于凭空
   多一个概念），各渠道用自己的品牌大小写。
2. `render_origin_declaration(working_source, expressive_tools)`——设计 §6.1 的那一行。
   两半都来自平台已经算好的数据：label 来自本 registry（决定哪些调用算回复的同一条
   记录），工具来自该轮 `expressive_tools` 的**同一个 tuple**。所以这句话不可能和
   桌子矛盾——没有第二份副本可以漂。工具为空时返回空串：没有回复面的轮次不能被塞一句
   声称有回复面的话。
3. `is_owner_tool()`——`reply_owner` / `notify_owner` 的统一判定。放这里是因为本模块
   已经必须同时推理这两个名字（默认 handler 两个都列）。**问「到没到 owner」的必须两个
   都认；问「是不是渠道自己的工具」的必须两个都拒**——只写死一个的过滤器让另一个溜了
   过去，IM 兜底帧因此被标成 `reply_owner`，会在 owner 聊天面板里显示成 agent 在跟他
   说话。

默认 handler 的 `user_reply_tool_names` 现在两个 owner 名字都列。owner 自己的聊天轮
落到这个 handler（没有显式的 "chat" 注册），它桌上是 `reply_owner`；只列一个会让
`_has_organic_reply` 在用另一个的表面上瞎掉——回答得完美的聊天轮读成「从没说话」，
helper-LLM 兜底会在每一轮成功的对话上再写一条回复。

各 trigger prompt 里对「怎么回复」的重述随之删除，见 [[channel_prompts]]。


## 2026-08-07 — `PLATFORM_REPLY_TEXT_KEY`：平台自己写的回复优先于渠道抽取器

`extract_reply_text` 现在**最先**看 `arguments["_platform_reply_text"]`，命中就直接用，
不再走 `extract_reply_fn` / `content`。

为什么需要它：`step_3` 的 1:1 私聊兜底（`no_reply_im_dm`）是**平台经
`ChannelSenderRegistry` 把回复发出去**的，不是模型调渠道工具。于是它合成的那一帧
**不符合任何渠道真实工具的参数形状**，而每个渠道的抽取器都只认自己那一套：

- 微信读 `arguments["text"]` → 读不到就退回它的 `"(sent via wechat_send)"` 占位符，
  于是**聊天记忆里存的是占位符**，下一轮上下文丢掉这条回复；
- 飞书解析 `lark_cli` 的 `command` 字符串 → 返回 `None`，于是**一条已经投递成功的
  回复被记成沉默**。

两个渠道、两种相反的失败模式，所以修在这一层修一次，而不是让编排层去认每个渠道的
参数名（那会把渠道知识倒灌进 `step_3`，违反铁律 #3 的方向）。这段文本的权威来源正是
我们自己——它是平台写的。

键名带前导下划线是刻意的：它是我们的内部标记，不属于任何渠道的工具 schema。

发现过程：`tests/agent_runtime/test_im_dm_fallback_delivery_e2e.py` 的跨层断言
（step_3 发帧 → chat_module 独立判定）在第一次运行时就抓到了占位符，人工三轮真机
Telegram 测试完全碰不到这条路径。

## 2026-08-04 — extract_reply_text 空白复判 + 三态返回契约（空气泡根因）

citation strip 之后复判空白：gpt-5.x + WebSearch 场景「几乎全是
citation token 的回复」被剥成 `"\n"`，truthy 穿过原有的 `if not text`
falsy 判定，一路落库成空气泡（2026-07-13 报告的真凶）。返回值改为三态
契约：非空 str=回复文本；`""`=**是**回复调用但文本剥空（含 content
缺失）；None=根本不是回复调用（工具名不匹配，或自定义 extractor 拒绝
如 lark_cli 非发送命令）。所有 falsy 消费方（chat_module split、
_delivered_to_origin、step_4 锚点）行为一致；唯一需要区分的消费方是
openai_compat._classify_event——`""` 丢弃事件、None 落 tool_call，按
is_user_reply_tool 名字判会误吞 lark_cli 非发送命令（review 建议的原
方案即此缺陷）。语义影响：纯空白回复算「未交付」——剥完只剩空白说明
本来就没有实质回复。owner-visible 路径经 extract_owner_visible_text
委托，同点覆盖。

## 2026-08-04 (review 三) — effective_owner_visible_names 属性

None 回落规则收敛到单点：属性返回「生效的 owner-visible 名单」，
is_owner_visible_reply_tool 与 chat_module 的日志共用，日志不再手抄
回落逻辑（review round 2 Minor #4：手抄的规则将来必先漂）。

## 2026-08-04 (review 修正) — owner_visible_reply_tool_names：「交付给来源」≠「owner 可见」

PR #230 review 抓到：一份 `user_reply_tool_names` 名单被三个消费方共用，
bus 名单扩容后「bus 交付」被 step_4 误当「给 owner 发过消息」→ owner 会话
锚点被 A2A 回复劫持。拆成两个谓词：
- `user_reply_tool_names` = 交付给「联系你的人」（度量/NO-REPLY 判定语义）。
- `owner_visible_reply_tool_names`（新，默认 None=回落前者）= 输出出现在
  owner web chat。chat/IM 渠道天然两者相同（会话对象就是 owner）；bus
  override 为仅 send_message_to_user_directly（对端是 agent）。
新方法 `is_owner_visible_reply_tool` / `extract_owner_visible_text`（复用
extract_reply_text 的自定义 extractor 路径）。消费方：step_4 锚点判定与
chat_module 的 user-visible split 用 owner-visible；extract_reply_text
保持「交付」语义留给度量与未来兜底。

## 2026-07-03 — `dedicated_trigger` flag + `handlers()` accessor

`MessageSourceHandler` gains `dedicated_trigger: bool = False`: True for
sources with their own long-running trigger process (all six IM channels).
MessageBusTrigger derives its do-not-redispatch channel prefixes from this
flag via the new `MessageSourceRegistry.handlers()` snapshot accessor,
replacing a hand-maintained prefix tuple that had drifted (wechat/discord/
narramessenger missing → double dispatch). Any module shipping a
run_*_trigger.py entrypoint MUST set the flag — enforced by
tests/message_bus/test_bus_channel_inbox_skip.py.
# message_source_handler.py — 按 WorkingSource 分发的聊天历史处理表

## 为什么存在

每个 `WorkingSource`（`chat` / `lark` / `message_bus` / `job` / `a2a` / `callback`
/ `skill_study` / 未来的 channel…）映射到一个 `MessageSourceHandler`，替 chat-history
pipeline 回答两个问题：

1. **写侧**——本轮 agent 有没有通过这个 source 的工具回复用户？
   （`is_user_reply_tool` / `extract_reply_text`）
2. **读侧**——这条落库的行喂给 LLM 时该打什么标签？（`format_row_prefix`）

用 `MessageSourceRegistry` 全局注册表而不是 `if working_source == "lark": ...`，是为
落实铁律 #3（模块互不 import：chat_module / context_runtime 不能 import lark_module /
message_bus）和铁律 #4（通用分发在这里，per-source 知识跟着各自模块走）。新 IM trigger
上线 = 一行 `Registry.register(...)`，别处零改动；`dump()` 可打全表方便 debug。

需要定制的 channel（Lark 把回复塞进 `command` 的 `--markdown` flag）注册自带的
`extract_reply_fn`；不需要定制的 source 全落到 `_DEFAULT_HANDLER`。本文件纯配置 +
纯函数，无 I/O / async / DB。

## 2026-06-17 — 在回复抽取唯一收口处剥离 Responses-API 引用标记

PR #25 给 `extract_reply_text` 加了一道内容层清洗：剥掉 OpenAI Responses-API 在跑过
WebSearch 后内联吐进用户可见文本的 "citation" 标记（形如 `citeturn6view0`、
`citeturn2news12`，2026-06-08 gpt-5.5 via codex 观测到）。

为什么剥而不解析：ChatGPT 自家前端会把这些标记用一张单独的 annotation 表渲成可点的
Markdown 链接，但 `openai-codex` Python SDK 0.1.0b3 不暴露那张表
（`OutputTextContentItem` 只带 `{text, type}`），拿不到 URL/title 映射就没法渲染正经
链接——务实做法是直接剥掉，让用户看到干净文本而不是黏在句尾的天书标记。

为什么剥在**这里**而不在 per-framework translator：这些标记是 model 写进
`send_message_to_user_directly`（或任意回复工具）`content` 参数里的**纯字符串内容**，
不是 SDK 协议元数据；在 SDK 边界剥会漏掉 model 写进 `lark_cli` / `slack_cli` /
`tg_cli` markdown 的同类标记。而**每个 channel 的回复都汇流经过本方法**，一处剥覆盖
全部 channel。

实现要点 / gotcha：

- `_strip_responses_api_citation_tokens` 带 `if "cite" not in text` 快路径，无标记
  原样返回。剥完还会收尾：合并双空格、去标点前空白（中英标点都管，i18n 安全）、去行尾
  水平空白（段尾那个 token 形状的洞）。
- 正则 `cite[a-z]+\d+[a-z]+\d+` 要求 cite 后**两轮 alpha+digit**，避免误伤英文单词
  "cite" 后接名词。
- 同时导出 `strip_responses_api_citation_tokens`（无下划线公开别名）供模块外调用方
  （主要是 `response_processor` 给 live UI 流式构建 ProgressMessage 时）复用同一套剥离。
- `extract_reply_text` 被重写成：先从 `extract_reply_fn` 或默认 `content` 取出
  `text`，统一过一遍剥离再返回——无论哪个 extractor 产出，剥离都一致生效。

## 2026-08-18 — `render_origin_declaration` 新增 `reply_is_plain_text`

这行的前提是「origin-first 排序把来源模块的工具放在位置 0」—— 只在来源模块**确实声明了
东西**时成立。patrol 轮声明为空（回复就是纯文本、由平台以房间 marker 张贴），位置 0 于是
落到排在后面的 `notify_owner`，而那行会写成「回复请用 notify_owner」：把 lead 推去给 owner
发消息，而不是写房间状态行 —— 既是错的动作，也正是 patrol 提示花一整段禁止的动作。

registry 分不出这件事，因为 `notify_owner` **确实**是 message_bus 的合法回复工具之一（用
「告诉 owner」来应答一个 bus 轮次是允许的）。我一度想用 `is_user_reply_tool(tools[0])` 判，
那条不变量是错的，且会在未注册来源上把覆盖缺口变成静音轮次。所以改由调用方传入这个事实 ——
平台本来就把它标成了 `BUS_PLAIN_TEXT_TURN_EXTRA_KEY`（见 [[hook_schema.py]]）。

其他模块在这种轮次上保留自己的工具（巡检中向 owner 升级是合理动作）；不该发生的只是「用一句
话把其中之一说成本轮的应答方式」。

## 2026-08-18 (二) — `im_channel_prefixes` 搬到数据所在处

它原本住在 [[message_bus_trigger.py]]，但读的是本文件的 registry；而
[[local_bus.py]]（比 trigger 更低的层）现在也需要它，让 bus import trigger 是错的依赖方向。
搬过来后两个消费者读同一份定义。

两个消费者防的是不同的东西：trigger 防**重复派发**（旧 IM 频道的消息它们自己的 trigger 已经
跑过 AgentRuntime），`_unread_predicate` 防**注入**。同一批行，两种伤害。

它不是死代码，不能按铁律 #2 删：写入器没了，但行还在每个已部署的库里。可退休的条件写在
回填 runbook 里 —— 而且只有 `_unread_predicate` 那道能退，trigger 那道与行是否还在无关。

---
code_file: src/narranexus/platform/module_system/managed_channel_ingress.py
last_verified: 2026-08-28
stub: false
---

## 2026-08-26（下午）— 这里盖的是**字典**，模型看不到

⚠️ **本文件的 gotcha，从这一侧进来的人必须先知道**：这里的盖章只写
`trigger_extra_data["channel_tag"]` 这个**字典**。而模型读的是
`build_inbound_run_context` 早就渲染好的那个字符串——它在这些 hooks
**之前**就定型了。

让信号真正到达模型的是 [[openai_compat.py]] 在 hooks **之后**调的
[[sync.py]] `retag_managed_input`（用已盖章的字典重建那一行）。
**两者缺一，这个信号就到不了模型。**

这不是假想：上一轮 review 抓到的正是这个 Critical——盖章在，但发生在渲染
之后，于是托管 A2A DM 的 tag 与人类对话逐字节相同，而 DM 协议里那条
「若标记为 `agent sender`」成了模型永远走不到的分支。而
`MatrixTrigger.is_agent_peer` 是全仓唯一答得出这个问题的实现，
NarraMessenger 又恰好跑在托管模式下——受影响面正是这个信号唯一有用的地方。

所以**删掉或挪动 `retag_managed_input` 会原地复现那个 Critical**。
`test_agent_peer_signal.py` 里那条端到端断言（真的调 `before_run`，不是手抄
盖章逻辑）是这条链的回归防线。

## 2026-08-26 — 托管路径自己盖 `is_agent_peer`

原生路径在 `build_trigger_extra_data` 里填，托管路径**不跑 context
builder**，所以那条填充够不到它。不盖的话，每一个托管 A2A DM 对下游都读作
人类对话。

用 try/except 包住，与 `_stamp_turn_envelope` 包住 `managed_reply_kwargs`
同一个理由：一个坏到答不出「对面是不是机器」的 trigger，代价应该是这一轮
少一个信号，不是整个渠道塌掉。

**只在为真时写键**（不是「降级为 False」——代码里现在没有任何一处写 `False`，
except 分支什么都不写）。与 `ChannelTag.to_dict` 丢假值同一条规则，所以托管
turn 与原生 turn 持久化的 tag 键集合一致；键缺失在下游本来就读作 False。

## 2026-08-25 — 托管侧**不做** warm_start（记录在案的决定，不是遗漏）

原生路径在 `start()` 里 `await guard.warm_start(...)`，托管侧没有。这是
有意的，不是「一个 seam 四个挂载点」在这里破了：

- `_guard()` 是**同步**、按需懒建，托管协调器没有 `start()` 那样的异步启动
  钩子可挂；
- 更根本的是**托管侧没有要喂的东西**：warm_start 买来的只有观测面
  （`health_snapshot` / 心跳），而托管模式没有这类常驻观测面；
- 冷却本身跨重启依然生效——靠 `_load()` 逐 key 懒加载，那与预热无关。

**将来 Manyfold 长出自己的 `/healthz` 时这条要重新评估**，否则缺口会以
「托管渠道永远报 0」的形式回来，而 `test_ingress_guard_all_paths` 钉的是
`_ingress_admitted`，钉不住生命周期钩子的对齐。

## 2026-08-24 — 托管路径自带 ingress 熔断器

托管模式绕开**整条**原生接收路径（无 `_subscribe_loop` / dedup store /
worker 队列 / `_process_message`），而 [[ingress_guard.py]] 在
`ChannelTriggerBase.start()` 里构造——托管模式从不调 `start()`。两件事叠起来
的结果是：不单独接线的话，**Manyfold 面会是唯一一条没设防的入口**。

所以协调器自己持有 per-channel 的 guard（`self._guards`），并且**通过
`trigger.build_ingress_guard(db)` 构造，不手抄那七个阈值**——某个渠道收紧
自己的数字时要两条路一起收紧。`test_managed_guard_reuses_the_channel_tunables`
钉住这一点。

**失败语义显式选 open**：本文件的既有要求是每个 managed gate 都要明确选边
（narramessenger 的 authorize hook 是 fail-closed，因为它**是**授权门）。
熔断器是限流不是授权，guard 抛异常 / 建不出来 → 放行。

**已知取舍（预审记录）**：`self._guards` 按 channel 缓存，guard 持有的是
**首次**传入的 db client。`get_db_client()` 是 per-event-loop 的池，正常进程
里不会变；万一变了（loop 关闭重建），旧 client 的调用会失败 → 被 repository
吞掉 → 守卫退回纯内存工作。选择不重建 guard，是因为重建会丢掉内存里的滑窗和
冷却——用一个降级换另一个更糟的降级不划算。

**闸门排在业务 hook 之前**：已经隔离掉的对话不该每条消息还去做一次授权
往返。

**`is_agent_peer` 也要在这里盖章**：原生路径在 `build_trigger_extra_data` 里
写进 `channel_tag`，托管路径不跑 context builder，不盖的话
[[step_3_agent_loop.py]] 会把每一个托管 A2A DM 都读成人类对话，兜底回复在
最容易成环的那个面上保持武装。这行用 try/except 包住并降级为 False——理由
和 `_stamp_turn_envelope` 包住 `managed_reply_kwargs` 完全一样：一个坏到答不出
「对面是不是机器」的 trigger，代价应该是这一轮少一层收紧，不是整个渠道塌掉。
（`_Boom` 那个测试双第一次跑就抓到了这个漏包。）

## 2026-08-10 — 托管入站生命周期落审计(batch-2 §B;review 后直写重构)

coordinator **直接持 `ChannelTriggerAuditRepository` 写**(`_audit`
never-raise 助手),不经 trigger seam——两条 deny 路径(trigger 加载
不上、gate 崩溃 fail-closed)恰恰在 trigger 不可用时触发,"依赖坏件
才能记坏件"的机制记不了。**全部 deny 路径落 `managed_ingress_denied`**
(含基础设施两条:整渠道级故障若无痕会被二分法误判成"平台没发",
方向正好指反);silent → `managed_ingress_silent`;convert_attachments
→ `managed_attachments_converted`(declared vs converted;**workspace
解析失败的 early-return 也落行**,error="workspace_resolution: …"——
那是"全部附件丢失"、最需要覆盖的一格)。`_wire_message_id` 统一
message 身份推导(ParsedMessage 与审计行共用,不漂移);审计行带
chat_id/sender_id(取自 channel_tag)。after_run 增加 `audit_details`
透传。设计取舍:复用 `channel_trigger_audit` 表而非新建(todo §B
原案)——现表已有 JSON details/保留期/查询助手,平行审计系统违背
铁律 #8。

## 2026-08-10 — before_run 补 #254 turn envelope

`_stamp_turn_envelope`:native turn 的信封由 context builder 产出
(经 `build_trigger_extra_data`),managed turn 没有 builder ——
不补的话 step_3 把每个 managed 1:1 DM 读成 group room,无回复兜底
在整个托管面是死代码(与 #254 修的原生缺陷同类)。room_type 从
wire `chat_type` 推(仅 "group" 算群;DM 是 "private" 或缺省);
reply kwargs 走 trigger 的 `managed_reply_kwargs` seam,无 trigger
→ 不写(step_3 按 room_id 单独投递)。best-effort,永不破 turn。

## 2026-08-04 — silent_ingest 只调缝(review)

不再触碰 trigger 私有方法;契约知识(extra 字典 → Attachment 对象)留
协调器,编排知识归 trigger 的 `managed_silent_ingest`。

## 2026-08-28（接线 review）— 三处收口

**保留期清扫是协调器自己的事，但天数不是。** 窗口读的是
`trigger.INGRESS_BREAKER_RETENTION_DAYS`，与 `_guard()` 通过
`trigger.build_ingress_guard(db)` 拿那七个阈值同一条读法。第一版读了基类值
——那会把「保留期是 per-trigger」这条刚立下的语义在托管面又拆一次（原生按 90
天留、托管按 30 天删，同一张表同一个 channel 两个口径），而配套测试收了
`days` 却没断言它，所以这个变异当时是绿的。

清扫排在 **guard 判定之后**：熔断器关掉的渠道不会写这张表，为它每进程日扫一次
必然为空的查询没有意义，而那次查询是在用户消息的路径上同步做的。节流的截止
时刻设在尝试**之前**——DB 挂了不该每条消息重打一次。

**保留期清扫是协调器自己的事。** 基类那条按 `channel_name` 作用域扫，而协调器
没有周期循环——托管行原本能被清掉纯粹是因为那条清扫当时是全局的。加作用域之后
纯托管部署里它们会永久堆积，所以清扫改为**骑在 ingress 路径上、按进程日节流**
（与 `manyfold/files.py` 的审计保留期同形）。

**`is_agent_peer` 一次求值。** 此前盖章处和闸门各求一次，且降级方式不同：盖章
处抛异常记为 human（少一层收紧），闸门处抛异常被外层 `except` 吞掉 →
**整个熔断器对这条消息失效**。现在算一次传进去，降级只有一种。

**`build_ingress_guard` 去掉下划线。** 它是跨组件契约（协调器靠它拿到与原生面
同一套阈值），却带着私有名——下一个做「清理私有方法」的人会正确地认为这是层次
违规而改掉它，而改掉的结果是托管面失去熔断器。
# managed_channel_ingress.py — 托管模式的 trigger 执行体宿主

## 为什么存在

Manyfold 托管模式下平台持有连接与清洗流水线,IM 消息以 chat turn 形式
经 `/v1/chat/completions` 转发进来;但原生 channel trigger 的
per-message **业务钩子**(wechat 首聊认主、narramessenger authorize
门、inbox 写入、错误兜底直发)都长在平台接管走的那条收消息路径上,
prompt 级映射够不着。本文件是这些钩子在托管模式下的宿主:按
CHANNEL_TRIGGER_MAP 惰性构造 trigger 实例(**绝不调 start()**——无订阅
循环、无连接),在 openai_compat 的 run 前后路由
`managed_before_run` / `managed_after_run`。

## 上下游

上游:`backend/routes/openai_compat.py`(gate 在 BackgroundRun 构造前,
deny 直接回执;after_run 在流收尾 finally 里 fire-and-forget + done
callback)。下游:`channel_trigger_base` 的 managed 缝(默认实现 +
wechat/matrix 覆写)。它是 `run_channel_triggers` 的对等物(trigger
注册表上的协调器),不是 Module——铁律 #3 不受影响。

## 2026-08-03(补2) — `silent_ingest`:非 @ 群消息的记忆摄取

`is_mention=false` + `chat_type=group` 的托管 turn 不跑回复 run(会闯进
群闲聊),改调 trigger 的原生
`_build_and_run_agent_silent_batch`(单条成批):narrative 路由 + 记忆
写入照跑、LLM step 跳过、绝不向房间发任何东西;openai_compat 以回执
completion 应答。never-raise,失败降级为 dropped 回执。合批(平台侧
collect / 我方批量语义)按 spec Q8 留待实现期与平台共定——单条成批
先保证语义正确。

## 2026-08-03(补) — `convert_attachments`:平台落盘附件并轨原生协议

平台 ingest 已把附件写进 workspace(`chat-attachments/...`)并在契约里
传 `{name,mime,size,path}`;但原生 marker 经 upload store 的 per-day
索引解析路径,平台落点不在索引里 → 必须把每个文件**重新经
`persist_attachment_bytes` 入库**(= 原生 fetch_attachments,"下载"换
成本地读),拿到 file_id/marker/STT。转换后写
`extra_data["attachments"]`,原始键必被消费(未转换的平台字典绝不能
流进 marker 管线)。全程 never-raise:坏 ref 单文件降级 text-only。
路径逃逸守卫与 files.py 的 `_safe_resolve` 同语义。

## 关键决策 / Gotcha

- **失败语义按钩子性质分叉**:副作用渠道 fail-open(构造失败/钩子异常
  → 放行,下游自然暴露 no_credential),narramessenger fail-closed
  (它的钩子就是授权门;类缺失/异常 → deny)。
- `synthesize_managed_message` 只重建业务钩子需要的最小 ParsedMessage;
  wechat 回复路由读 `raw["context_token"]`(wire 上是 reply_token)。
- 单例 + per-channel 实例缓存;构造失败按渠道隔离(与
  run_channel_triggers 同款防御姿态)。

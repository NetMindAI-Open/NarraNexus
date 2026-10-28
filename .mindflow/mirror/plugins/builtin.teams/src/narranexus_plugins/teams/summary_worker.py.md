---
code_file: plugins/builtin.teams/src/narranexus_plugins/teams/summary_worker.py
last_verified: 2026-09-11
stub: false
---

# team_summary_worker — 自动维护每个团队的进展总结

## 为什么存在

公告栏的第三个来源（前两个是用户和 agent）：「这个团队现在到哪一步了」，
好让加入长任务的成员不必从 20 条 scrollback 里自己拼。

形状照抄 [[memory_consolidation_worker]] —— 仓库里唯一跑通的「后台 LLM 产出」模式：
轮询循环、逐项隔离、失败绝不外溢。LLM 调用走 `get_helper_sdk()`（铁律 #9，不绑定单一
provider/框架），走既有成本上下文（铁律 #14：纯机会性工作，从不打断或延迟任何 turn）。

## 为什么不让 lead agent 去总结

那会占用用户配置的 agent slot、产生用户没要求的 turn，并且总结进入**那个** agent 的历史
而非其他人的——正是公告栏要消除的不对称。总结属于团队，所以由平台写。

## 失败必须保留旧总结

它的产出落在每个成员的每一轮 prompt 里，所以一次糟糕的总结不是表里一行坏数据，
而是**一段机器猜测被前置到团队之后每一次回复上，直到被替换**。因此：

- 总结失败 → 保留上一版。清空会把一次 provider 抖动变成「团队唯一的共享进展视图丢失」，
  而且空总结在下一个读者眼里不是「未知」，是「这个团队毫无进展」。
- 模型返回空白 → 同理不写。
- 一个房间总结不出来，绝不拖住其他房间。
- 房间没动过 → 零次 LLM 调用。否则 worker 会每分钟花用户的钱重写同一段话。

## 上限是截断，不是拒绝 —— 与用户条目相反，且是故意的

用户的规则永不被悄悄截短，因为他们会继续以为整条都生效；而**没有人依赖一段生成文字的
确切措辞**，把超长的总结整条拒掉只会让团队完全没有进展视图。

## prompt 要 STATE，不要 instructions

总结紧挨着团队真正的规则展示。一句写成命令式的话会被当命令执行。

## 构建期两次设计修正（都由测试逼出来）

1. **触发原本用 `bus_messages.id` 高水位** —— 那列不存在。`message_id` 是随机字符串，
   表唯一的排序是 `created_at`，也正是它索引覆盖的列。现在用时间戳水位，
   代价是**同秒并列可能少算若干条**：对「是否已发生 15 件事」这种阈值可以接受，
   对「绝不能漏行」的用途不行。
2. **水位原本塞在总结行的 `author_id`**（总结的该列本来是 NULL）。那正是本功能自己的
   schema 测试批评的「一列两义」。现在是专用可空列 `watermark_at`，并有测试钉住。

相关：[[team_bulletin_repository]]、[[team_schema]]、[[main]]（挂载与关停顺序）

## 2026-08-11 (review 修正) — 后台任务的凭据与成本上下文

两个**只在生产上才发作**的错，测试全绿也拦不住，因为上面每条测试都把 `_summarise` 整个替换掉了。
而这个「整体 stub」当初还被我写进 docstring 当优点（「围绕这次调用的规则比调用本身更重要」）——
那句话错了两次：规则确实重要，但**调用本身必须真的能跑**，而我把一次从未执行过的函数调用
留在了未测面里。

1. `set_cost_context(agent_id="", user_id="", label=...)` —— 真实签名是
   `set_cost_context(agent_id, db)`，没有 `user_id`、没有 `label`、`db` 必填。每次真实总结必抛
   `TypeError`，被 `run_once` 的逐 team `except` 吞成一条 warning：**进程活着、循环活着、
   一条总结也写不出来**，代价是每 60 秒每个活跃 team 一条日志。
2. **没有注入 owner 凭据。** 这个 worker 跑在 lifespan 里、不在任何 HTTP 请求上下文中，
   auth_middleware 的 ContextVar 注入根本不发生，helper 调用会穿透 `_ConfigProxy`
   落到平台全局 key —— 正是 2026-07 那次事故（过期的平台 key 让每一次后台 helper 调用 401 了
   约两周，长期记忆静默降级）。[[memory_consolidation_worker]] 在 2026-06-11 P0 补过这一步，
   我抄了它的循环形状，**唯独漏了这一步**。

新增 `_inject_team_credentials`：按 **team owner** 解析（总结是平台替团队做的事，不是某个 agent
的差事；随便挑一个成员会平白借用它的模型覆盖），并且**先 clear**——`run_once` 在同一个 task 里
顺序遍历多租户，不先清就意味着 owner 解析失败的团队会继承上一个团队的凭据，**那是跨租户泄漏，
不只是配置过期**。

三条新测试跑的是**没被 stub 的** `_summarise`，只假掉 SDK；三个修复各自做过变异验证
（改回原样立刻变红）。

## 2026-08-11 (review 第二轮) — 成本主体、系统消息过滤、L2

**修完 🔴1 之后我又造了一个更隐蔽的**：`set_cost_context("", db)` 签名合法了，但每个 helper SDK
在 `agent_id` 为空时 `if not _agent_id or not _db: return` **直接丢弃整条记录**——而且这条 return
在 `warn_missing_usage` 之前，所以连那条专为「静默漏记」设的 L2 告警都不会响。docstring 却写着
「已归属」。**留着一个必然被守卫丢掉的调用，比不写更糟**：下一个读者会以为账已经记上了。

现在由团队的**默认应答者**（lead，否则最早加入的成员）承担。这是一次轻微的错误归属——那个 agent
并没有要求做这次总结——但它是两个可选错误里更好的那个：另一个是 owner 的 token 消耗上出现一个
看不见的洞，而这个仓库自己把静默漏记称作它最大的记账缺口。无成员的团队没有承担者，也没有可总结的东西。

**系统消息被排除在触发计数和总结素材之外。** 本功能自己写进房间的 `system_bulletin` 通知否则会
把一个安静的团队推过阈值——**由上一次总结的公告触发下一次总结**，平台自己触发自己。

**`run_once` 现在留下一条 L2 pass 记录**（rooms / summarised / failed）。此前只有逐次失败的
warning，于是「每个房间都很安静」和「每个房间都在失败」是同一个观测：沉默。正是这条信号本该
暴露上一轮那两个只在生产发作的错——worker 永远返回 0，看起来却很健康。

`clear_user_config()` 补进 `finally`，与 `clear_cost_context()` 对称；clear-first 序列改为委托
`resolver.inject_user_helper_credentials`，因为它是**跨租户不变量**，两份实现意味着将来有人只改一处。

## 2026-08-11 (review 第三轮) — 过滤器改为 import 常量

`_SYSTEM_MSG_TYPES` 原本硬编码两个字符串字面量，于是 #259 新增的第三种类型
（`patrol`）没有任何东西告诉它——「平台自己触发自己」这个本来关掉的门，**从另一侧敞开了**：
巡查恰恰只在流程卡住的房间说话（30 分钟 6 条上限），所以一个**完全没有真实工作发生**的
停滞房间，光靠平台自己的催办行就能在一个多小时内凑够 15 条阈值，触发一次真实 LLM 调用；
而且它们的 `from_agent` 是合成标记 `team_<id>`，不过 `member_map`，喂进 summariser 会被
读成某个成员在发言。

现在三个类型都从各自的定义处 import（`PATROL_MSG_TYPE` / `BULLETIN_NOTICE_MSG_TYPE` /
`STOP_NOTICE_MSG_TYPE`），SQL 占位符按元组长度生成——否则下次加类型要同步改三处 SQL 字符串，
而这次的教训正是「有一处没人改」。

## 2026-08-11 (review 收口) — bearer 委托既有规则，空 bearer 路径关闭

`_cost_bearer` 原本是 `_resolve_default_responder` 的**第二份手写实现**外加自己的
`team_members` 裸 SQL。规则已移入 [[team_schema]]（`patrol_is_on` 一个 release 前立的先例），
两个调用方——「没人 @ 时谁回答」和「总结的 token 记在谁头上」——共用它，因为**它们问的是同一个问题**。

空 bearer 路径**此前只是不太可能，不是不可达**：闸门只看消息条数。docstring 说这种团队
「也没有可总结的东西」，那是一句关于世界的断言，不是代码的保证——而它会在有人把最后一个成员
移出一个繁忙房间的那一刻失效。现在无成员直接跳过并记 debug：没有 bearer 就意味着
helper SDK 会丢弃整条成本记录，总结等于**烧了 owner 的 token 却哪里都没记下**。

跳过不计入 `failed`——它不是失败。

## 2026-08-11 (review 收口 2) — 不再自定义房间前缀

改为从 [[team_schema]] import。上一轮的注释宣称它已经统一了，实际没有。

## 2026-08-11 (review 收口 3) — `bearer` 必填，import 提到模块级

`_summarise` 的 `bearer` 去掉默认值：空 bearer 闸门的全部意义就是它不该是空的，
留一个 `= ""` 等于把闸门要挡的状态设成默认。`_cost_bearer` 的函数内 import 也提到了模块级。

## 2026-09-04 · builtin.teams as a feature-level plugin (batch 3c.2)

Started/stopped through the `WORKERS` contribution (`WorkerSpec(host="backend")`) of `builtin.teams`, not by `backend/main.py` directly. `_BackendHandle` adapts the start()/stop() pair to the run-until-stopped handle shape the backend worker host expects, so the worker itself stays untouched.

Batch 6b.3: moved from `platform/services/team_summary_worker.py` into the builtin.teams package.

## 2026-09-07 (prod 事故) — 失败的团队按指数退避，不再每分钟重打

prod 上两个 team 的 owner 自带 NetMind key 余额为零，worker 对它们**每 60 秒各重试一次**，
连续三天每小时约 1,000 行同一个 400，把 backend 日志淹了。「失败保留旧总结」是对**产出**的
正确策略，但对**调用**没说任何话：在进程之外的东西（充值、换 key）改变之前这个调用不可能成功，
每分钟问一次只是在埋日志。

现在每个 team 失败后进入退避：等待 `BACKOFF_BASE`（300s）按连续失败次数翻倍，封顶
`BACKOFF_MAX`（6 小时）。算式用共享的 `utils/backoff.compute_cooldown_seconds`（不再手写第 4 份副本）；
两个常量是 int，所以连击计数不设上限也不会溢出（任意精度整数），`_BACKOFF_CAP_STEPS` 随之删除。

`_summarise_team` 返回 `_Attempt(outcome, newest)`，outcome 三态：`WRITTEN`（写入 → 清零）、`NOT_DUE`（没到阈值 / 空 transcript /
无成员，**没打模型** → 清零）、`EMPTY_REPLY`（**打了模型但回空**）。空回复是 `_INSTRUCTIONS` 设计内的正常输出，
既不能写入（会盖掉旧总结），也推不动水位（从未总结过的团队没有那一行）。

**两种等待，退避表条目是 `_Wait`（review r2 I1）**：
- 失败等待：`next_at` + `failures`，按时钟指数退避（上面那条曲线）。
- 空回复等待：`shown_newest` = 模型看到的最新消息时间戳，**按内容不按时间**——`_in_backoff` 对它做一次
  `_new_message_count(channel, shown_newest)`，房间比模型看过的再多出 `MESSAGE_THRESHOLD` 条非系统消息才重问，
  时间推进多久都不重问（输入没变答案也不会变）。旧版让空回复共用失败的计时曲线：闲聊 15 条的房间半天爬到 6h 档，
  之后团队真干活公告板也要等 6 小时。
- 空回复说明调用本身成功，会替换掉失败条目（连击清零）；空回复之后再失败从第 1 次（BASE）算起，有测试钉住。
- 内容门要读库，读挂了该团队这轮算 waiting、条目不动，不拖累其他团队。
空回复不计入 `failed`、也不告警，只打一行 info。安静房间（阈值挡住）仍然永不退避，变忙的瞬间就会被尝试，有测试钉住。

**失败必须有出口。** except 分支在 `_note_failure` 之后调 `_report_failure` → `alert_background_llm_failure`
（source=`team_summary`，agent_id=`_cost_bearer`，owner=团队 owner，source_id=team_id）：每次真实尝试失败都落一行
`service_audit`；凭据类或余额耗尽类（共享分类器 `OUT_OF_CREDIT_REASONS`）再发 owner 收件箱通知，按
`owner_notice_cooldowns` 30 分钟去重（凭据类按 `(agent, team)`，余额类按 owner 收敛，见 [[background_llm_alerts]]）。
`_summarise_team` 已解析的 bearer 记在 `_attempt_bearer`，`_report_failure` 直接复用，只有在解析前就失败时才再查（review r2 M7）；
owner 仍查一次 `teams`（只在失败路径、且受退避约束）。只在真实尝试上调——在退避中被跳过的团队不告警，
否则就是把日志洪水换成审计洪水。owner/bearer 查询和告警本身各自包 try：故障中的 DB 会让查询抛，而这里在 except 块里，
冲出去会拖死同一 pass 后面的团队。

**退避只放内存，不加列。** 重启就是再试一次的好理由；持久痕迹由上面的 `service_audit` 承担。
`_clock` 是测试可替换的接缝（默认 `time.monotonic`），测试用假时钟推进而不是 sleep。已删除的团队条目在每个 pass 末尾清掉。
与 [[memory_consolidation_worker]] 的分野：那边把持续失败的 scope 置 `failed` 直到重新变 dirty（边沿触发）；这边失败是定时等待（失败的原因在进程外，没有边沿可等），空回复则是内容边沿（见上）。

`last_pass` 多一个 `backoff` 计数（被跳过的等待中团队数），进 `[team.summary] pass:` 日志行；该行的门是
`failed or summarised or backoff`（review r2 I4）——「只剩等待中团队」这个稳态也会打出，空部署三者皆 0 仍静默。
（worker 自插件化起跑在 workers 进程，backend `/health` 已不再有 `team_summary` 块。）

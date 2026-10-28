---
code_file: src/narranexus/platform/channel/ingress_guard.py
stub: false
last_verified: 2026-08-28
---

## 2026-08-27（第一轮 review）— tier 衰减补上「时间」这一半

`_maybe_recover` 只从 `admit()` 里跑，所以它只在会话**继续说话**时降级。
跳到 tier 3 之后就沉默的会话，tier 永远停在 3——没有任何轮询，重启后
`_load` 原样读回。这正是 `_maybe_recover` 自己 docstring 说要防的那件事
（「一年前抖了一分钟的会话永远背着一个能升到 24 小时的 tier」），却只实现了
「一直在说话」那一半。

同一个缺陷还锁死了这张表：`cleanup_older_than_days` **刻意只扫 `tier = 0`**
（带着升级记忆的行正是我们答应要记住的），所以卡在 tier > 0 的行永远无法回收。
`warm_start` 的 `cooling_only` 优化是在绕开这个后果，不是在治它（铁律 #5）。

`_load` 现在按沉默时长**追补衰减**：`steps = 沉默 // 衰减步长`，一次补齐而不是
一次一级——半年前的行不该需要半年的重启才能回零。归零时写回，行随即进入
清扫面，两个问题一次解决。

**锚点从「隔离结束」起算，不是从跳闸起算。** 第一版取 `tier_changed_at` 本身，
而冷却是从跳闸那一刻开始跑的——于是整段服刑时间被当成良好表现计费。按默认值
（步长 1200s，schedule 300/1800/7200/86400）：tier 2 冷却到期时已白送 1 步、
tier 3 送 6 步、tier 4 送 72 步。也就是**任何一次部署之后，处在高档位的会话
下一条消息就把 tier 写回 0**，持续复读的对端从 300s 重新爬——正是
`_half_open` docstring 点名要防的「a persistent loop oscillates at the
cheapest tier forever」。那条既有测试当时是绿的，只因为它测的是 tier 1：四档
里唯一冷却短于一个步长的一档。

这也与「继续说话」那一半对齐：`_trip` 清掉 `clean_since`，`_half_open` 在冷却
到期那一刻才把它设成 `now`，所以说话那条路从来不给冷却期记账。两半现在同一
个语义。

**「冷却中不衰减」不需要第二道判据**：锚点落在冷却结束点之后，未到期的冷却
必然把锚点推到未来，步数只能算出 0。第一版另写了一句 `if cooling: return`，
它恒真、且读起来像一道独立判据——同一个形状在 PR-4 那轮已经被判过一次，删掉。

**锚点是新列 `tier_changed_at`，两个看起来能用的邻居都不行**：
- `last_tripped_at` 在 `_maybe_recover` 降级时**不变**，用它会把已经付过的
  沉默再算一遍，衰减过头——而衰减过头等于给再犯者更便宜的冷却，正是 tier
  要阻止的事；
- `updated_at` 由仓储用**墙钟**盖章，而这个状态机跑在调用方传入的 `now` 上，
  两者在任何非墙钟的调用者那里都对不上。

边界由测试钉着：冷却期内**绝不**衰减（tier 3 的 7200s 冷却比 1200s 步长长，
存在「既在隔离中又已过一步」的时间窗，不落在这个窗口里就测不到）；冷却刚过
那一侧**也不**衰减（镜像用例）；追补按步数成比例；recover 也刷新锚点。

**衰减同时挂在 `prune_idle` 上（内存侧）。** 只挂 `_load` 不够：tier > 0 的
会话被 `prune_idle` 刻意永久保留，因此永远不会再走一次 `_load`，而
`_maybe_recover` 只对继续说话的会话生效。结果是跳闸后彻底沉默的会话在**本
进程生命周期内**既不降级、其行也不可清扫，`open_session_count()` 会从「现在
有多少被隔离」漂成「自上次部署以来一共有多少跳过闸」——那是本设计针对 8/14
盲区给出的 L2 指标，在按铁律 #14 设计成跑数天的进程里这个漂移是致命的，也
正是 `warm_start` 花力气避免的失真从另一头长回来。

`prune_idle` 是**同步**方法且 `admit()` 不 await 它，所以内存侧只改内存；落库
修正由该 key 下次说话时的 `_load` 完成——**而这句话成立有两个前提，两个都是
后来才补上的**：(a) 该会话得真的会被丢弃，sweep 的判据一度写成「从来没有过
冷却」，于是跳闸过的会话被永久钉在内存里、`loaded` 恒为 True、`_load` 再也不
跑；(b) 行不能被改成「半新半旧」，而探测一度只写新锚点不写 tier。两条路径给出
同一个答案，靠的是
**算术只有一份**（`_steps_of_silence`）——第一版是两份拷贝，一份读行、一份读
内存，而两边的 `cooldown_until` 根本不是同一个事实，于是「较晚者」这句话在两
份实现里落到了两个不同的值上。规则收进一处之后，「两边一致」才是代码的性质
而不是注释的承诺。

**锚点必须在隔离终点被销毁之前就吸收它。** `cooldown_until` 是"服刑何时结束"
的唯一记录，而它在两个地方会消失：`_half_open` 探测时内存与落库同时清空它；
`_load` 只把**仍在未来**的冷却写进 `state.cooldown_until`。任何一处漏了折叠，
锚点就退回跳闸时刻、整段冷却重新被算成沉默——tier 3 就是 6 步，于是：

- **探测之后**（不需要重启）：那条被刻意保留 tier 的探测，会被紧随其后的任意
  一次 sweep 清零。对端只要「风暴 → 熬过冷却 → 发一条新内容 → 继续风暴」就能
  永远停在最便宜的 300s 档，正是 `_half_open` docstring 点名要防的那件事。
- **重启懒加载**：`_decay_for_silence` 用行里的冷却算对了、保住了 tier，几十
  毫秒后同进程的第一次 sweep 又把它推翻。

**折叠**（把隔离终点吸收进锚点）发生在**三处**，共用一份定义
`_fold_isolation_end`：`_load` 读行时折、`_half_open` 清空冷却前折、
`_steps_of_silence` 计算时折。这条规则在三轮 review 里被改了三次，每次都是
因为它有多份——第二次合并掉两份之后，第三次又为了 `_load` 复制出第三份，且
三份的空值处理还不一致。现在是一处定义、三个 caller。

**写** `state.tier_changed_at` 的点比折叠点多，分三类：

- **变更 tier 时写**：`_trip` / `_maybe_recover` / `_decay_for_silence` /
  `_decay_silent_in_memory`。
- **不变更 tier、只搬运隔离终点**：`_half_open`。它**刻意保留 tier**（探测的
  全部意义就是「不降级地放行一条」），只是在销毁 `cooldown_until` 之前把终点
  折进锚点——理由见下一段。
- **从行里读进来**：`_load` / `warm_start`。

`warm_start` **写但不折**——它以
`cooling_only=True` 加载，行的冷却必然在未来、必然落进 `state.cooldown_until`，
共用函数会自己折；多写一次就是同一条规则的第四份拷贝，且没有任何测试能把两者
区分开（试过，变异杀不掉）。

`_half_open` 的落库 payload 里 **tier 与锚点必须同行**，和 `_trip` /
`_maybe_recover` 一样。它们是同一个事实的两半（「tier 是几」「上次何时动」），
而内存 sweep 是唯一一条移动 tier 却不落库的路径——它的正当性完全建立在「行仍
是 tier N、锚点未动，下次 `_load` 算出同样的结果」上。只写新锚点会把这个前提
拆掉：reload 拿到旧 tier 配新锚点，已经服完的刑期重新长回来。

取冷却**终点**而不是 `now`，是为了让内存与落库两条路算出同一个数，也为了不
抹掉「冷却结束到探测到来」之间那段真沉默。这一层要观察得到，探测必须发生在
冷却终点后**至少一个衰减步**——差 50 秒的取样点下两种写法都给 0 步，测不出来。

**除零守卫**：`_decay_step_seconds = window × recovery_windows`，而 `window`
没有下界。这是整个类里唯一一处会做除法的地方，其余全部「宁可退化也不抛」；
异常逃出 `admit()` 会把 fail-open 变成 fail-closed，整条渠道消息全丢——铁律
#16 禁止的用户可感知内容丢失，而 `window_seconds` 恰是最像会被做成
per-channel 配置的那个参数。

## 2026-08-28 — 接线已落地，本文件开始真正生效

四个挂载点、三个 audit 事件、health 与心跳计数、`warm_start`、保留期清扫都已
接上（见 [[channel_trigger_base.py]]）。下面「上下游」一节描述的形态现在**就是
仓库的形态**。

## 2026-08-27 — 本文件合入时是**死代码**，这是刻意的（已由接线 PR 解除）

拆分方案把熔断器分成两个 PR：本文件（状态机 + 表 + repository + 验收回放）
先合，**没有任何调用点**；四个挂载点、audit 事件、`/healthz` 计数随接线那个
PR 落地，那一刻熔断器才真正生效。

这样拆是因为 PR#358 的三轮 review 数据很清楚：**内核只在第一轮出过一个问题
（会话键漏 `agent_id`），之后两轮零 finding**，不收敛的是体量和耦合。分开之
后 reviewer 只需要判断「这个算法对不对」，不必同时判断「接线对不对」。

**所以下面「上下游」一节描述的是接线后的形态，不是当前仓库的形态。** 今天
`grep -rn "IngressGuard" src/` 只会命中本文件自己和测试。同理，
`channel_ingress_breaker` 表的保留期清扫**尚未挂上**——没有写入方就无所谓
清扫，`schema_registry.py` 里那段注释明确写着 NOT swept yet，接线时同 commit
改成事实。

## 2026-08-25（第三轮 review）— suppressed 播种 + 通知层拆出

**`suppressed` 不再从 `suppressed_count` 播种。** M9 把这一列的语义改成
「**上一轮**隔离吸收了多少」，但 `_load()` / `warm_start()` 仍拿它给
**下一轮**的内存计数器播种——于是这个数会在懒加载/重启后虚高，并跨轮累加。
重载后我们其实并不知道之前丢了多少，诚实的做法是从 0 数起；那一列继续为
SQL 保留已完成隔离的数字。

**owner 通知层已整个拆出**（见 [[background_llm_alerts.py]]）。熔断器本体、
四个挂载点、三个 audit 事件、`/healthz` 计数全部保留——止血能力不受影响，
少的是「事中推送」。

## 2026-08-25（第二轮 review）— 修复自己制造的债

上一轮的修复 commit 自己引入了这一批。记下来是因为它们**重复了上一轮刚
教过的同一类错误**：把断言性的散文和代码同时写下，写的是打算做成的样子，
然后从不回头验证。

**`forget_agent()` 又一次没有调用方。** 上一轮发现 `forget()` 零调用、据此
加了 `prune_idle`；这一轮把它改名 `forget_agent` 并把 docstring 写得更肯定
（「Called when an agent's subscriber stops」）——**依然零调用方**。

内核 PR 合入时它仍然零调用方，docstring 写的是未来时。**接线 PR 已经把它接到
[[channel_trigger_base.py]] 的 `_stop_subscriber` 上**，并由
`test_ingress_guard_all_paths.py` 加了一条守卫：guard 的生命周期方法必须真的
有调用方。docstring 也已从未来时改回陈述句。

死代码带着一句「Called when …」比没有这个方法更糟——下一个人会以为解绑路径
已经清理过了。这一段本身就是在记录这个教训，而它的第一版**用同样的方式又犯
了一次**：把接线写成了已完成。

**`warm_start` 的量级论证是反的。** docstring 原话「closed ones are swept
by retention」，而 retention **只**扫 `tier = 0`，warm_start **只**加载
`tier > 0`——两者永不相交。跳闸一次后再不说话的会话，tier 降不下来（衰减
需要它继续 `admit()`），行永远不被删、每次启动被拉回内存、`prune_idle`
因 tier>0 不驱逐。结果是把一张只增不删的表整个搬进内存，而且
`open_session_count()` 会随部署次数单调虚高——I4 要修的是「重启后谎报 0」，
修完变成「长期虚高」，同样答不了「现在有多少会话被隔离」。

改成只加载 `cooling_only=True`。**升级记忆不需要预热**——`_load()` 在该
会话再次说话时懒加载，那正是它起作用的唯一时刻；预热买来的只有观测面，
而观测面从来只指「当前被隔离」。

**`suppressed_count` 第三次没落对。** 预审时修了「两个写入点都写 0」，
但这轮新增的重跳路径走 `_trip`，而 `_trip` 那处仍写 0——于是唯一真正会攒下
`suppressed` 的路径（持久循环）落库还是 0。现在 `_trip` 落
`suppressed_before`（内存里为下一轮隔离清零不变）。

**键的分量加了 128 钳制**：448 原本只是注释里的算术，`chat_id` /
`sender_id` 来自平台侧没有长度契约。见
[[channel_ingress_breaker_schema.py]]。


## 2026-08-28（接线 review）— 事件名归 verdict 所有

`audit_event()` 与 `audit_details()` 并列。**写**必须两份（原生走 `_audit`，
托管走协调器的直写 seam，变异证明了删掉任一份另一份都不会红），但「哪个
transition 对应哪个事件」是**知识**，不是管道——它此前在两个调用方各抄了一份。

设计里 L0 观察层与 L1 合并层还没落地，`transition` 一定会长出新值。抄两份的
后果不是「有一处忘了改」这么轻：新事件在原生面有、托管面没有，等于那条渠道
的这类事件在 DB 里**根本不存在**——而 DB 正是「机器人怎么安静了」唯一能查的
地方。

映射刻意**不吞掉「丢不丢」**：托管侧即使没写事件也仍要回 receipt，原生侧的
放行仍取 `verdict.admit`。合并成一个「返回事件顺便决定放行」的函数会把这层
耦合埋进去。

## 2026-08-28（接线 review 二轮）— transition 词表变成数据

`INGRESS_TRANSITIONS` 是 frozenset，四个值各有常量，状态机里不再写字面量。

把映射收成一份（`audit_event()`）只是把漂移点从「两个调用方各抄一份」挪到
「新值忘了加进映射」。而后者的失效方式更隐蔽：`audit_event()` 返回 `None`，
于是**两个面都不写行**——不是漏一面，是这类事件在 DB 里根本不存在。词表变成
数据之后，一条测试就能穷尽断言「每个成员都映射得到事件」，加值不映射当场红。

那条测试必须用 `admit=True`：`admit=False` 时「未放行」那条分支会替任何未映射
的值兜出一个 drop 事件，断言就永真了。
# ingress_guard.py — 「这条消息值不值得处理」

## 为什么存在

2026-08-14，Liam × AI Signal 在一个 NarraMessenger DM 里乒乓死循环
**70+ 小时、6.6 万条消息**，全程监控绿灯。

根因不是某一层写错了，而是**入站路径上没有任何一层问过「这条消息值不值得
处理」**。调研确认（2026-08-24）：IM 入站只有**消息身份去重**（id-keyed，
[[channel_dedup_store.py]]）和**突发合并**（[[channel_debounce_merger.py]]），
没有 per-sender / per-chat 频率限制，也没有跨消息重复检测。唯一的内容指纹层
（`ChannelDedupStore` layer 4）被 `CONTENT_DEDUP_WINDOW_SECONDS` 门控，**当时
所有渠道都是 0，整层是死的**。

于是每条消息无条件跑全套管线：narrative 检索/判官 → persona 更新 → agent
loop → 回复决策。当对面是一个损坏的外部 agent 在逐字复读时，每一层单独看
都在正确工作，合起来是永动机。

**修在 ingress 而不是回复层**：回复层有三个各自独立的耗钱面（agent 自主
回复、DM 兜底回复、后台管线），堵一个漏两个；ingress 是唯一一个卡住就全部
止血的位置。

## 与另外两个熔断器的关系

三个熔断器，三个不同的问题，谁也不能替代谁：

| 熔断器 | 问的问题 |
|---|---|
| [[channel_trigger_base.py]] 快速死亡熔断 | 我自己的凭据坏了吗 |
| `agent_framework/loop/circuit_breaker.py` | 我自己的 turn 一直失败吗 |
| **本文件** | **进来的消息值不值得处理** |

退避 + 冷却 + 半开探测的范式三者同款，这是刻意的——一个仓库里不该有三种
「怎么退避」的写法。

## 设计决策

### 为什么 P1 不做 L0 观察层，却仍然敢上硬熔断

设计文档的完整模型是 L0 观察 / L1 降频合并 / L2 短熔断 / L3 递增熔断。
P1 只落 L2/L3。L0 存在的意义是「先观察不误伤」，这里用**进入条件取合取**
来替代：窗口内必须**同时**满足频率超标**和**重复率超标。

内容各异的正常高频对话——用户连发六条想法、活跃群高峰、job 定时批量——
**永远不可能**满足重复率条件，因此结构性免疫。这不是把 L0 砍掉，是把它的
保护对象换了一种方式覆盖。L1 的合并处理确实推迟到 P2，那是**优化**（省钱），
不是**保护**（止血）。

### 重复率的定义：`1 - distinct/count`

30 条一模一样 → 0.967；30 条各不相同 → 0.0。

写测试时踩过一次：直觉以为「两条正文交替 20 次」的重复率是 0.5，实际是 0.9。
想清楚之后确认公式是对的——**两条台词的乒乓依然是乒乓**，不会因为有两句话
就变得无辜。0.5 那一档对应的是「每句说两遍」，那才是人类会做的事。

### 空指纹算「独一无二」，不算重复

没有正文的消息（无 caption 的文件上传）指纹为空。这类消息**每条都算 distinct**。
反过来会让「连续拖 30 个文件进来」读成逐字复读风暴，而
[[channel_trigger_base.py]] 的空内容闸门早就为无 caption 上传开过同一个口子
（`raw["attachment_refs"]`），这里必须一致。

### 状态分两层存：滑窗在内存，tier 落库

| 数据 | 存哪 | 为什么 |
|---|---|---|
| 滑窗计数 + 指纹环形缓冲 | 纯内存 | 10 分钟就过期的数据，每条入站消息写一行是纯写放大 |
| tier / cooldown_until | 落库，**只在层级变迁时写穿** | 事故跑了 70 小时，期间任何一次重启都会把已经隔离 24 小时的对端重新放行 |

这是本文件最重要的一个决定：**热路径零 DB 写**。每个 session key 在进程生命
周期内只读一次库（首次见到时懒加载），之后全走内存；变迁时写穿。
`test_ingress_breaker_persistence.py::test_only_transitions_are_written` 钉住
这条线——62 条入站消息只允许 1 次写。

注意这与 [[channel_trigger_base.py]] 的凭据熔断器**结论相反**：那个是刻意
纯内存的（它描述的是**活着的** subscriber 状态，停掉的 trigger 不该把隔离
带进下一次 start）。两者不矛盾，因为描述的东西寿命不同。

### 一个时钟，墙钟，可注入

凭据熔断器用 `time.monotonic()` 是对的（纯内存）。一旦要落库，冷却就**必须**
用墙钟表达，而一个状态机里跑两个时钟是「重启差一拍」类 bug 的温床。所以
全程 `utc_now()`，并允许 `now` 参数注入——测试里所有时间断言都是算术，
不睡、不打 fake clock（范式抄 `test_credential_breaker.py` 的 `_armed()`）。

### 冷却表是字面量，不是公式

5min → 30min → 2h → 24h，末位重复。刻意**不用**
`utils/backoff.py::compute_cooldown_seconds`——那个是 `base·2^(n-1)`，
任何底数都凑不出这四个数。这四个数来自设计文档。

### 半开探测保留 tier

冷却到期只放行**一条**探测消息，`tier` **不清零**。理由与
`_breaker_release` 完全相同：一个清完冷却立刻继续复读的会话必须落到
**下一档**，否则持久的循环会永远在最便宜的一档来回震荡。

### tier 会衰减

连续 N 个干净窗口降一级，最终清零。没有这条，一个一年前抖了一分钟的会话
会永远背着一个「能升到 24 小时」的 tier，一年后第一个坏分钟就要付一天。

### fail-open

守卫**不是**授权门。DB 读写失败、guard 自身抛异常，一律放行。对照组是
narramessenger 的 managed authorize hook——那个 fail-closed，因为它**是**
授权门。[[managed_channel_ingress.py]] 的 mirror 要求每个 managed gate 显式
选边，这里选的是 open。

## 上下游

**上游（四个挂载点，同一个 seam `_ingress_admitted`）**：

1. [[channel_trigger_base.py]] `_process_message` —— Slack / Telegram /
   Discord / WeChat + Matrix 的回复路径（这四家 override 了但都调 `super()`）
2. [[lark_trigger.py]] `_process_message` —— Lark **不调 `super()`**，独立挂
3. [[matrix_trigger.py]] 的 `group_silent` 分支 —— 在 `super()` **之前** return，
   但仍然跑记忆管线
4. [[managed_channel_ingress.py]] `before_run` —— Manyfold 托管路径完全绕开
   原生 chokepoint

**没有单一 chokepoint 是本次接线的核心事实**。调研一开始以为只有 Lark 是
例外，`test_ingress_guard_all_paths.py` 一跑就抓出
Telegram / WeChat / Matrix
也各自 override 了 `_process_message`（只是都调了 `super()`）。这类
「N 份手抄」正是 [[channel_trigger_base.py]] mirror 里
`build_trigger_extra_data` 那条教训的同一个缺陷类，答案也一样：
**一个 base seam + 一个 grep 级守卫测试**。

**下游**：`ChannelIngressBreakerRepository`（落库）、
[[channel_audit_events.py]] 的三个事件常量。
（owner 通知那一路已于 2026-08-25 拆出，见本页最新条目。）

## 2026-08-25 — PR#358 review 修的两个真问题

### 会话键漏了 `agent_id`（阻塞级）

**一个 trigger 实例服务全部凭据**：`_subscriber_creds` 里每个 agent 一份，
同一条房间事件扇出到每个成员 agent 的 client，`_process_message` 因此**每个
agent 各跑一次**。这不是边缘情况——[[channel_dedup_store.py]] 的 docstring
早就写明「a Matrix room event fanned out to every member agent's client and
must each be processed」，它自己的三层缓存全部按 agent 分区。

而第一版的会话键不含 `agent_id`，于是这 N 次 `admit()` 落到**同一个**
`_SessionState`：`count` 变成 N 倍真实条数，而指纹
`sha256(chat_id|sender_id|content)` 三个分量在 N 次调用里完全相同 → N 份
逐字重复。重复率变成 `1 − 1/N`，**与内容无关**，只由房间里我方 agent 的
数量决定。

实测（生产默认阈值）：

| 房间里我方 agent 数 | 对端发几条**各不相同**的消息就跳闸 |
|---|---|
| 1 | 不跳闸 ✅ |
| 2 | 10 条 |
| 3 | 7 条 |
| 5 | 4 条 |

跳闸后房间里**所有** agent 一起对该发送者失聪，冷却一路升到 24h。这直接
推翻了本设计「内容各异结构性免疫」的核心论证——免疫在单 agent 房间成立，
在多 agent 房间不成立，而多 agent 房间正是 A2A / team room 的一等场景。

修法：`agent_id` 进键，与 dedup 层同一个分区策略。列宽 320 → 448
（419 是四段加三个分隔符的实际需要）。**列宽这件事 SQLite 看不见**：TEXT
永不截断，本地全绿，只有 MySQL 侧才会因为长键截断让两个 agent 的行撞唯一
索引、互相覆盖。

### 「只放行一条探测」是假的（I1）

`_half_open` 放行探测的同时 `events.clear()`，而跳闸判据要求窗口内攒满
`rate_bar`。所以每个冷却周期实际放行的是 **1 条探测 + rate_bar − 1 条**，
每条都跑完整管线、每条回复又是对面的一条新入站消息。文档、docstring、
测试名三处都写「只放行一条」，代码做的是另一回事。

原来的两条测试恰好都绕开了这个分支：一条只发 1 条消息（断言不到第 2 条），
一条发满 12 条但只断言「最终升级了」、不断言中间放行了几条。

修法走**真半开**：跳闸时快照当时窗口里的指纹集合（`trip_fingerprints`，
有上界），探测消息命中就**当场重新跳闸**。比「放行一条再观察」更省：指纹
本身就是「复读还在继续」的证据，不需要再烧一次管线去确认。内容确实是新的
则清空记忆、恢复正常计数——冷却 24 小时后第一句真话不该被罚。

重启后 `trip_fingerprints` 为空（纯内存），那一次探测退回旧行为。可接受：
持久化那一半守住的是冷却本身，那才是关键。

**修的过程中自己引入过一次回归**：重新跳闸路径上先把 `state.suppressed`
清零再调 `_trip`，导致上一轮冷却吸收的条数被丢掉，升级的 audit 行会声称
自己什么都没挡下。已修并加测试钉住。

### 顺带

`warm_start()`（见「坑」一节）和 `cooling_session_count()` 都补上了可注入
时钟——本文件其余部分早就是这个约定，这两处是漏网的。

## 预审补的两个洞（2026-08-25）

写完跑绿之后对着 diff 又审了一遍，抓到两个测试覆盖不到的问题——都不是错误
逻辑，是**长跑内存**，而铁律 #14 明确说长跑是一等场景。

**`_sessions` 无界增长。** 每见过一个会话键就留一个 `_SessionState`（各挂
一条 deque）永不释放。冒烟验证：5000 个只说过一句话的陌生人 → 5000 条状态
全留着、零个在熔断中。`forget()` 当时写了但**没有任何人调用**。

修法是 `prune_idle()` + `admit` 里每 1000 次摊还调用一次。**摊还而不是每条
清**：清扫是 O(sessions)，每条消息付一次就把热路径从 O(1) 变成 O(n)。放在
`admit` 里而不是挂 `_run_cleanup`，是因为托管路径根本没有自己的清扫 tick。

**只有真正什么都没带的会话可以丢**：tier=0、不在冷却、窗口内无事件。丢掉
一个闭合会话是无损的——它的 DB 行还在，下次这个键再说话时懒加载读回来
（`test_a_pruned_session_reloads_its_durable_state` 钉这一点）。带着 tier
或冷却的会话正是我们承诺要记住的东西，清扫不能变成提前遗忘的后门。

**`suppressed_count` 是条死列。** 内存计数器在冷却期间正常累加，但热路径
不写库（这是对的），而 `_trip` 和 `_half_open` 两个写入点**都写 0**——于是
这一列在库里永远是 0，schema 注释承诺的「本轮冷却挡下了多少条」从 SQL 根本
问不出来。冒烟验证：verdict 报告 52，库里写着 0。

修法：`_half_open` 写**真实吸收数**。那是唯一能写的时刻——探测发生时这个数
恰好完整，之后内存清零；`_trip` 继续写 0 是对的，新一轮隔离从零开始算。

顺带修的：`cooling_session_count()` 原本内部硬用 `utc_now()`，与本文件
「时钟可注入」的其余部分不一致，也导致它没法用合成时钟断言。改成收同样的
`now` 参数。

## 坑

- **`content_fingerprint` 是无条件的纯函数**，
  `ChannelTriggerBase._content_fingerprint` 保留 `CONTENT_DEDUP_WINDOW_SECONDS`
  门控后再委托给它。哈希口径必须只有一份，否则去重层和熔断层会对「同一条
  消息」产生两种定义；但那个门控管的是另一个问题（平台是否用新 id 重投），
  不能一起解开。
- **守卫插在 unbound / echo / empty 三道闸门之后**。回声是 agent 自己发的，
  空消息是解析不出来的——把它们计进对端频率，等于让 agent 自己熔断自己。
- **每次 drop 必须留 audit 行**（`ingress_dropped_breaker`，逐条写）。
  「机器人怎么六小时不说话了」必须能从 DB 回答；静默 return 正是让原事故
  跑了 70 小时没人发现的那类盲区。
- **`warm_start()` 必须在 `start()` 里跑**。两个观测面读的都是**内存**计数，
  重启后 `_sessions` 是空的——库里有 50 条 `tier > 0`、10 条还在 24h 冷却，
  `/healthz` 照样报 0。发布一次 = 看板重新变绿，同时一堆会话仍然是聋的，
  正好打掉本设计反复引用的事故教训 #4。顺带也修掉「重启后要等该会话再次
  说话，冷却才恢复」的窗口。
- **`open_session_count()` / `cooling_session_count()` 是常驻状态**，进
  `health_snapshot()` 和心跳。事故教训 #4：熔断不能只有 trip 那一行。

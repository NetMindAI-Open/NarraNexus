---
code_file: backend/routes/runs.py
last_verified: 2026-09-10
stub: false
---

## 2026-09-10（PR #394 第二轮 review I-4）— 活性规则单一导入路径

`STATE_RUNNING` 改为从 `narranexus.platform.utils.run_liveness` 导入（原来经 `run_recorder`
转手），行为不变。

## 2026-08-07 (四次) — 停止顺带把工作板停下(第 7 条落地)

`_pause_work_items(db, root)`:停一棵树之后,把该树 ACTIVE 的工作项置
`paused`。

不加这一步,停止会被**悄悄撤销**:树的 run 停了,但工作板仍然列着这个任务
未完成,于是 Leader 下一轮巡查看到「未完成项 + 负责人 idle」→ 催办 → 拉起
新 run。owner 按了停,眼看着新活冒出来 —— 与排队消息过滤要防的同一个打地鼠,
高一层,而且这次是**平台主动**发起的。

`paused` 而非 `cancelled`(Owner 拍板):停止意味着「别跑了」,不是「这事不做
了」。工作项留在板上,恢复是用户的显式动作。

## 2026-08-07 (三次) — 每个被停的 agent 各一条,不合并

二次改动收了整个 stopped 集合、按房间分组,但 `_post_stop_notice` 只用了
`agent_ids[0]`,正文又是固定英文句 —— 结果**只报出一个名字**,其余是死数据,
docstring 却写着 "naming every agent stopped there"。描述了一个没写出来的
行为(PR #252 review 连续两轮点名)。

改为**一个 agent 一条**。为什么不真去实现合并:渲染出来的名字来自消息自己的
发送者(transcript 把 `from_agent` 解析成显示名),这正是它能本地化的原因。
合并就得把其余名字塞进 `content` —— 一个前端只能去抠字符串的英文句子,因为
`bus_messages` 没有可放名单的结构化列。两三条灰色系统行比一次字符串抠取
便宜,而且每条都可归属。

路由级用例补齐(此前零覆盖):一个房间三个被停 agent → 三条可归属的系统行;
peer DM 不留痕;没有 activity 行的 chat run 不留痕。

# routes/runs.py — run 级控制面(目前只有:owner 的停止请求)

## 为什么是 run-scoped 而不是 team-scoped

run 可观察性早已是**平台属性**(#219:每个 trigger run 一份 recorder,
每个读侧走同一个观察端点)。停止是它的写侧孪生,所以以 `run_id` 为键,
任何能说出一个 run 的表面都能用它 —— 今天是 team roster,明天可能是
runs dashboard —— 而不是绑死在团队房间上。

## 只记录意图,不执行中断

run 活在另一个进程(workers),HTTP 请求进不去。真正的中断由那边的
[[cancel_watcher]] 读 `events.cancel_requested_at` 完成。

**记录与投递刻意分离**:点击能在一次往返内拿到答复,不管 workers 进程
当时多忙。这正是需求的核心 —— 2026-07-23 事故是 8 分钟的沉默,不是一次
缓慢的停止。前端据此立刻转「正在停止」,终态则从观察流里等。

## 设计决策

- **owner 校验走 `AgentRepository.resolve_owner(agent_id)`,绝不用
  `events.user_id`**。后者存的是 run 的**触发键**:团队房间里那是**发送者**
  (`usr_<uid>` 或转发的 agent_id)。信它就等于让房间里任何跟这个 agent
  说过话的人都能停别人的 agent。测试专门钉了这条
  (`test_triggering_user_is_not_the_owner`)。
- **403 与前端是否画按钮无关**。按钮的可见性只是提示,这里是边界。
- **已终态的 run 不落旗标**,只回 `already_settled=true`。旗标比请求活得久,
  而 watcher 的判据是 `requested >= started_at` —— 往终态行上盖旗标等于给
  这个 agent 的下一个 run 留了个活陷阱。
- **重复点击保留最初的时间戳**。第二次点击若改写时间戳,`requested >=
  started_at` 的裁决就能被一次(比如重启后落下的)迟到点击移动。

## 上下游

- **上游**:`TeamMemberPanel` 的停止按钮 → `api.cancelRun(runId)`
- **下游**:`events.cancel_requested_at` → [[cancel_watcher]](触发 token)
  与 [[run_recorder]] 的 `sweep_stale_runs`(决定终态写 cancelled 还是
  failed)
- 挂载:`backend/main.py`,prefix `/api/runs`

测试:`tests/backend/test_run_cancel_route.py`

## 2026-08-11 — `STOP_NOTICE_MSG_TYPE` 移到核心包

常量本身不变，定义处从这里移到 [[team_bulletin]]，本文件改为 import。
原因是又多了一个消费者：团队总结 worker 要把它排除在「团队活动」之外，
而核心包不能反向 import 路由。

## 2026-08-11 (review 收口 2) — 中段 import 提到文件顶部

上一轮为了让核心包能共用 `STOP_NOTICE_MSG_TYPE`，这里留下了一个带 `# noqa: E402` 的
中段 import。座位不用再动就能收拾干净：import 移到顶部，解释「为什么定义在核心包」的注释
留在原处，`# noqa` 删除。同时不再自定义 `TEAM_ROOM_OWNER_PREFIX`。

## 2026-08-11 (review 收口 3) — 删掉悬空注释

`TEAM_ROOM_OWNER_PREFIX` 改为 import 之后，解释它的那段注释悬在原地，
而且让读者去看 `plugins/builtin.teams/src/narranexus_plugins/teams/routes.py`——**那里同样已经不再定义它**。
这正是同一个 commit 刚在 `message_bus_trigger.py` 清掉的毛病，我在这里复现了一次。

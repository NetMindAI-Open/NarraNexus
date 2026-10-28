---
code_file: src/narranexus/platform/message_bus/delivery_notice.py
last_verified: 2026-09-09
stub: false
---

## 2026-09-09 — 第四种结局：`announce_processing_failure`（收件方根本跑不起来）

文件头列的三种结局都假设 turn **跑起来了**。上游 #106 是第四种：收件方 runtime 在产出
任何东西之前就抛（worker 在旧模块路径上 import 失败），三次后消息被 `get_pending_messages`
永久过滤——收件方 owner 收件箱知道，**发件 agent** 不知道，继续等。

新函数把这件事写进发件方正在看的那个会话，并 @ 发件方，让它下一轮开在失败上而不是
沉默上（[[system_messages]] `trigger_label` 给了专门措辞）。**复用 `DELIVERY_FAILED_MSG_TYPE`**
而不是新类型：对每个读者它就是一次投递失败（平台的而非模型的），前端已按 warning 权重
渲染该类型，`PLATFORM_MSG_TYPES` 也已收录——新类型要连改前端与 10 份 locale，换来的只是一个
没人区分的名字。带 `attempts` 与脱敏后的原因，理由同 `announce_delivery_failure`。
调用方：[[message_bus_trigger]] `_wake_sender_on_drop`（仅 DM、仅发件方是 agent、仅非平台行）。

## 2026-09-03 — `announce_undelivered` 只剩 A2A 私聊一个调用点

团队房里的沉默**不再贴任何平台行**:[[message_bus_trigger]] 的 `is_team` 分支改调
[[_bus_activity]] 的 `note_silent_turn`,把 `silent` 步追加到成员活动行,由花名册呈现
(idle 与 queued 态都显示「上一轮未发言」)。原因:prompt 说沉默合法,房间却记成失败,
prod 团队房 16% 的行是这条,agent 学会了硬说。

下面「三种收场」第 3 条与「A2A 才是贵的那一种」末段关于**团队房通知不带 mentions**
的描述,自本日起只在历史行上成立。`mentions=None` 这条分支本身**还活着**——DM 里
errand 续跑、以及发起方是用户的私聊仍走 `mentions=None`;只是不再有团队房调用方。
`announce_delivery_failure`(产出了回复但没送到房间)团队房**仍会发**,未改。


# delivery_notice — 一轮 turn 什么都没送达时，平台开口说话

## 为什么存在

来自 PRD《Team 群聊状态与消息可靠性（看到的必须是真的）》2026-08-04 §四。

一轮 bus turn 有三种收场，此前只有第一种是可见的：

1. 产出了回复，也发出去了 —— 房间里看得到；
2. 产出了回复，**我们没发出去** —— 后端全绿、账单照扣、房间空白；
3. 压根没有可投递的东西 —— 同样是空白房间。

第 2、3 两种，用户都无法与「这个 agent 无视了我」区分开，**彼此之间也无法区分**。
所以它们是两种 msg_type，不是一句含糊的「没有回复」：(2) 是**我们的错、可追责**，
(3) 是 agent 自己这轮空转。混成一句就等于把责任糊掉。

## A2A 才是贵的那一种

团队房间里的沉默只是让人困惑；agent↔agent 私聊里，**提问方是被阻塞的** ——
它在等回答，等不到就永远挂着，它背后的人也一起挂着。所以 A2A 的通知**带
mentions**，会真的把提问方叫醒：一个本来会永远悬着的 errand 自己解开了。

团队房间的通知则**不带 mentions**：那里没有人被阻塞，为一次沉默叫醒全体成员，
代价比沉默本身还大。

## 刻意没做的事：不拿独白兜底

第 3 种的 NexusPower 形态是「正文全走了 `AGENT_THINKING.monologue`，`output_text`
是空的」。看上去最省事的修法是把独白折进回复 —— **不做**。
独白契约向 agent 承诺它的纯文本**永不投递**——owner 能作为过程看到，但它不
指向任何人（见 [[run_collector]] 的 `include_monologue`）。转发给 peer 等于把
它从未打算说给任何人听的推理**投递**出去。**可见 ≠ 已投递**，这条守的是投递。
说一句「本轮没有投递任何内容」是诚实的，而且什么都不泄露。

## 全部 best-effort，返回判定而不是抛异常

投递失败通知走的**正是刚刚失败的那条路**，所以它自己也失败是**预期情况**，不是边缘
情况。因此签名返回 bool 而非 None：调用方要据此决定是否降级到 owner 收件箱。
任何一条通知都不允许变成新的故障源。

同理，错误原文在进入 content 之前先过 `redact_secrets` —— provider SDK 惯于把出错的
密钥原样回显在错误体里，而这行字会变成**全体成员可读的永久记录**，比 owner 收件箱
的受众更宽，所以预算不能比它更松。

相关：[[system_messages]]（两种类型都注册进 `PLATFORM_MSG_TYPES`）、
[[message_bus_trigger]]（唯一调用方）、[[local_bus]]

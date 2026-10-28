---
code_file: tests/message_bus/test_errand_auto_board.py
last_verified: 2026-09-03
stub: false
---

## 2026-09-03 — 「Who may open one」一节 + 现有用例补 `lead_agent_id`

`_open_errand` 默认 `lead_agent_id=LEAD`;直接调用 `record_handoffs` 的六处补
`lead_agent_id=LEAD`;`test_a_promise_that_hands_on_leaves_both_links_watched` 改断言
A3→A4 不开项(组长→A3 仍开);`test_a_real_team_reply_records_its_errand` 补 `teams` 行。
新增:非组长不开/组长开/无组长不开/用户总能开/`opens_handoffs` 真值表/经
`post_team_reply` 从 `teams` 行读组长。


## 2026-08-14 — 为什么存在

[[errand]] 的回归网。这个文件真正守的不是某一条断言，而是**开和闭必须成对存在**
这个设计前提：只开不闭会让每一句随口的「@Bruno 你怎么看」堆在一块每个成员每轮
都读的板子上，巡查再一直催——严格劣于什么都不做。所以两半在一个文件里测。

## 最重要的两条

`test_the_dunhuang_promise_does_not_close_anything` 用的是那次 run 的真实
`final_output`。它变绿的反面是：板子附和 runtime 说「活干完了」，平台再一次不知
道流水线已经死了。

`test_a_promise_that_hands_on_leaves_both_links_watched` 是两半**合起来**在这条真
实消息上的行为：A3 自己的差事保持 open，A4 的差事同时开出来。这两个事实分开推理
都得不出来，所以必须有一条组合断言。

## 分类器的偏向是被断言的，不是被希望的

`test_promise_detection_covers_both_languages...` 把不对称写死：误判为「承诺」的
代价是多一行催办，误判为「交付」的代价是整个机制在它唯一为之而生的那条消息上失
效。改词表时先读这条测试的 docstring。

## 接线测试为什么单独存在

上面所有断言在模块完全没被引用的情况下也会全绿。
`test_a_real_team_reply_records_its_errand` 走
[[message_bus_trigger]] 的 `_handle_channel_batch`，是投递路径忘了调这一层时唯一
会红的那条。`test_bookkeeping_never_breaks_a_delivered_reply` 守另一个方向：记账
失败不许把一次已经成功的投递弄失败（它在开发中真的抓到过一次 NameError）。

## 另一半：提示词

同文件里两条 prompt 断言守「禁止承诺」那条规则的**出口**而不只是禁令本身——只说
「不要」会让沉默成为合规答案，那是 0802 微信那次的失败形状。

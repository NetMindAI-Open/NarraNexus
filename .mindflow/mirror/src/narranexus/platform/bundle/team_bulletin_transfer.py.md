---
code_file: src/narranexus/platform/bundle/team_bulletin_transfer.py
last_verified: 2026-08-11
stub: false
---

# team_bulletin_transfer — 公告栏随 bundle 走

## 为什么存在

bundle 是把一个团队交给别人的方式，公告栏是这个团队的运作约定。
不带它，交出去的就是一个**忘了自己怎么工作的团队**——而接收方无从察觉，
因为「没有公告栏」和「从来没设过规则」看起来一模一样。

## 为什么单独一个模块

两半的规则只有挨在一起才讲得通：**出去时丢掉的东西，正是进来时不能信的东西。**
`builder.py` / `importer.py` 已经足够长，这层配对关系放进去会看不见。

## 出口刻意丢两样

**`author_id`** —— 所有 agent id 和 owner 的 user id 在导入时都会重新铸造，
带过去的 id 会把一条规则归给「现在恰好持有这个 id 的人」，或者归给没人。
`source` 保留，所以 agent 写的规则在对面仍读作 agent 写的、仍可审阅；丢掉的只是悬空指针。

**自动总结** —— 它描述的是**导出方**那台机器上的进展。在接收方那里，它是一段
对从未发生过的工作的自信叙述，而且会坐在每一轮 team prompt 里直到 worker 替换它。
丢掉的代价是零：接收方自己的 worker 几分钟内就会重新生成。

## 入口把 bundle 当不可信输入

bundle 可能被手改过，所以导入侧重新施加线上同样的上限，并且**无论 payload 声称什么都不写总结**——
否则一个手改的 bundle 就能种下一段永久「进展」，让接收方的 worker 把它当成自己的槽。

超预算和格式坏的条目**跳过并记日志，不抛异常**。这与线上 add 路径相反，且是故意的：
一个正在打字的人应该被告知这条放不下；而一个超长的 bundle 应该导出一个
「被截断但能用」的团队，而不是一个接收方无法重试的半成品。

相关：[[builder]]、[[importer]]、[[team_bulletin_repository]]、[[team_schema]]

---
title: MindFlow 是 Goal-Driven，不是 TDD 原教旨
applies_to: [understand, design, plan, dev, test, improve, doc, deliver, verify-claims, orchestrator]
severity: high
recorded_at: 2026-04-12
source: goal-driven 重构设计讨论
---

# Lesson: MindFlow 是 Goal-Driven

## 核心理念

每个工作都围绕明确目标展开。**灵活在手段（evidence form）、严格在结果（goal 必须被证据证明达成）**。

## 不要做的事

- 把 TDD 当 Iron Law 机械执行（UI、配置、脚本类 task 不适合）
- 为写测试而写测试（没贡献 goal 的 test 是负债）
- 让 goal 模糊（「看起来好」「流畅」不是 acceptance_criteria）
- 在 dev 里跳过 evidence（「心里有数」≠ evidence）
- 用 visual-only 证据过 P0 goal（人工易漏，必须配 automated 或 user_in_coding）
- 沉默跳过 goal（不需要做 = 写 skipped_with_reason 显式跳过）

## 要做的事

- 每个 goal 在 understand 阶段就要有可验证的 acceptance_criteria
- 每个 goal 在 design 阶段敲定 verification_strategy
- 每个 task 在 plan 阶段挂到 goal 上
- 每个 task 在 dev 阶段按声明的 evidence_form 留证据
- Test Part B 按 goal 遍历，user_in_coding 覆盖所有用户可见 goal
- Deliver 前 goals.md 所有 P0 必须 verified

## 和 Superpowers 的区分

Superpowers 的 TDD Iron Law 适合**单 task 的严格纪律**。  
MindFlow 面向**完整 feature 交付**，尺度更大，goal 层闭环比 TDD 层纪律更重要。

两者互补：在 task 内部，TDD 仍是推荐（尤其 automated_* evidence form）；但在整个 feature 层，goal-tracking 是我们的独有价值。

## Red Flag 原话（出现立刻 STOP）

- 「这个 task 太简单不用留 evidence」
- 「我手动测过了」
- 「benchmark 大概没问题，没跑基线」
- 「goal 默认达成了吧」
- 「P2 goal 跳过就不记录了」
- 「verification_strategy 到时候再说」
- 「TDD 我不喜欢，所以不做任何 evidence」

遇到就停，补到对应的 evidence + goals.md。

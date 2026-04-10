---
code_file: src/xyz_agent_context/module/basic_info_module/prompts.py
last_verified: 2026-04-10
---

# prompts.py — BasicInfoModule 指令定义

## 为什么存在

`BASIC_INFO_MODULE_INSTRUCTIONS` 向 Agent 注入运行时的基础环境信息：当前时间、Agent ID、用户 ID 等。这让 Agent 在回答"你是谁"或使用工具时有正确的自我认知，不需要猜测或要求用户提供这些信息。

## 上下游关系

- **被谁用**：`BasicInfoModule.__init__` 赋值给 `self.instructions`；`XYZBaseModule.get_instructions()` 用 `ctx_data` 字段格式化后注入系统提示
- **依赖谁**：无外部依赖，纯文本常量；占位符由 `ContextData` 字段提供（如 `{agent_id}`、`{user_id}`、`{current_time}`）

## 设计决策

BasicInfoModule 的 prompts 是最稳定的 prompt 文件之一——它只描述客观事实（谁、何时、在哪运行），不含业务规则或行为约束。修改它的唯一理由是 `ContextData` 的字段变化。

## 新人易踩的坑

- `ContextData` 里字段名变更时，记得同步更新这里的占位符，否则 `get_instructions()` 的 `.format()` 会在运行时抛 `KeyError`。这类错误只在 Agent 实际被调用时才会暴露，不会在 import 时报错。

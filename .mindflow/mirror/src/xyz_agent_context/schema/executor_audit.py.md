---
code_file: src/xyz_agent_context/schema/executor_audit.py
last_verified: 2026-06-18
stub: false
---

# executor_audit.py — Pydantic model for instance_executor_audit rows

## 为什么存在

为 `ExecutorAuditRepository` 提供类型化的行模型。调用方可以用 `ExecutorAuditEvent`
做类型注解，而不是裸 dict；也集中定义了全部已知 event_type 字符串常量，避免
散落在各处的魔法字符串。

## 这个文件不做什么

不做验证或枚举强制——`event_type` 字段是 `str`，不是 `Literal` 约束的
`ExecutorEventType`。这是故意的：`Literal` 只在函数签名上用作提示，
让新的 event_type 可以在调用方直接传入而不需要先更新此文件。

## 上下游关系

- **被谁用**：`ExecutorAuditRepository.record()` 不直接构造此模型（直接写 dict），
  但外部调用方如果想用类型化对象可以通过 `ExecutorAuditEvent(**row)` 构建。
  测试文件目前只导入了 `ExecutorAuditRepository`，未直接用 `ExecutorAuditEvent`。
- **依赖谁**：仅依赖 pydantic，无项目内依赖。

## 设计决策

- `event_type` 用 `str` 而非 `Enum`，与 `lark_trigger_audit_repository.py` 保持一致
  的约定：模块级常量而非 Enum，DB 列保持简单 VARCHAR，新 event_type 不需要改这个文件。
- `id` 和 `created_at` 均为 Optional——DB 自动填充，Python 端创建对象时可省略。
- `ExecutorEventType` Literal 类型别名供类型检查器用，不在运行时强制。

## Gotcha

无特别陷阱——字段全部 nullable，可在构造 `ExecutorAuditEvent` 时只传必填字段。

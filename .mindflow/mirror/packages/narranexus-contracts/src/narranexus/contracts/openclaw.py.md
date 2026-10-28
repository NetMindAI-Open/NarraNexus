---
code_file: packages/narranexus-contracts/src/narranexus/contracts/openclaw.py
last_verified: 2026-09-07
stub: false
---

# contracts/openclaw.py — OpenClaw 生态曾用名词表

## 2026-09-07 — 新建（从 `platform/schema/migration_schema.py` 搬来）

`OPENCLAW_ALIASES` 被 builtin.skills（SKILL.md `metadata.<name>`）与 platform/migration（home 目录、配置文件名）共用；
放在 migration schema 里意味着 skills 插件依赖迁移子系统的词表（复审 M7）。contracts 是两个消费方共同的下游，且不
import 任何一方。顺序即查找优先级。测试以 `is` 钉住三处用的是同一个元组对象。

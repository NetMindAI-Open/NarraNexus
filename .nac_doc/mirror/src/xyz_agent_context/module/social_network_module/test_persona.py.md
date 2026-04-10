---
code_file: src/xyz_agent_context/module/social_network_module/test_persona.py
last_verified: 2026-04-10
---

# test_persona.py — Persona 功能手动测试脚本

## 为什么存在

Persona 推断是需要真实 LLM 调用和数据库连接的端到端功能，单元测试很难覆盖。这个脚本提供了一个快速的手动验证入口——用于开发时确认 `should_update_persona` 的判断逻辑是否正确、`infer_persona` 的 LLM 输出质量是否符合预期、`update_entity_persona` 是否正确写库。

运行方式：`uv run python src/xyz_agent_context/module/social_network_module/test_persona.py`

## 上下游关系

- **被谁用**：开发者手动运行，不被任何生产代码引用
- **依赖谁**：`SocialNetworkModule`、`_entity_updater` 里的三个 Persona 相关函数；需要环境变量和数据库连接

## 设计决策

这是一个探索性测试脚本（exploration script），不是正式的 pytest 测试。代码里直接构造假数据（mock `SocialNetworkEntity`）测试条件判断，也有调用真实 LLM 的路径。两种模式混合，以开发效率为优先，不追求严格的测试隔离。

## Gotcha / 边界情况

- 脚本里的 `project_root` 计算假设文件在 `src/xyz_agent_context/module/social_network_module/` 下（`parents[4]`）。如果目录结构变更，需要更新这个 offset。

## 新人易踩的坑

- 不要误把这个脚本加入 CI——它依赖真实 LLM API Key 和数据库连接，会在 CI 环境里失败。如果需要 CI 可运行的 Persona 测试，应该在 `tests/` 目录里用 mock 重写。

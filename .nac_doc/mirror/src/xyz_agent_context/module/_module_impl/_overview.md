---
code_dir: src/xyz_agent_context/module/_module_impl/
last_verified: 2026-04-10
---

# _module_impl/ — Module 系统的私有实现层

## 目录角色

`_module_impl/` 是 Module 系统的"决策引擎"——它在 `ModuleService` 的接口背后完成最复杂的两件事：决定本次执行应该加载哪些 Module 实例（`loader.py` + `instance_decision.py`），以及如何把多个 Module 的并行收集结果合并成单一的 `ContextData`（`ctx_merger.py`）。

前缀 `_` 表示这是包私有层，外部代码不应直接引用这里的任何文件。所有对外接口都通过 `module_service.py` 暴露。

## 关键文件索引

| 文件 | 职责 |
|------|------|
| `loader.py` | 核心加载逻辑：区分 capability（规则加载）和 task（LLM 决策）Module；虚拟 JobModule 注入；`ALWAYS_LOAD_MODULES` 强制注入 |
| `instance_decision.py` | LLM 实例决策：通过 `OpenAIAgentsSDK.llm_function()` 决定创建哪些 task module 实例；`SKIP_MODULE_DECISION_LLM` 快速路径 |
| `ctx_merger.py` | 并行收集结果合并：`LIST_FIELDS`（extend）、`DICT_FIELDS`（deep merge）、其他字段（非 None 胜出） |
| `selector.py` | 候选实例筛选：从现有实例里按向量相似度、topic hint、时间等维度选出相关实例 |
| `instance_factory.py` | 实例对象工厂：根据 `module_class` 字符串实例化对应的 Module 类 |
| `metadata.py` | Module 元数据聚合：收集所有已注册 Module 的配置信息，供实例决策提示词使用 |
| `prompts.py` | 实例决策用的 LLM 提示词模板 |

## 和外部目录的协作

- `module/__init__.py` 的 `MODULE_MAP`：`instance_factory.py` 通过这个注册表把字符串 `"JobModule"` 映射到实际类；`loader.py` 在 `_get_all_module_classes()` 里遍历它
- `module/base.py` 的 `ModuleConfig`：`metadata.py` 通过 `get_config()` 收集每个 Module 的能力描述，注入到 LLM 实例决策提示词里
- `services/InstanceSyncService`：`instance_decision.py` 里的 `task_key → instance_id` 转换通过 `InstanceSyncService` 完成——`task_key` 是 LLM 输出的语义标识符，`InstanceSyncService` 负责查找或创建对应的真实 `instance_id`
- `repository/InstanceRepository`：`loader.py` 的 `_load_current_instances()` 直接查 DB 取当前活跃实例

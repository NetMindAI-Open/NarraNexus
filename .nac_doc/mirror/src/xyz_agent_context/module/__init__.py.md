---
code_file: src/xyz_agent_context/module/__init__.py
last_verified: 2026-04-10
---

# __init__.py — MODULE_MAP 注册表与包导出

## 为什么存在

这个文件是整个模块系统的"目录"。它做三件事：定义 `MODULE_MAP`（字符串名 → 类的映射）、触发 `rebuild_module_instance_model()`（解决 Pydantic forward reference），以及聚合包的公开 API 供外部 import。

## 上下游关系

- **被谁用**：`ModuleService.__init__` 通过 `from xyz_agent_context.module import MODULE_MAP` 获取注册表；任何需要 `ModuleService`、`HookManager`、`XYZBaseModule` 的外部代码都从这里导入
- **依赖谁**：所有具体 Module 类（循环地）；`_module_impl/` 的工具类；`schema/module_schema.py` 的 `rebuild_module_instance_model`

## 设计决策

**`MODULE_MAP` 是注册的唯一入口**：新模块必须在这里注册，否则 `ModuleLoader` 永远不会加载它。这是故意的集中化——避免自动发现（annotation scanning）带来的不透明性。

**`rebuild_module_instance_model()` 在 import 时调用**：`ModuleInstance` schema 里有 `Optional["XYZBaseModule"]` forward reference，在所有 Module 类都定义完之后才能 resolve。这个调用必须在 `__init__.py` 里执行，因为这是所有类都已 import 之后最早的时机。

**`MemoryModule` 排在 `MODULE_MAP` 第一位**：注释说"最高优先级，确保在其他模块之前执行"。这依赖 `ModuleLoader` 在顺序执行 `hook_data_gathering` 时保留 `MODULE_MAP` 的顺序，`MemoryModule` 需要先把 EverMemOS 查询结果缓存到 `ctx_data.extra_data`，后续的 `ChatModule` 才能读取。

## Gotcha / 边界情况

- 在顶部添加任何会触发循环导入的语句（比如导入 `module_service`）会让整个包 import 失败，症状是难以理解的 `ImportError`。
- 新增 Module 类但只 import 不加到 `MODULE_MAP`，该模块永远不可用，且不会有任何报错。

## 新人易踩的坑

- 注册了新 Module 但忘记在 `DEFAULT_MCP_MODULES`（`module_runner.py`）和对应的 `MODULE_PORTS` 里添加端口配置，导致 MCP 服务器启动时端口冲突或无法访问。

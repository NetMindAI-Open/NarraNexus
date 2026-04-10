---
code_file: src/xyz_agent_context/module/module_service.py
last_verified: 2026-04-10
---

# module_service.py — Module 服务协议层（Facade）

## 为什么存在

`ModuleService` 是 `AgentRuntime` 与整个模块系统之间的唯一接口。它把"如何加载模块"的所有复杂性（LLM 实例决策、DB 实例同步、传统列表模式、fast-path 跳过 LLM）封装在 `_module_impl/loader.py` 里，对 `AgentRuntime` 只暴露两个入口：`load_modules()` 和 `create_module()`。

## 上下游关系

- **被谁用**：`AgentRuntime`（`agent_runtime/`）在流水线第 2 步构造并调用 `load_modules()`；测试代码直接调用 `create_module()` 创建单个实例
- **依赖谁**：`ModuleLoader`（`_module_impl/loader.py`）做实际工作；`MODULE_MAP`（`__init__.py`）提供类注册表；`DatabaseClient` 透传给 `ModuleLoader`

## 设计决策

**薄 Facade 模式**：`ModuleService` 本身几乎没有业务逻辑，只是 `ModuleLoader` 的薄包装。这是有意的——调用方（`AgentRuntime`）不需要知道加载的实现细节，而实现细节可以自由演化（加缓存、换决策策略等）而不影响调用方。

**`MODULE_MAP` 懒加载**：在 `__init__` 里通过 `from xyz_agent_context.module import MODULE_MAP` 动态导入，而非模块顶部导入。这是为了避免循环引用：`module_service.py` 被 `__init__.py` 导出，而 `__init__.py` 又导入了所有具体 Module 类，如果在顶部静态导入 `MODULE_MAP` 就会形成循环。

**`DEFAULT_MODULE_LIST` 的存在**：当 `use_instance_decision=False` 或 `input_content=None` 时回退的传统模式列表。注意 `ModuleService.DEFAULT_MODULE_LIST` 和 `ModuleLoader.DEFAULT_MODULE_LIST` 之间有轻微不一致（前者不含 `MessageBusModule`），需要注意。

## Gotcha / 边界情况

- `DEFAULT_MODULE_LIST` 和 `ModuleLoader.DEFAULT_MODULE_LIST` 目前不完全同步，传统模式下如果某个模块没加载，先比对这两个列表。
- `load_modules()` 只是委托层，所有真正的决策逻辑在 `_module_impl/loader.py`。调试实例决策问题应该去那里，而不是这里。

## 新人易踩的坑

- 试图在 `ModuleService` 里添加业务逻辑——任何新逻辑都应该放到 `ModuleLoader` 或 `_module_impl/` 对应文件里，`ModuleService` 保持薄。
- 以为 `get_all_module_names()` 返回的是"当前已加载的模块"——它返回的是 `MODULE_MAP` 里注册的所有可用模块名，与当次调用无关。

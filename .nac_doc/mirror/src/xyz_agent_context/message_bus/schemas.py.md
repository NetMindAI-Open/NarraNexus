---
code_file: src/xyz_agent_context/message_bus/schemas.py
last_verified: 2026-04-10
stub: false
---

# schemas.py — MessageBus 数据模型定义

## 为什么存在

`MessageBusService` 的方法参数和返回值需要稳定的类型，同时 `LocalMessageBus` 的数据库行需要反序列化成 Python 对象。`schemas.py` 集中定义这四个数据模型，让接口层（`message_bus_service.py`）和实现层（`local_bus.py`）都依赖同一套类型，不各自定义。

## 上下游关系

**被谁用**：`message_bus_service.py` 在抽象方法签名里用这些类型；`local_bus.py` 在数据库行转换时（`_row_to_message()` 等）实例化这些类；`module/message_bus_module/_message_bus_mcp_tools.py` 用 `BusMessage` 等类型做 MCP 工具的返回值。

**依赖谁**：只依赖 Pydantic v2 和 Python 标准库，无业务逻辑依赖。

## 设计决策

所有时间戳字段类型是 `Any = None`（`Timestamp = Union[str, datetime]` 别名也定义了但实际字段用 `Any`）。这是因为 SQLite 返回时间戳为字符串，MySQL 返回为 datetime 对象，统一用 `Any` 避免在多后端场景下类型验证失败。代价是丧失了类型安全——调用方在比较时间时需要自行处理 `str(ts)` 或 `ts.isoformat()` 的转换。

`model_config = {"arbitrary_types_allowed": True}` 是为了支持 `Any` 时间戳和其他非标准类型在 Pydantic 模型里的使用。

`BusMessage.mentions` 是 `Optional[List[str]]`，在数据库里序列化为 JSON 字符串（`local_bus.py` 的 `_row_to_message()` 里有 `json.loads`）。

## Gotcha / 边界情况

`BusChannelMember` 有两个游标字段：`last_read_at` 和 `last_processed_at`。`last_read_at` 给前端"已读"展示用，`last_processed_at` 给后台 `get_pending_messages()` 用。在数据库里这是两列，不要把它们混用。

## 新人易踩的坑

时间戳比较时不要直接 `msg.created_at > cursor`——在 SQLite 模式下两者都是字符串，字符串比较在 ISO 8601 格式下通常正确，但如果格式不完全一致（有无时区后缀、精度不同）会出现奇怪的排序结果。`LocalMessageBus` 里用的是 `str(latest.created_at)` 保证一致性。

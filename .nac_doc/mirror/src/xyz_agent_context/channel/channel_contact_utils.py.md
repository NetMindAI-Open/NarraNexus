---
code_file: src/xyz_agent_context/channel/channel_contact_utils.py
last_verified: 2026-04-10
stub: false
---

# channel_contact_utils.py — contact_info.channels 字段的读写规范化工具

## 为什么存在

Social Network 里每个 Entity 有一个自由格式的 `contact_info` JSON 字段，用于存储各渠道的联系方式（Matrix ID、Slack workspace、房间 ID 等）。问题在于 LLM 生成的这个字段格式非常不稳定：有时把 Matrix ID 放在顶层（`{"matrix": "@xxx"}`），有时放在嵌套里（`{"channels": {"matrix": {"id": "@xxx"}}}`），有时用 `user_id` 代替 `id`。

`channel_contact_utils.py` 把所有这些变体规范化成统一格式，让消费方只需要关心 `{"channels": {"matrix": {"id": "...", "rooms": {...}}, ...}}` 这一种结构。

## 上下游关系

**被谁用**：`module/social_network_module/` 里的 entity 更新工具调用 `merge_contact_info()` 和 `normalize_contact_info()` 在保存 LLM 提取的联系方式时做规范化；MatrixModule（和未来的 Slack Module）调用 `get_channel_info()`、`get_room_id()`、`set_room_id()` 读写渠道特定信息；`ChannelContextBuilderBase.get_sender_entity()` 的子类实现读取 entity 的 contact_info 后用这些工具查找发件人的渠道 ID。

**依赖谁**：只依赖 Python 标准库（`copy` 模块），无外部依赖。纯工具函数，不访问数据库。

## 设计决策

`set_channel_info()` 和 `merge_contact_info()` 使用深度 merge（`_deep_merge()`）而非覆盖，这是关键设计——当 LLM 更新 Matrix ID 时，不应该清除之前记录的 Slack workspace；当更新某个 room 的 room_id 时，不应该清除同渠道里其他 room 的 room_id。这个 merge 策略让 contact_info 可以增量更新。

`normalize_contact_info()` 的规范化逻辑维护了一份 `known_channels` 集合（`{"matrix", "slack", "discord", "telegram"}`）。这个设计是刻意的——只对已知渠道做特殊处理，未知的 key 原样保留（保证向前兼容）。添加新渠道时需要同时在这里添加。

`id_aliases` 是一组 LLM 常用的错误字段名（`user_id`、`matrix_user_id` 等），会被统一规范化为 `id`。这个列表基于实际观察 LLM 输出的常见错误积累而来。

## Gotcha / 边界情况

`_deep_merge()` 在遇到类型冲突时（比如 key 在 base 里是 `str`，在 override 里是 `dict`）会直接用 override 覆盖，不报错。这意味着格式错误的 contact_info 合并进来可能把正确的字段覆盖掉——发现 contact_info 数据损坏时，可以从这里找原因。

`normalize_contact_info()` 处理 `{"matrix": "@xxx:host"}` 这种顶层字符串时，会把它放进 `channels.matrix.id`，但**不会**设置 `preferred_channel`——即使顶层只有一个渠道，也不会自动假设它是首选渠道。

## 新人易踩的坑

所有工具函数都**修改传入的 dict in-place**，同时也有返回值（为了链式调用）。别以为函数不用返回值就安全——原始 dict 已经被修改了。如果需要不可变操作，自己先 `copy.deepcopy(contact_info)` 再传入。

`get_room_id()` 返回的是与特定 `counterpart_id` 的"房间 ID"（Matrix 里是 `!roomid:server`），不是渠道里该 Agent 自己的 ID（那个是 `get_channel_info(contact_info, "matrix").get("id")`）。两者容易混淆。

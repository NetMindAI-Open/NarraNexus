---
code_file: src/xyz_agent_context/agent_framework/loop/turn_input.py
last_verified: 2026-07-27
stub: false
---
# loop/turn_input.py — 物化层 turn 输入的显式打包

## 为什么存在

step_3 历史上把一个 turn 的输入以四个散落 local（messages / mcp_servers /
skill_env_vars / extra_disallowed_tools）逐个塞给 driver——「物化层」只
存在于调用点的局部变量里。TurnInput 把它变成一个显式对象：所有 driver
可证明地吃同一份东西，且 bundle 有了生长的位置。

## 设计约束

- `driver_kwargs()` **逐字节复刻**历史调用形状：messages/mcp_servers 按
  引用传（step_3 在调用前还会 merge mcp_servers）、空 extra_env /
  disallowed_tools 归一为 None 让 driver 默认值生效。
- `refs` 是预留的引用层字段（TurnContext 双层输入的另一半：自研 loop
  凭可序列化 ID 自己投影上下文）——在有 driver 真正消费之前**恒为
  None**。声明没人实现的字段正是 schema 诚实原则要防的坑。
- `cancellation` 刻意不进 TurnInput：它是 runtime 拥有的每-run 控制流，
  不是 turn 内容。

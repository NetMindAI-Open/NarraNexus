---
code_file: src/narranexus/platform/message_bus/team_files.py
last_verified: 2026-08-10
stub: false
---

## 2026-08-10 (review 修正) — 改走仓储；`shared_at` 归一为 offset-aware

两处对外可见的变化：

1. **数据访问路径**：不再手写 `SELECT * FROM team_files …`，改走
   [[team_workspace_repository.py]] 的 `TeamFileRepository.list_by_team(limit=)`。这条曾是
   `team_files` 上**第三处**手写 SQL，也是**唯一带 bound LIMIT** 的一条——而那个 seam 正是同一
   分支为收口方言风险建的，留一处在外面等于自我否定。
2. **`shared_at` 的 wire 形状**：`str(created_at)` → `parse_dt(...).isoformat()`，即
   `'2026-08-07T12:34:56+00:00'`。这是 **agent 在 `bus_list_team_files` 返回里直接读到的字段**，
   不带 offset 时消费方会按本地时区解释。

# team_files — 枚举团队共享目录

## 为什么存在

共享目录一直有文件，缺的是**问它有什么**的能力。落盘用生成的 file_id 命名，原始名只活在
索引里；索引之前不存在，所以发现渠道只有「某个 agent 在房间里念一遍绝对路径」，别人恰好
注意到。这让「你收到文件了吗」变成模型之间的社交协议——模型记得叙述时才可靠。

## 关键设计

**授权按成员关系，不按 owner。**一个 user 拥有多个 team，「这是我 owner 的 team」**不是**
读它的理由——按 owner 判会让该 owner 的任意 agent 读到他所有 team 的目录。

**空列表是 success，不是 error。**「还没人分享」是一个答案。返回错误会把模型推向重试或为
一个正常工作的工具道歉。拒绝时**不带 `files` 键**，调用方无法把「拒绝」误当成「空目录」。

**每条带绝对 path。**列出来但不能操作等于散文——path 正是 Read 接受的形式，也是 team prompt
一直告诉 agent 用的形式。

**逻辑不放在 MCP 工具模块里**：这样「成员关系而非所有权」这条规则不依赖 MCP 传输即可测试，
同一个函数日后也能直接支撑路由。

## 上下游

- **被谁用**：`bus_list_team_files` MCP 工具（[[_message_bus_mcp_tools.py]]）；team prompt
  （[[message_bus_trigger.py]]）明确指向该工具而非让 agent 自己猜路径
- **依赖谁**：[[team_workspace_repository.py]] 的 `TeamFileRepository`（不再直接碰表；写入方仍是 [[_bus_attachment_impl.py]]）、`team_members`

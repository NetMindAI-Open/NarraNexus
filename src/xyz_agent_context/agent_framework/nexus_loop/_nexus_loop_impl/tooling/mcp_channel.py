"""
@file_name: mcp_channel.py
@author: Bin.Liang
@date: 2026-07-27
@description: MCP client 通道——平台 15 模块 86 工具与任意外部 MCP server
的统一接入(B3: 回复用户的唯一通道经此, 是最小集核心而非扩展)。

契约要点:
- 工具名保留 mcp__{server}__{tool} 命名(下游三处子串匹配依赖, A2);
- SSE 传输 + per-agent headers(mcp_servers spec 对象原样消费);
- tools/list_changed -> 整体重建(Hermes nuke-and-repave), 配
  _generation 代数计数器做缓存失效, RLock 等价的 asyncio 锁防并发重建;
- 不可信内容包裹(Hermes): MCP/web 结果包 untrusted 标记、中和内嵌的
  同名分隔符、不做可伪造的「已包裹」快路径。
"""

from typing import Any

from xyz_agent_context.agent_framework.nexus_loop.contracts.model import McpServerSpec
from xyz_agent_context.agent_framework.nexus_loop.contracts.tooling import (
    ToolContext,
    ToolResult,
    ToolSpec,
)


class McpToolChannel:
    """一组 MCP server 的聚合通道(ToolChannel 实现)。"""

    def __init__(self, servers: dict[str, McpServerSpec]) -> None:
        """servers: {name: spec}, 来自 TurnInput.mcp_servers(平台注入,
        本类不知道「模块」概念——解耦: 它只认识 MCP 协议)。"""
        ...

    async def connect(self) -> None:
        """建立全部 server 连接并拉取初始工具清单; 单 server 失败降级为
        该 server 工具缺席(记 error 事件), 不拖垮整个通道。"""
        ...

    def list_tools(self) -> list[ToolSpec]:
        """全部已连接 server 的工具, mcp__ 前缀命名; server 内顺序与
        server 间顺序均确定性(C2)。readOnlyHint/destructiveHint 映射进
        ToolAnnotations(并行策略的依据)。"""
        ...

    async def call(self, name: str, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        """路由到对应 server 执行; 结果文本过不可信内容包裹后返回。"""
        ...

    async def refresh(self) -> bool:
        """响应 tools/list_changed 或显式触发: 全量重拉清单、整体替换、
        _generation += 1。返回是否变化(dispatcher 据此失效缓存)。"""
        ...

    async def add_servers(self, servers: dict[str, McpServerSpec]) -> None:
        """运行中追加 server(expand_module 的执行末端): 连接、清单尾部
        追加、代数递增。"""
        ...

    async def aclose(self) -> None:
        """回合结束/取消时统一收敛全部连接(孤儿连接是既往事故来源)。"""
        ...

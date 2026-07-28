"""
@file_name: expansion.py
@author: Bin.Liang
@date: 2026-07-27
@description: 动态能力加载(ModuleExpander)——Owner 核心设定的执行体:
agent 在运行中以 expand_module 工具为信号, 为当前回合追加系统指令与
MCP tools, 实现平台 modules 的按需装载。

与 Nexus 解耦的方式: 本类不认识「模块」——它消费一个抽象的
ExpansionCatalog(平台注入: {key: (指令文本, McpServerSpec 集合)}),
expand_module(key) = ①mcp_channel.add_servers(尾部追加)②指令文本进
「动态尾部」(V 层, 绝不改稳定前缀——C2)③记 cache 击穿计数埋点。
CARD 索引(key + 一句话描述)常驻 S4 段: 发现权永不裁剪, 取用显式付费。
Codex tool_search / OpenClaw tool_search 已验证这条「大目录按需拉入」
路线可行。
"""

from dataclasses import dataclass

from xyz_agent_context.agent_framework.nexus_loop.contracts.model import McpServerSpec


@dataclass(frozen=True)
class ExpansionEntry:
    """目录里的一项可展开能力(平台的一个 module 在 loop 眼中的样子)。"""

    key: str
    card: str                                  # 一句话描述(CARD 索引用)
    instructions: str                          # 展开后注入的指令文本
    mcp_servers: dict[str, McpServerSpec]      # 展开后追加的 MCP servers


class ModuleExpander:
    """expand_module 的执行体(被 context_tools.expand_module 转发调用)。"""

    def __init__(self, catalog: tuple[ExpansionEntry, ...], mcp_channel: object) -> None:
        """catalog: 平台注入的可展开目录; mcp_channel: McpToolChannel 引用
        (追加 server 的执行末端)。"""
        ...

    def card_index(self) -> str:
        """CARD 索引文本(S4 段数据源): 全部 key + card, 确定性排序。"""
        ...

    async def expand(self, key: str, ctx: object) -> str:
        """执行一次展开: 校验 key -> add_servers -> 返回指令文本
        (由 loop 追加进动态尾部)。幂等: 重复展开同 key 返回既有状态,
        不重复追加。每次展开记录 cache 击穿计数(埋点)。"""
        ...

    def expanded_keys(self) -> frozenset[str]:
        """本回合已展开的 key 集合(账本/诊断用)。"""
        ...

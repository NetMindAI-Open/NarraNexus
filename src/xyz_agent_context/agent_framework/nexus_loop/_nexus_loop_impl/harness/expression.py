"""
@file_name: expression.py
@author: Bin.Liang
@date: 2026-07-27
@description: 独白/表达契约(ExpressionContract)——本框架独有语义的唯一权威实现。

四家开源 harness(CC/Codex/Hermes/OpenClaw)的 assistant 文本都直达用户;
NexusAgent 相反: 文本是 agent 的自我思考, 触达用户/外部世界必须显式调用
表达工具。

关键立场(Owner 2026-07-27 定稿): **loop 不内建任何表达工具。** 对外发声
是平台 module 赋予的能力(chat_module 等, 经 MCP 通道进入), 不是 loop 的
basic 功能——loop 只负责思考。因此"哪些工具算表达工具"完全由平台经
TurnRequest.expressive_tools 名单注入; loop 只做识别与标记, 不做提供。
没接渠道 module 的 agent 依然完整可跑, 只是没有发声通道。

本类是这条语义的单点: 事件分类、表达工具判定、独白呈现标记都从这里出,
loop/dispatcher/adapter 不各自散落 if 判断。
"""

from xyz_agent_context.agent_framework.nexus_loop.contracts.events import LoopEvent
from xyz_agent_context.agent_framework.nexus_loop.contracts.tooling import ToolCall, ToolSpec


class ExpressionContract:
    """独白(monologue)与对外表达(expression)的裁决器。"""

    def __init__(self, expressive_tools: frozenset[str]) -> None:
        """expressive_tools: 平台注入的表达工具名单(唯一来源)。
        v1 实际内容是 chat_module MCP 的回复类工具名;平台接了新渠道
        module 就往名单里加名字——loop 零改动。空名单是合法状态:
        该 agent 本回合没有发声通道。"""
        ...

    def is_expressive(self, spec_or_call: "ToolSpec | ToolCall") -> bool:
        """判定一个工具(或调用)是否属于对外表达通道。

        用途: ①事件打独白/表达标记供前端分区呈现; ②P3 流式参数投影只对
        表达工具的声明字段开启; ③统计「本回合是否产生过有机回复」
        (对应现有 _has_organic_reply 的子串判断, 收敛为结构化判定)。
        """
        ...

    def tag_text_event(self, event: LoopEvent) -> LoopEvent:
        """给文本/思考增量事件补上独白标记(payload 层面), 声明其
        「非用户回复」性质——前端据此渲染成思考流而不是聊天气泡。"""
        ...

    def turn_had_expression(self, tool_calls_seen: list[ToolCall]) -> bool:
        """回合结束时判断: 本回合是否有过至少一次对外表达。

        平台可据此决定是否触发「agent 只想了没说」的后续策略(平台的事,
        loop 只提供事实)。
        """
        ...

"""
@file_name: subagent_channel.py
@author: Bin.Liang
@date: 2026-07-27
@description: 子代理通道——spawn_subagent 工具面(P4 实现, 接口 day-1)。

设计定稿(07-22 §5.8 三家杂交): 能力交集(子代理工具面 = 父面 ∩ 声明面,
Hermes)+ 血缘 key(OpenClaw)+ 结果作为新轮回归; 我们独有的「遗腹结果」:
主 session 结束后子代理照跑、结果持久归队(控制面 announce 服务, 平台侧)。
策略传播: 父 PolicyEngine 实例引用直接传入——修掉现有 hook 不传播盲区。
子代理 prompt 用 PromptMode.MINIMAL 派生, 不维护第二份 prompt。
"""

from xyz_agent_context.agent_framework.nexus_loop.contracts.tooling import (
    ToolContext,
    ToolResult,
    ToolSpec,
)


class SubagentChannel:
    """spawn_subagent / agents_wait / agents_list 的 ToolChannel(P4)。"""

    def __init__(self, policy_engine: object, announce_endpoint: str | None) -> None:
        """policy_engine: 父引擎引用(继承交集); announce_endpoint: 控制面
        结果归队服务(平台注入 URL, 不 import 平台)。"""
        ...

    def list_tools(self) -> list[ToolSpec]:
        """子代理工具族 spec(v1 经 feature-gate 关闭, 不进 schema)。"""
        ...

    async def call(self, name: str, args: dict, ctx: ToolContext) -> ToolResult:
        """P4: spawn -> 血缘 key 登记 + 后台起子回合; wait/list -> 查询。"""
        ...

    async def refresh(self) -> bool:
        """恒 False(工具面静态)。"""
        ...

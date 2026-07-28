"""
@file_name: context_tools.py
@author: Bin.Liang
@date: 2026-07-27
@description: 上下文自助三件套——view_image / context_status / expand_module
的 spec 与 handler(expand_module 的执行末端在 expansion.py, 本文件只挂
schema 与转发, 保持「工具声明跟 spec 走、能力实现跟 channel 走」)。

context_status(Codex TokenBudget 思路 + E5 成本透明): agent 可自查剩余
窗口与已花费——把「上下文经济学」交给 agent 自己, 而不是平台硬管
(铁律 #14/#15 的正向表达)。
"""

from xyz_agent_context.agent_framework.nexus_loop.contracts.tooling import (
    ToolContext,
    ToolResult,
    ToolSpec,
)


def specs() -> list[ToolSpec]:
    """view_image(read_only)/ context_status(read_only)/ expand_module
    的 spec 清单。expand_module 的 description 必须写清「显式付费」语义:
    展开会增加上下文成本, 按需取用。"""
    ...


async def view_image(args: dict, ctx: ToolContext) -> ToolResult:
    """P3: 把 workspace 内图片读入上下文(转 provider 图像块; 尺寸/张数
    上限进配置)。"""
    ...


async def context_status(args: dict, ctx: ToolContext) -> ToolResult:
    """P3: 返回本回合累计 usage、估算剩余窗口、cache 命中省额
    (数据源 = TurnLedger.total_usage + profile 窗口表)。"""
    ...


async def expand_module(args: dict, ctx: ToolContext) -> ToolResult:
    """Owner 核心设定的入口: agent 以工具调用为信号, 为本回合动态加载
    更多平台能力(模块指令 + MCP tools)。转发给 expansion.ModuleExpander,
    本 handler 不含逻辑。"""
    ...

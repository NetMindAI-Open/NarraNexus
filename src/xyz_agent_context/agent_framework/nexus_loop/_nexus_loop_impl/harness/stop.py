"""
@file_name: stop.py
@author: Bin.Liang
@date: 2026-07-27
@description: 停止评判策略实现。铁律 #14: 禁止任何轮次/时长硬顶——
停止条件只能是语义性的(不再有动作 / 目标达成), 永远不是计数器。
"""

from typing import Sequence

from xyz_agent_context.agent_framework.nexus_loop.contracts.protocols import LedgerView
from xyz_agent_context.agent_framework.nexus_loop.contracts.tooling import ToolCall


class NoMoreActionsStop:
    """v1 默认停止策略: 模型本 step 不再发起任何工具调用即停。

    注意与四家开源实现的差异: 他们的停止 = 模型停嘴(assistant 文本即回复);
    我们的文本是独白, 所以「说了话但没动作」同样是停——说话不续命。
    (pi 的 while-no-max-steps 立场与铁律 #14 同向, 采纳; Hermes 的
    max_iterations 硬顶与铁律冲突, 明确不抄。)
    """

    async def should_stop(self, step_calls: Sequence[ToolCall], ledger: LedgerView) -> bool:
        """step_calls 为空 -> True。无 IO、无 LLM 调用, 纯判断。"""
        ...


class GoalSpecStop:
    """P4 预留座位: 基于 GoalSpec 的独立停止评判(便宜模型评判者)。

    v1 不实现、不实例化; 放在这里只为宣示「停止评判的扩展 = 换 Assembly
    里的 StopPolicy 实现」, loop.py 一行不改。
    """

    async def should_stop(self, step_calls: Sequence[ToolCall], ledger: LedgerView) -> bool:
        """P4: 综合账本与目标规格, 调用评判模型给出停止裁决。"""
        ...

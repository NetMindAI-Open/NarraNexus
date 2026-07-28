"""
@file_name: turn_ledger.py
@author: Bin.Liang
@date: 2026-07-27
@description: TurnLedger——回合内真相(未来 SessionEventLog 的同形前身)。

三个不变量由**构造保证**(方法不存在非法路径, 不靠运行时检查):
1. tool_use/tool_result 配对——打断只能走 synthesize(合成 result);
2. role 交替合法性;
3. seq 单调(全部事件的 seq 由本类唯一分配)。
「entry 即 schema」: P1 落 nexus_events 表时 EventLogWriter 直接持久化
entries(), 本类零改动; resume = TurnLedger(turn, base=日志前缀)。
"""

from typing import Sequence

from xyz_agent_context.agent_framework.nexus_loop.contracts.events import (
    LedgerEntry,
    LoopEvent,
    Usage,
)
from xyz_agent_context.agent_framework.nexus_loop.contracts.model import ModelEvent
from xyz_agent_context.agent_framework.nexus_loop.contracts.tooling import ToolCall, ToolResult


class TurnLedger:
    """回合账本(同时满足 contracts.protocols.LedgerView 只读协议)。"""

    def __init__(self, base: Sequence[LedgerEntry] = ()) -> None:
        """base 是 resume/fork 的缝: P4 从事件日志重建时传入前缀, v1 恒空
        (测试锁定「v1 恒空」——预留纪律三件套)。"""
        ...

    def record_model_event(self, ev: ModelEvent) -> list[LoopEvent]:
        """记录一个模型事件, 返回应产出的 LoopEvent(含轨道分配:
        text/thinking 增量 -> ui 轨; 完整 tool_use -> model 轨; usage 累加)。
        独白标记由 harness.ExpressionContract 在 loop 侧补挂, 本类不管语义。"""
        ...

    def record_tool_result(self, call_id: str, result: ToolResult) -> list[LoopEvent]:
        """记录工具结果并完成配对; 未知 call_id 直接抛错(不变量违规 =
        程序 bug, 不吞)。"""
        ...

    def synthesize_interrupted_results(self, reason: str) -> list[LoopEvent]:
        """打断路径: 为全部未配对调用合成 is_synthetic=True 的 tool_result,
        保证配对不变量后回合可安全终止(CC 同款语义)。"""
        ...

    def record_steering(self, messages: list) -> list[LoopEvent]:
        """记录步边界注入的插话消息(model 轨, 纯追加)。"""
        ...

    def close_turn(self, end_reason: object) -> LoopEvent:
        """产出 turn_done 事件(含 EndReason 与累计 usage)。"""
        ...

    # ---- LedgerView 只读协议 ----

    def entries(self) -> Sequence[LedgerEntry]:
        """全部条目(按 seq 有序), EventLogWriter/投影/回放的数据源。"""
        ...

    def open_tool_calls(self) -> Sequence[ToolCall]:
        """尚未配对的调用清单。"""
        ...

    def total_usage(self) -> Usage:
        """累计真实用量——response.done 计费链的唯一数据源(A4/C3)。"""
        ...

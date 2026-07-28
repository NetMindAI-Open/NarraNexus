"""
@file_name: scheduling_channel.py
@author: Bin.Liang
@date: 2026-07-27
@description: 调度通道——update_plan(P3)与 sleep(P4)两个循环原语的工具面。

update_plan: PlanLedger 的写入口(plan 事件进账本 ui 轨 + V2 prompt 段
每轮重注入——「持久规则放每轮重注入的位置」共识 #4); 对标 CC TodoWrite /
Codex update_plan。
sleep: 自我调度原语——loop 以 EndReason.SUSPENDED 收尾, 控制面定时器负责
唤醒(容器死唤醒不丢, 我们独有); 与平台的自我留言通道(module 赋予,
落 TriggerInbox)组合出「稍后跟进/自我提醒」类涌现行为(07-22 §3.5)。
"""

from xyz_agent_context.agent_framework.nexus_loop.contracts.tooling import (
    ToolContext,
    ToolResult,
    ToolSpec,
)


class SchedulingChannel:
    """update_plan / sleep 的 ToolChannel。"""

    def __init__(self, timer_endpoint: str | None) -> None:
        """timer_endpoint: 控制面定时器服务(平台注入; None = sleep 不在场)。"""
        ...

    def list_tools(self) -> list[ToolSpec]:
        """update_plan / sleep 的 spec(v1 经 feature-gate 关闭)。"""
        ...

    async def call(self, name: str, args: dict, ctx: ToolContext) -> ToolResult:
        """update_plan -> plan 事件写账本(前端进度渲染 + V2 段数据源);
        sleep -> 登记唤醒时间, 返回后 loop 在 STOP_CHECK 相位产出
        SUSPENDED(经 StopPolicy 协作, 不是硬中断)。"""
        ...

    async def refresh(self) -> bool:
        """恒 False。"""
        ...

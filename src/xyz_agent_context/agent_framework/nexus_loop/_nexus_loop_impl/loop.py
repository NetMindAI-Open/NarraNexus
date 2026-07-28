"""
@file_name: loop.py
@author: Bin.Liang
@date: 2026-07-27
@description: NexusAgentLoop——薄状态机/相位推进器。**≤500 行 review 门禁**
(对 Hermes 5216 行 god-function 的结构性免疫; pi 内核证明可行)。

只做「下一步做什么」的相位推进, 禁止业务逻辑:
PROJECT -> MODEL_STREAM -> DISPATCH -> DRAIN_STEERING -> STOP_CHECK -> 回到
PROJECT 或终止。全部分叉都是策略调用(StopPolicy/SteeringInlet/RetryPolicy),
全部能力都是通道调用(ToolExecutor)——§6.11 扩展路线图整表没有一行需要
改本文件, 这是 v2 设计的核心承诺。
铁律 #14: 本文件永远不出现 max_iterations / max_duration / 总超时。
"""

from typing import AsyncIterator

from xyz_agent_context.agent_framework.nexus_loop.contracts.events import LoopEvent


class NexusAgentLoop:
    """回合执行器: 一个实例跑一个 turn(无跨回合状态——stateless worker)。"""

    def __init__(self, assembly: object, ledger: object, projector: object) -> None:
        """assembly: LoopAssembly(全部依赖的唯一注入点); ledger: TurnLedger;
        projector: ContextProjector。类型注解声明期从宽, 实现期收紧为
        真实类型(assembly 在顶层包, 避免声明期反向 import)。"""
        ...

    async def run_turn(self) -> AsyncIterator[LoopEvent]:
        """执行一个完整回合, 流式产出全部 LoopEvent。

        每个相位间检查 cancel.requested()(打断绝不切开 tool_use/result
        配对——走 ledger.synthesize_interrupted_results); 每个事件先过
        log.append 再 yield(「落日志是路过」); STOP_CHECK 相位依次调
        SteeringInlet.drain(有插话则继续)与 StopPolicy.should_stop。
        任何路径(正常/打断/异常)都必须以 close_turn 事件收尾——
        response.done 是计费链唯一数据源(A4)。
        """
        ...

    async def _phase_project(self) -> list:
        """PROJECT: 先做压缩检查(compaction.should_compact -> 主动压缩,
        replacement 条目入账本并落日志; 压缩前经 steering 缝注入
        memory-flush 提醒), 再由 projector 产出本 step messages +
        prompt_cache 计划。被动路径: MODEL_STREAM 抛 CONTEXT_OVERFLOW
        分类错误时, 回到本相位强制 compact 后重试当前 step。"""
        ...

    async def _phase_model_stream(self, messages: list) -> list:
        """MODEL_STREAM: model.stream_step 消费; 事件经 ledger 记账、
        expression 契约打独白标记后产出; 收集本 step 的 tool_use。
        流异常 -> ErrorClassifier 分类 -> RetryPolicy 裁决重试或终止。"""
        ...

    async def _phase_dispatch(self, calls: list) -> None:
        """DISPATCH: tools.execute_step(内部含 policy 裁决与并行策略);
        结果经 ledger 配对记账。"""
        ...

    async def _phase_drain_steering(self) -> bool:
        """DRAIN_STEERING: 排空插话, 有注入则记账并返回 True(继续循环)。
        v1 恒 False(NullSteeringInlet)。"""
        ...

    async def _phase_stop_check(self, step_calls: list) -> bool:
        """STOP_CHECK: StopPolicy 裁决; True 则产出 turn_done 并终止。"""
        ...

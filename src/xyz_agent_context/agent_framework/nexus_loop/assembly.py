"""
@file_name: assembly.py
@author: Bin.Liang
@date: 2026-07-27
@description: LoopAssembly——全部组件的唯一汇合点 + 默认装配工厂。

「加组件 = 加带默认值的字段」; 测试整体替换任意组件。不用全局注册表的
原因(07-26 §6.1): loop 进程在 executor 容器内, per-turn 装配来自请求体
(无平台 DB), 显式 dataclass 比隐式全局更符合 stateless worker(铁律 #20)。

本文件是 nexus_loop 对外的**唯一使用接口**: 消费方(adapters/nexus 的
driver)只调 build_assembly() + run_turn_events(), 不触碰任何 _nexus_loop_impl
内部件。
"""

from dataclasses import dataclass, field
from typing import Any, AsyncIterator

from xyz_agent_context.agent_framework.nexus_loop.contracts.events import LoopEvent
from xyz_agent_context.agent_framework.nexus_loop.contracts.protocols import (
    CancellationSignal,
    CompactionPolicy,
    ContextProjector,
    ErrorClassifier,
    EventLogWriter,
    ModelClient,
    RetryPolicy,
    SteeringInlet,
    StopPolicy,
    ToolExecutor,
)


@dataclass(frozen=True)
class TurnRequest:
    """一次回合的完整入参包(driver 从遗留签名翻译而来)。

    与平台解耦的边界: 平台的一切(模块、Awareness、narrative、渠道)在
    这里都已还原成中性数据——messages / mcp_servers / expansion catalog /
    expressive 工具名单 / disallowed 集。换一个平台, 填另一份 TurnRequest,
    loop 照跑。
    """

    thread_id: str
    messages: list[dict[str, Any]]                 # 物化层拼好的完整消息
    mcp_servers: dict[str, dict[str, Any]]         # {name: {url, headers?}}
    model_params: dict[str, Any]                   # model/provider/thinking 解析结果
    workspace: str
    agent_id: str
    disallowed_tools: frozenset[str] = frozenset()
    expressive_tools: frozenset[str] = frozenset() # 表达工具名单(独白契约)
    expansion_catalog: tuple = ()                  # ExpansionEntry 元组(动态加载目录)
    skill_dirs: tuple[str, ...] = ()
    extra_env: dict[str, str] = field(default_factory=dict)
    streaming: bool = True


@dataclass(frozen=True)
class LoopAssembly:
    """NexusAgentLoop 的全部依赖(策略缝 v1 全部为 no-op/最小实现)。"""

    model: ModelClient
    tools: ToolExecutor
    projector: ContextProjector
    compaction: CompactionPolicy    # v1 = ToolResultPruner(Owner: 压缩 day-1 在场)
    log: EventLogWriter
    errors: ErrorClassifier
    cancel: CancellationSignal
    stop: StopPolicy
    steering: SteeringInlet
    retry: RetryPolicy
    hooks: Any = None            # HookRegistry(实现类在 impl 层, 声明期 Any)
    expression: Any = None       # ExpressionContract(同上)


def build_assembly(request: TurnRequest, cancel: CancellationSignal) -> LoopAssembly:
    """默认装配工厂(唯一构造点)。

    装配决策全部在此: resolve_profile -> LiteLLMModelClient(或旁路);
    channels = (BuiltinToolset, McpToolChannel, SkillsChannel, ...) 按
    feature-gate 取舍; policy layers = (DisallowedTools, WorkspaceConfinement);
    v1 策略缝 = NoMoreActionsStop / NullSteeringInlet / NoRetry /
    HookRegistry.empty()。测试用自己的 LoopAssembly 整体替换, 不 patch。
    """
    ...


async def run_turn_events(
    request: TurnRequest, cancel: CancellationSignal
) -> AsyncIterator[LoopEvent]:
    """nexus_loop 的顶层入口: 装配 -> 建账本/投影 -> 跑 NexusAgentLoop,
    流式产出类型化 LoopEvent。

    注意: 产出的是内部事件; 遗留 dict 契约的翻译(LegacyEventAdapter)
    由 adapter 层调用——保持「新协议在内、旧契约在边」的方向, 将来
    §8.2 能力协商上线后新消费方直接吃 LoopEvent。
    """
    ...

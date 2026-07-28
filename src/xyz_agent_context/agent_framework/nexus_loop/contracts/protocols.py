"""
@file_name: protocols.py
@author: Bin.Liang
@date: 2026-07-27
@description: 全部组件的 Protocol 接口——「接口一次到位, 实现分期长大」的载体。

每个组件一个 Protocol, 实现可整体替换; loop.py 只 import 本文件与 events.py,
永远不 import 任何具体实现(组件经 LoopAssembly 注入)。扩展 = 换实现或加注册,
永不改签名(07-26 §6.0 原则 1: 加第二个实现时 diff 里不出现对既有类的修改)。

CancellationView 在此以结构化子集重声明(requested() -> bool), 使 L0 保持零
平台依赖; agent_framework.loop.cancellation_view.CancellationView 天然满足它。
"""

from typing import Any, AsyncIterator, Protocol, Sequence, runtime_checkable

from xyz_agent_context.agent_framework.nexus_loop.contracts.errors import LoopError
from xyz_agent_context.agent_framework.nexus_loop.contracts.events import (
    LedgerEntry,
    LoopEvent,
    Usage,
)
from xyz_agent_context.agent_framework.nexus_loop.contracts.model import (
    ModelEvent,
    ModelRequest,
    ProviderMessage,
    ProviderProfile,
)
from xyz_agent_context.agent_framework.nexus_loop.contracts.tooling import (
    Decision,
    PolicyContext,
    ToolCall,
    ToolContext,
    ToolResult,
    ToolSpec,
)


@runtime_checkable
class ModelClient(Protocol):
    """流式模型调用的唯一抽象。

    默认实现 LiteLLMModelClient 包住 llm/litellm_client.py(全仓唯一
    litellm import 点); 透传质量不达标的 provider 才写旁路实现
    (首例: AnthropicDirectClient)。
    """

    profile: ProviderProfile

    def stream_step(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        """发起一次模型调用, 流式产出 ModelEvent。

        实现责任: cache_plan 按 profile.cache_style 翻译成 provider 参数;
        thinking 块回放按 profile.thinking_replay 处理; usage 按
        profile.usage_keys 归一为 contracts.events.Usage 词汇。
        流失败抛原始异常, 分类交给 ErrorClassifier(职责分离)。
        """
        ...


@runtime_checkable
class ToolChannel(Protocol):
    """工具通道——最重要的长期收敛点(07-26 §6.5)。

    v1 两个实现: BuiltinToolset / McpToolChannel。P3/P4 的子代理、
    sleep、update_plan、expand_module 各自作为新 channel 注册进
    dispatcher, loop 与 dispatcher 零改动。
    """

    def list_tools(self) -> list[ToolSpec]:
        """返回本通道当前可见的工具清单(含 description, 单一事实源)。"""
        ...

    async def call(self, name: str, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        """执行一个工具调用。实现内部不做策略裁决(那是 PolicyEngine 的事)。"""
        ...

    async def refresh(self) -> bool:
        """刷新工具清单, 返回是否发生变化。

        MCP 通道响应 tools/list_changed 时整体重建(Hermes nuke-and-repave
        + 代数计数器); 内建通道恒返回 False。expand_module 动态加载
        依赖此缝。
        """
        ...


@runtime_checkable
class ToolExecutor(Protocol):
    """工具分发器抽象(实现: ToolDispatcher)。loop 只认识这个接口。"""

    def visible_tools(self) -> list[ToolSpec]:
        """全部通道工具之和 - disallowed, 确定性排序(cache 前缀稳定要求)。"""
        ...

    async def execute(self, call: ToolCall) -> ToolResult:
        """执行前必须过 PolicyEngine; deny 返回错误型 ToolResult, 不抛异常。"""
        ...


@runtime_checkable
class PolicyLayer(Protocol):
    """一层策略裁决(纯函数)。PolicyEngine 依序全过, 任一 deny 即 deny。"""

    def check(self, call: ToolCall, ctx: PolicyContext) -> Decision:
        """裁决一次调用。实现内部异常 == deny(fail-closed)。"""
        ...


@runtime_checkable
class StopPolicy(Protocol):
    """停止评判的策略缝。v1 = NoMoreActionsStop; P4 换 GoalSpec 实现。"""

    async def should_stop(self, step_calls: Sequence[ToolCall], ledger: "LedgerView") -> bool:
        """在 STOP_CHECK 相位被调用; 返回 True 则回合终止。

        平台语义: 文本是独白, 「不再说话」不构成停止条件——只有
        「不再有动作」才是(与 CC/Codex 的关键分歧, 见 harness/expression)。
        """
        ...


@runtime_checkable
class SteeringInlet(Protocol):
    """步边界插话入口。v1 = NullSteeringInlet(恒空); P4 接 TriggerInbox。"""

    async def drain(self) -> list[ProviderMessage]:
        """在 DRAIN_STEERING 相位排空收件箱, 返回要注入的用户消息。

        注入语义必须是纯追加(不改历史前缀), 否则打穿 prompt cache。
        """
        ...


@runtime_checkable
class EventLogWriter(Protocol):
    """两轨事件日志出口——「落日志是路过, 不是分叉」(loop 每个事件必经)。"""

    async def append(self, event: LoopEvent) -> None:
        """append-only; (thread_id, seq) 幂等。v1 实现 = NDJSON 流式回传
        控制面; P1 换 DB writer(JSONL 为真相源、DB 做索引——Codex
        reconcile 模式)。"""
        ...


@runtime_checkable
class ErrorClassifier(Protocol):
    """provider 原始异常 -> LoopError 的归一点(07-26 A5 契约)。"""

    def classify(self, exc: BaseException) -> LoopError:
        """分类异常。overflow 错误串命中 -> CONTEXT_OVERFLOW(被动压缩信号)。"""
        ...


@runtime_checkable
class RetryPolicy(Protocol):
    """step 级重试策略缝。v1 = NoRetry(错误即整轮结束, 现状行为)。"""

    async def should_retry(self, error: LoopError, attempt: int) -> bool:
        """P3 的 StepRetry: 可重试类错误重试当前 step——历史都在 ledger 里,
        重试只花一个 step 的钱。"""
        ...


@runtime_checkable
class CancellationSignal(Protocol):
    """取消信号的结构化子集——归一三种来源(属性/方法/下游断流)。"""

    def requested(self) -> bool:
        """步边界检查点; True 则走打断路径(合成 tool_result 保配对)。"""
        ...


@runtime_checkable
class LedgerView(Protocol):
    """TurnLedger 的只读视图——给 StopPolicy/ContextProjector 消费,
    不暴露写方法(读写分离, 防策略实现越权改账本)。"""

    def entries(self) -> Sequence[LedgerEntry]:
        """全部账本条目(按 seq 有序)。"""
        ...

    def open_tool_calls(self) -> Sequence[ToolCall]:
        """尚未配对 tool_result 的调用(打断合成的输入)。"""
        ...

    def total_usage(self) -> Usage:
        """累计真实用量(response.done 计费链的唯一数据源)。"""
        ...


@runtime_checkable
class CompactionPolicy(Protocol):
    """压缩策略——v1 即在场(Owner 2026-07-27 拍板: 压缩必须 day-1 有,
    因为 claude_code driver 内部有 auto-compact, 没有它就是能力回退;
    长跑回合撞 context 墙不可接受, 铁律 #14)。"""

    def should_compact(self, ledger: LedgerView, profile: ProviderProfile) -> bool:
        """主动触发判断(接近窗口阈值, 阈值按 profile 窗口表计算)。
        被动触发不走本方法: loop 收到 CONTEXT_OVERFLOW 分类错误时
        直接调 compact 后重试当前 step。"""
        ...

    async def compact(self, ledger: LedgerView, profile: ProviderProfile) -> Sequence[LedgerEntry]:
        """产出 compaction replacement 条目(TYPE_COMPACTION)。

        纪律: 只产出新条目, 不触碰既有条目(append-only); tool_use/result
        配对不许被切开(切分点必须落在配对边界外——OpenClaw 教训);
        head(系统段)与 tail(近期消息)受保护, 只压中间窗。
        """
        ...


@runtime_checkable
class ContextProjector(Protocol):
    """账本 -> 本 step messages 的投影函数。

    v1 = PassthroughProjector(吃物化层现成 messages + 回合内累积);
    压缩(P3)= 换一个 projector 实现 + LedgerEntry 新类型, loop 零改动。
    """

    def project(self, ledger: LedgerView, profile: ProviderProfile) -> list[ProviderMessage]:
        """产出发给模型的完整消息序列(含 per-provider 方言处理)。"""
        ...

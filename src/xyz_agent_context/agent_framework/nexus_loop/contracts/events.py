"""
@file_name: events.py
@author: Bin.Liang
@date: 2026-07-27
@description: 循环内部事件与账本条目的类型定义——两轨制(track=model/ui)是全部
恢复/回放/压缩能力的前提(Codex 两轨制合证, 07-26 文档 C1 约束)。

设计要点:
- model 轨: 用于重建 LLM 上下文的事件(完整 tool_use/tool_result/文本块);
- ui 轨: 只给前端重放的增量(text_delta/thinking_delta/tool_arg_delta),
  不参与上下文重建;
- 「entry 即 schema」: LedgerEntry 与未来 nexus_events 表的行同形, 落库时
  换 writer 而不是改类(pi 的 compaction-as-entry 同款思路)。
"""

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Literal

Track = Literal["model", "ui"]

# LoopEvent.type / LedgerEntry.type 的合法值(与 loop/events.py 遗留契约的
# 对应关系由 LegacyEventAdapter 单点维护, 这里只定义内部词汇)
TYPE_TEXT_DELTA = "text_delta"
TYPE_THINKING_DELTA = "thinking_delta"
TYPE_TOOL_USE = "tool_use"
TYPE_TOOL_ARG_DELTA = "tool_arg_delta"   # ui 轨专用: 流式参数投影(P3 预留)
TYPE_TOOL_RESULT = "tool_result"
TYPE_STEP_DONE = "step_done"
TYPE_TURN_DONE = "turn_done"
TYPE_ERROR = "error"
TYPE_COMPACTION = "compaction"   # replacement 条目: 携带被替换的 seq 区间 +
                                 # 摘要文本 + retained_tail 指针(pi 形状)。
                                 # 压缩是"追加新条目"不是删除——append-only,
                                 # 完整历史永在日志; 投影时用它替代原区间。
                                 # narrative 联动: 平台记忆服务消费此事件,
                                 # 把摘要沉淀进长期记忆(见 modeling/compaction.py)


class Phase(Enum):
    """回合内状态机的五个相位(07-26 §6.2)。

    DRAIN_STEERING 与 STOP_CHECK 的调用点从第一天就在循环里:
    v1 分别挂 NullSteeringInlet(恒空)与 NoMoreActionsStop(无动作即停),
    P4 换实现时 loop.py 一行不改。
    """

    PROJECT = auto()          # 投影本 step 的 messages(cache 断点在此注入)
    MODEL_STREAM = auto()     # 流式模型调用
    DISPATCH = auto()         # 策略审查 + 工具执行
    DRAIN_STEERING = auto()   # 排空插话收件箱(v1 恒空)
    STOP_CHECK = auto()       # 停止评判(v1 = 无动作即停)


class EndReason(Enum):
    """回合终止原因。

    NexusAgent 的平台语义: 文本是内心独白, 触达用户必须走工具——所以正常
    终止是「模型不再调工具」(NO_MORE_ACTIONS), 而不是「模型不再说话」。
    SUSPENDED 是 P4 sleep/暂停的预留, v1 永不产生(测试锁定)。
    """

    NO_MORE_ACTIONS = auto()
    INTERRUPTED = auto()
    ERROR = auto()
    SUSPENDED = auto()


@dataclass(frozen=True)
class Usage:
    """一次/多次模型调用的真实 token 用量(可加合)。

    锚定 provider 返回的真实 usage 而非字符估算(07-26 C3 约束):
    cache_read/cache_creation 两个字段是 cache 命中率仪表盘的数据源。
    """

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0

    def __add__(self, other: "Usage") -> "Usage":
        """逐字段相加, 返回新实例(frozen 语义)。"""
        ...


@dataclass(frozen=True)
class LoopEvent:
    """循环对外产出的类型化事件(LegacyEventAdapter 翻译前的内部形态)。

    (thread_id, seq) 构成幂等键; seq 在 thread 内单调递增, 由 TurnLedger
    统一分配, 任何其他组件不得自造 seq。
    """

    track: Track
    seq: int
    type: str
    payload: dict[str, Any] = field(default_factory=dict)
    usage: "Usage | None" = None


@dataclass(frozen=True)
class LedgerEntry:
    """账本条目——与未来 nexus_events 表的行同形(entry 即 schema)。

    P1 落库时 EventLogWriter 直接持久化 entries(), 本类零改动;
    resume = TurnLedger(turn, base=从日志读出的前缀)。
    """

    seq: int
    track: Track
    type: str
    payload: dict[str, Any] = field(default_factory=dict)
    usage: "Usage | None" = None

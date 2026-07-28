"""
@file_name: compaction.py
@author: Bin.Liang
@date: 2026-07-27
@description: CompactionPolicy 实现——上下文压缩, v1 即在场(Owner 拍板)。

分两梯队, 同一 Protocol, Assembly 换实例即升级:
- ToolResultPruner(v1 默认): 不动 LLM 的确定性裁剪——把陈旧工具结果
  替换为一行占位摘要("[bash] ran npm test -> exit 0, 47 lines"),
  Hermes 第一阶段 / OpenClaw pruning 同款; 零成本、零幻觉风险;
- SummaryCompactor(v1.5): 辅助小模型摘要中间窗(head/tail 保护),
  迭代更新旧 summary 而非推倒重来、防注入包装("REFERENCE ONLY" 前缀 +
  结束 marker)、600s 失败冷却 + 确定性兜底(Hermes 全套纪律,
  模板在 prompts/resources/compaction.md)。

**与 narrative 的强关联(Owner 2026-07-27), 经事件日志解耦实现**:
loop 不 import narrative(铁律: 平台信息只进不出), 联动走两条既有通道:
1. 出方向: compaction 事件(含摘要与被替换区间)落两轨日志 -> 平台的
   memory_consolidation / narrative 服务**消费 compaction 事件**, 把摘要
   与关键事实沉淀进长期记忆——压缩不是丢信息, 是把信息换个地方存;
   (平台侧需新增该消费者, 属 narrative 系统的配套改造)
2. 入方向: 压缩触发前, loop 经 steering 缝注入一条 harness 提醒
   ("即将压缩, 请把需要长期保留的信息写入记忆工具")——agent 用
   GeneralMemoryModule 等 MCP 记忆工具自主落 narrative(OpenClaw
   memory-flush 先例, agent 中介, 零新增依赖)。
重建/下回合时, narrative 事实经物化层 C 层回流进上下文, 闭环成立。

触发双轨: 主动(should_compact, 接近窗口阈值)+ 被动(loop 收到
CONTEXT_OVERFLOW 分类错误 -> compact -> 重试当前 step, overflow 错误串
表在 session/error_classifier.py)。
"""

from typing import Sequence

from xyz_agent_context.agent_framework.nexus_loop.contracts.events import LedgerEntry
from xyz_agent_context.agent_framework.nexus_loop.contracts.model import ProviderProfile
from xyz_agent_context.agent_framework.nexus_loop.contracts.protocols import LedgerView


class ToolResultPruner:
    """v1 默认压缩: 确定性工具结果裁剪, 不调用任何 LLM。"""

    def __init__(self, *, trigger_ratio: float = 0.75, keep_recent_tokens: int = 20_000) -> None:
        """trigger_ratio: 估算用量 / 窗口 超过即触发; keep_recent_tokens:
        尾部保护区(OpenClaw keepRecentTokens 同款默认)。"""
        ...

    def should_compact(self, ledger: LedgerView, profile: ProviderProfile) -> bool:
        """按累计 usage 与 profile 窗口表估算; 纯函数无 IO。"""
        ...

    async def compact(self, ledger: LedgerView, profile: ProviderProfile) -> Sequence[LedgerEntry]:
        """从最老的工具结果开始, 逐条替换为占位摘要(工具名 + 参数要点 +
        结果规模), 直到降回阈值下; 产出 TYPE_COMPACTION replacement 条目。
        配对边界不变量: 占位仍是合法 tool_result, 不破坏配对。"""
        ...


class SummaryCompactor:
    """v1.5: 小模型摘要压缩(可与 ToolResultPruner 级联: 先剪后摘)。"""

    def __init__(self, *, summary_model: str | None = None) -> None:
        """summary_model: 摘要委托模型(None = 复用主模型; OpenClaw 允许
        换便宜模型的先例)。"""
        ...

    def should_compact(self, ledger: LedgerView, profile: ProviderProfile) -> bool:
        """裁剪后仍超阈值才轮到摘要(有损操作是兜底不是首选)。"""
        ...

    async def compact(self, ledger: LedgerView, profile: ProviderProfile) -> Sequence[LedgerEntry]:
        """摘要中间窗: 迭代更新既有 summary; 输出带防注入包装; 失败走
        冷却 + 确定性兜底(截断式占位), 绝不让压缩失败挡住回合。"""
        ...

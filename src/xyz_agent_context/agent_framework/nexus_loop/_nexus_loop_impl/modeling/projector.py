"""
@file_name: projector.py
@author: Bin.Liang
@date: 2026-07-27
@description: ContextProjector 实现——账本 -> 本 step messages 的投影。

v1 = PassthroughProjector: 吃物化层现成的 messages(TurnInput.history +
user_message)+ 回合内账本累积。**压缩 v1 即在场**(Owner 2026-07-27
拍板), 但职责分离: 压缩由 CompactionPolicy 产出 TYPE_COMPACTION 条目
(见 compaction.py), 本投影器只负责**尊重**这些条目——投影时用
replacement 摘要替代被压区间, 其余原样。压缩策略升级(裁剪 -> 摘要),
投影器零改动。
"""

from xyz_agent_context.agent_framework.nexus_loop.contracts.model import (
    ProviderMessage,
    ProviderProfile,
)
from xyz_agent_context.agent_framework.nexus_loop.contracts.protocols import LedgerView


class PassthroughProjector:
    """v1 默认投影: 物化输入 + 账本累积的直接拼接。"""

    def __init__(self, base_messages: list[ProviderMessage]) -> None:
        """base_messages: 物化层拼好的 system+history+user(平台负责其质量,
        loop 不改写——「物化 vs 自投影」的分界线, materializer.py 是
        nexus_loop 不调用的对照组)。"""
        ...

    def project(self, ledger: LedgerView, profile: ProviderProfile) -> list[ProviderMessage]:
        """base + 账本内 model 轨条目按 role 交替规则拼接; 遇
        TYPE_COMPACTION 条目, 用其摘要替代所覆盖的 seq 区间(retained_tail
        原样保留); thinking 块按 profile.thinking_replay 处理
        (keep_signed/strip/as_text)。纯函数语义: 不修改账本。"""
        ...

"""
@file_name: steering.py
@author: Bin.Liang
@date: 2026-07-27
@description: 步边界插话入口实现。DRAIN_STEERING 相位的调用点 day-1 存在,
v1 挂 NullSteeringInlet(恒空); P4 换 TriggerInbox 实现时 loop 零改动。

参照: pi 每次 LLM 调用前 getSteeringMessages(); Codex input_queue 在
turn 间吸收新输入不打断进行中的 loop。注入必须是纯追加语义(不改历史
前缀), 否则打穿 prompt cache——这是 C2 约束对本组件的硬要求。
"""

from xyz_agent_context.agent_framework.nexus_loop.contracts.model import ProviderMessage


class NullSteeringInlet:
    """v1 默认实现: 没有插话来源, 恒返回空。"""

    async def drain(self) -> list[ProviderMessage]:
        """恒返回 []。测试锁定该行为(预留纪律三件套之「无行为锁定」)。"""
        ...

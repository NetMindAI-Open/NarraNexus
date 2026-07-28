"""
@file_name: event_adapter.py
@author: Bin.Liang
@date: 2026-07-27
@description: LegacyEventAdapter——遗留 dict 事件契约的**唯一**翻译点。

灰度共存期的核心(07-26 §5.2 新增项): loop 内部永远是类型化 LoopEvent,
六种遗留形状(text/thinking/tool_call/tool_result/error/response.done)、
tool_name 的 mcp__{server}__{tool} 前缀、response.done 的双词汇 cache
字段——全部收敛在本类。旧消费链(ResponseProcessor/前端/计费/审计)
零改动。将来能力协商(§8.2)上线后, 新事件类型也从这里长出。
本文件是全包唯一允许 import agent_framework.loop.events 遗留常量的实现文件。
"""

from typing import Any

from xyz_agent_context.agent_framework.nexus_loop.contracts.errors import LoopError
from xyz_agent_context.agent_framework.nexus_loop.contracts.events import LoopEvent, Usage


class LegacyEventAdapter:
    """LoopEvent -> 遗留 dict 事件的无状态翻译器(少量跨事件聚合状态)。"""

    def translate(self, event: LoopEvent) -> list[dict[str, Any]]:
        """一个内部事件翻译为零或多个遗留 dict(形状对齐 §2.2 六种,
        金样测试逐字段锁定)。独白标记映射为遗留事件里前端已识别的
        thinking/text 字段组合, 不发明新字段(灰度期纪律)。"""
        ...

    def error(self, error: LoopError) -> dict[str, Any]:
        """分类错误 -> 遗留 error 事件(error_type 枚举值进 payload,
        驱动 fallback/熔断/前端徽章)。"""
        ...

    def done(self, usage: Usage, model: str) -> dict[str, Any]:
        """收尾 response.done 事件——计费链唯一数据源; cache 字段按遗留
        双词汇同时给出(anthropic 系/openai 系两套 key)。任何终止路径
        都必须发且只发一次(driver 的 finally 保证)。"""
        ...

"""
@file_name: hooks.py
@author: Bin.Liang
@date: 2026-07-27
@description: HookRegistry——11 事件枚举 day-1 全定义, fire 点位 day-1 埋齐
(07-26 §6.7)。无监听的 fire 是零成本 no-op, 所以 loop/dispatcher 里的
调用点从第一天就写好, P3 接 hook 时不再动调用方。

失败姿态是注册属性(per-hook 显式声明 open/closed), 不是全局开关:
安全类 hook 注册为 closed(hook 失败 == 拦截), 观测类注册为 open。
"""

from enum import Enum, auto
from typing import Any, Awaitable, Callable, Literal


class HookEvent(Enum):
    """生命周期事件全集(对齐 Codex 11 事件清单)。"""

    PRE_TOOL_USE = auto()
    POST_TOOL_USE = auto()
    SESSION_START = auto()
    SESSION_END = auto()
    PRE_COMPACT = auto()
    POST_COMPACT = auto()
    SUBAGENT_START = auto()
    SUBAGENT_STOP = auto()
    USER_PROMPT_SUBMIT = auto()
    PERMISSION_REQUEST = auto()
    STOP = auto()


class HookOutcome:
    """一次 fire 的聚合结果(是否放行、修改建议、诊断信息)。"""

    ...


class HookRegistry:
    """hook 注册与触发的唯一入口。"""

    @classmethod
    def empty(cls) -> "HookRegistry":
        """v1 默认: 空注册表(Assembly 的 default_factory)。"""
        ...

    def on(
        self,
        event: HookEvent,
        fn: Callable[..., Awaitable[Any]],
        *,
        failure: Literal["open", "closed"],
    ) -> None:
        """注册监听。failure 声明该 hook 抛异常时的姿态:
        "closed" -> 视为拦截(安全类); "open" -> 记日志放行(观测类)。"""
        ...

    async def fire(self, event: HookEvent, payload: dict[str, Any]) -> HookOutcome:
        """触发一个事件点。无监听时零成本直接返回放行 outcome。"""
        ...

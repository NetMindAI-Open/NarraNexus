"""
@file_name: nexus_agent.py
@author: Bin.Liang
@date: 2026-07-27
@description: NexusAgent driver——nexus_loop 框架的 AgentLoopDriver 薄接口
(与 adapters/claude/sdk.py 同构: adapter 层只做契约翻译, 零业务逻辑)。

职责仅三件:
1. 遗留签名 (messages, mcp_servers, streaming, extra_env, cancellation,
   **kwargs) -> nexus_loop.assembly.TurnRequest;
2. cancellation 归一为 CancellationSignal(三种来源: is_cancelled 属性 /
   is_set() 方法 / 下游断流);
3. run_turn_events 的 LoopEvent 流经 LegacyEventAdapter 翻译成 §2.2 六种
   遗留 dict 产出; 任何路径 finally 必发 response.done(计费链唯一数据源)。

注册(实现完成后启用): agent_framework/__init__.py 加一行
register_agent_loop_driver("nexus_loop", NexusAgent)。
"""

from typing import Any, AsyncGenerator

from xyz_agent_context.utils.logging import timed


class NexusAgent:
    """AgentLoopDriver 实现——装配组件、翻译事件, 自身零业务逻辑。"""

    def __init__(self, working_path: str = "./"):
        """working_path: workspace 根(进 TurnRequest.workspace)。"""
        self.working_path = working_path

    def capabilities(self) -> set[str]:
        """能力声明清单, 随实现分期增长(声明与实现同一 commit, 契约测试
        锁定「空集直到实现」): P2 {event_log} -> P3 {arg_streaming,
        interrupt_soft} -> P4 {steering, resume, fork, sleep,
        subagent_announce}。v1 返回空集。"""
        return set()

    @timed("llm.nexus.agent_loop", slow_threshold_ms=15000)
    async def agent_loop(
        self,
        messages: list[dict[str, Any]],
        mcp_servers: dict[str, dict[str, Any]],  # {name: {"url": str, "headers": {str: str}?}}
        *,
        streaming: bool = True,
        extra_env: dict[str, str] | None = None,
        cancellation: Any | None = None,
        **kwargs: Any,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """执行一个回合, 产出遗留 dict 事件流(AgentLoopDriver 契约)。

        实现要点(声明期备忘):
        - disallowed_tools 从 kwargs 提取且必须生效(A1: codex driver
          del kwargs 丢弃它是既往事故, 不能学);
        - expressive_tools / expansion_catalog 等平台数据同样经 kwargs
          进入, 翻译进 TurnRequest——本文件不 import 平台模块;
        - try/except -> adapter.error(分类后); finally -> adapter.done。
        """
        ...

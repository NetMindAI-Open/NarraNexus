"""
@file_name: dispatcher.py
@author: Bin.Liang
@date: 2026-07-27
@description: ToolDispatcher——全部能力通道的唯一分发器(ToolExecutor 实现)。

不变量:
- visible_tools() = Σ channel.list_tools() − disallowed, 确定性排序
  (order_tools), 新通道/新工具只尾部追加(C2 cache 约束);
- 每次 execute 先过 PolicyEngine(fail-closed), deny 产出错误型
  ToolResult 而非异常;
- PRE_TOOL_USE / POST_TOOL_USE hook 点位 day-1 埋好(无监听零成本);
- 「模型可见工具 ≡ 实际注册工具」一致性由本类单点保证(OpenClaw 教训:
  防 prompt 与注册表漂移)。
"""

from xyz_agent_context.agent_framework.nexus_loop.contracts.protocols import (
    PolicyLayer,
    ToolChannel,
)
from xyz_agent_context.agent_framework.nexus_loop.contracts.tooling import (
    ToolCall,
    ToolContext,
    ToolResult,
    ToolSpec,
)


class ToolDispatcher:
    """channel 注册表 + 策略检查点 + 执行编排。"""

    def __init__(
        self,
        channels: tuple[ToolChannel, ...],
        policy_layers: tuple[PolicyLayer, ...],
        ctx: ToolContext,
        disallowed_tools: frozenset[str],
    ) -> None:
        """channels 顺序即工具清单的段顺序(cache 稳定的一部分)。"""
        ...

    def visible_tools(self) -> list[ToolSpec]:
        """聚合全通道工具, 剔除 disallowed(07-26 A1: disallowed_tools 必须
        生效——codex driver 把它 del kwargs 扔掉是既往事故), 确定性排序。
        带代数缓存: 任一通道 refresh 变化后失效重建(Hermes _generation)。"""
        ...

    async def execute(self, call: ToolCall) -> ToolResult:
        """单个调用的完整生命周期: policy 裁决 -> 路由到所属 channel ->
        执行 -> 结果归一。异常收敛为错误型 ToolResult, 永不穿透 loop。"""
        ...

    async def execute_step(self, calls: list[ToolCall]) -> list[ToolResult]:
        """一个 step 的批量执行: 按注解分类——read_only 工具并行
        (asyncio TaskGroup), 写类串行(Codex Auto 档语义); v1 可先全串行,
        接口不变。"""
        ...

    async def add_channel(self, channel: ToolChannel) -> None:
        """运行中追加新通道——expand_module 动态加载的落点。
        只允许尾部追加(不重排既有段), 追加后使 visible_tools 缓存失效,
        并记 cache 击穿计数(埋点, 观测动态加载的 cache 代价)。"""
        ...

    async def refresh_channels(self) -> None:
        """轮询/事件驱动地刷新各通道(MCP tools/list_changed 等),
        任何变化使缓存失效。"""
        ...

"""
@file_name: tooling.py
@author: Bin.Liang
@date: 2026-07-27
@description: 工具面契约——ToolSpec/ToolCall/ToolResult 与注解、策略裁决类型。

设计要点:
- 工具的 description 跟 Tool 定义走(单一事实源, OpenClaw 教训: 防止
  「模型看到的 != 实际注册的」漂移), prompts/ 只集中放行为指南;
- ToolAnnotations.expressive 是本框架独有语义的结构化落点: 标记「这个
  工具是对外表达通道」——文本是独白, 表达必须经 expressive 工具;
- streamable_fields 是 P3 流式参数投影的声明位, v1 无消费者(测试锁定)。
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


@dataclass(frozen=True)
class ToolAnnotations:
    """工具行为注解, 驱动分发策略(读并行/写串行)与呈现层能力。"""

    read_only: bool = False            # 只读工具可并行执行(Codex Auto 档语义)
    destructive: bool = False          # 危险写类: E3 可拦截预告的候选
    expressive: bool = False           # 对外表达通道(独白/表达契约的结构位)
    streamable_fields: tuple[str, ...] = ()   # P3: 参数字段 -> ui 轨流式投影


@dataclass(frozen=True)
class ToolSpec:
    """一个工具的完整声明——description 在此, 与工具实现同源同文件。

    MCP 工具名保留 mcp__{server}__{tool} 命名(下游三处子串匹配依赖它,
    07-26 A2 契约), 内建工具用裸名(read_file/bash/...)。
    """

    name: str
    description: str
    input_schema: dict[str, Any]
    annotations: ToolAnnotations = field(default_factory=ToolAnnotations)


@dataclass(frozen=True)
class ToolCall:
    """模型发起的一次工具调用(参数已完整)。"""

    id: str
    name: str
    args: dict[str, Any]


@dataclass(frozen=True)
class ToolResult:
    """工具执行结果。deny/异常也走这里(错误型 result), 不抛异常穿透 loop。"""

    call_id: str
    ok: bool
    content: Any = None
    error: str | None = None
    is_synthetic: bool = False         # 打断合成的 tool_result(配对不变量)


@dataclass(frozen=True)
class ToolContext:
    """一次工具调用的执行上下文(由装配层注入, 工具实现只读)。"""

    agent_id: str
    workspace: str
    extra_env: dict[str, str] = field(default_factory=dict)


class PolicyVerdict(Enum):
    """策略裁决结果。deny 永远赢; layer 内部异常 == DENY(fail-closed,
    多租户自研决策, 无业界背书——Codex fail-open 是反面教材)。"""

    ALLOW = "allow"
    DENY = "deny"


@dataclass(frozen=True)
class Decision:
    """一次裁决及其理由(理由进 tool_result 与审计日志)。"""

    verdict: PolicyVerdict
    reason: str = ""


@dataclass(frozen=True)
class PolicyContext:
    """策略层可见的裁决上下文。"""

    tool_ctx: ToolContext
    disallowed_tools: frozenset[str] = frozenset()

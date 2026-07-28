"""
@file_name: policy.py
@author: Bin.Liang
@date: 2026-07-27
@description: PolicyEngine——有序 layer 列表, deny 永远赢, fail-closed。

fail-closed 是自研决策(无业界背书: Codex fail-open、OpenClaw 空 allowlist
放行), 多租户云端的红线。v1 两个 layer; P3+ 追加 PlatformDenySetLayer
(不可放宽红线, 改动只能走代码 PR)、RepetitionLayer((tool, sha256(args))
熔断, 默认只告警——不是轮次上限, 铁律 #14)、PerAgentPolicyLayer。
子代理策略传播 = 把 engine 实例传给 SubagentChannel(引用即继承交集)。
"""

from xyz_agent_context.agent_framework.nexus_loop.contracts.tooling import (
    Decision,
    PolicyContext,
    ToolCall,
)


class PolicyEngine:
    """依序执行全部 layer; 任一 deny 即 deny; layer 抛异常 == deny。"""

    def __init__(self, layers: tuple) -> None:
        """layers: PolicyLayer 有序元组(顺序影响 deny 理由的归因, 不影响结果)。"""
        ...

    def check(self, call: ToolCall, ctx: PolicyContext) -> Decision:
        """聚合裁决(纯函数, 无 IO——可 hook 的异步审批走 HookRegistry
        的 PERMISSION_REQUEST 事件, 不在这里阻塞)。"""
        ...


class DisallowedToolsLayer:
    """A1 契约层: driver 入参 disallowed_tools 的强制执行点。"""

    def check(self, call: ToolCall, ctx: PolicyContext) -> Decision:
        """call.name ∈ ctx.disallowed_tools -> DENY。"""
        ...


class WorkspaceConfinementLayer:
    """workspace 约束层(现有 _tool_policy_guard 的等价替代)。

    自研后「hook 不传播进 subagent」的盲区自然消失——engine 引用直接
    传给子代理通道。
    """

    def check(self, call: ToolCall, ctx: PolicyContext) -> Decision:
        """文件/shell 类调用的路径参数必须落在 ctx.tool_ctx.workspace 内;
        逃逸尝试 -> DENY(理由写明路径), 不做静默改写。"""
        ...

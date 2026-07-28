"""
@file_name: __init__.py
@author: Bin.Liang
@date: 2026-07-27
@description: 内建 function tools 通道(BuiltinToolset, ToolChannel 实现)。

划界原则(Owner 2026-07-27 修订, 取代 07-22 §5.14D 的"表达族内置"方案):
**内建工具 = 思考原语**——只服务 agent 的自我思考与动手能力:
- files.py         文件六件套: read_file/write_file/edit_file/glob/grep/ls
- shell.py         bash / bash_background / process
- web.py           web_search / web_fetch
- context_tools.py view_image / context_status / expand_module

**对外表达不是 basic 功能。** 向用户/渠道发声依赖平台 module 赋予的工具
(chat_module 等, 经 MCP 通道进入)。loop 自身不拥有任何用户触达通道——
框架因此独立于"有没有用户"这个概念: 没接渠道 module 的 agent 只是
"失声", 不是"残缺"。哪些工具算表达工具, 由平台经
TurnRequest.expressive_tools 名单注入(见 harness/expression.py)。

注册机制(三家合成): feature-gated 在场性(云端/桌面/policy/用户配置,
铁律 #7 双模式一致)+ check_fn 前置探针(前置不满足从 schema 消失,
30s TTL + 失败抖动宽限, Hermes)+ Direct/Deferred 曝光档(Codex)。
"""

from xyz_agent_context.agent_framework.nexus_loop.contracts.tooling import (
    ToolContext,
    ToolResult,
    ToolSpec,
)


class BuiltinToolset:
    """全部内建工具的聚合通道。"""

    def __init__(self, ctx: ToolContext, *, enabled_groups: frozenset[str]) -> None:
        """enabled_groups: feature-gate 的结果(装配层算好传入),
        如 {"files", "shell", "web"}——v1 最小面 = files + bash(B4)。"""
        ...

    def list_tools(self) -> list[ToolSpec]:
        """在场工具清单: enabled_groups 过滤 + check_fn 探针过滤(带 TTL
        缓存)+ 确定性排序。description 与 handler 同文件维护。"""
        ...

    async def call(self, name: str, args: dict, ctx: ToolContext) -> ToolResult:
        """路由到对应 handler 执行; 结果尺寸超限时截断并标注(防止单个
        工具结果撑爆上下文, 上限进配置)。"""
        ...

    async def refresh(self) -> bool:
        """重跑 check_fn 探针(探针结果变化即 True)。"""
        ...

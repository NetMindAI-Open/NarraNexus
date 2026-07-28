"""
@file_name: shell.py
@author: Bin.Liang
@date: 2026-07-27
@description: 执行三件套——bash / bash_background / process。

v1 只上 bash(前台, 超时可配); bash_background 与 process(列/读输出/杀)
在 P3 随 ShellSessionManager(进程登记簿)引入——接口与 spec 声明 day-1
在场(Owner 拍板: 能力接口一个不缺), handler 未启用时经 check_fn 从
schema 消失, 而不是注册了不干活(schema 诚实纪律, Codex disallowed
反例)。参照: Codex unified_exec PTY 持久会话 / Hermes 多后端 terminal。
executor 容器即天然沙箱(workspace 挂载、无平台密钥), 不做 seatbelt。
"""

from xyz_agent_context.agent_framework.nexus_loop.contracts.tooling import (
    ToolContext,
    ToolResult,
    ToolSpec,
)


def specs() -> list[ToolSpec]:
    """bash(destructive=True)/bash_background/process 的 spec 清单。"""
    ...


async def bash(args: dict, ctx: ToolContext) -> ToolResult:
    """前台执行 shell 命令(cwd=workspace, env 叠加 ctx.extra_env,
    输出尺寸截断保护, 超时收敛进程树——孤儿进程是既往事故类型)。"""
    ...


async def bash_background(args: dict, ctx: ToolContext) -> ToolResult:
    """P3: 后台执行, 返回进程句柄; 登记进 ShellSessionManager
    (BuiltinToolset 内部的进程登记簿, 07-26 §6.5 归宿表)。"""
    ...


async def process(args: dict, ctx: ToolContext) -> ToolResult:
    """P3: 后台进程管理——list/read_output/kill 三个动作一个工具
    (Codex unified_exec 形状)。"""
    ...

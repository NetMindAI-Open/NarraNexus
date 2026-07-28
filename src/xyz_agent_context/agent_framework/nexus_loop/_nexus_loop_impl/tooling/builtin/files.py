"""
@file_name: files.py
@author: Bin.Liang
@date: 2026-07-27
@description: 文件六件套——read_file/write_file/edit_file/glob/grep/ls。
对标 Claude Code 六件套的完成度(用户体验基准线); edit 用精确替换语义。
每个工具 = spec(含 description, 单一事实源)+ async handler, 同文件成对。
全部路径参数经 WorkspaceConfinementLayer 裁决后才会到达 handler。
"""

from xyz_agent_context.agent_framework.nexus_loop.contracts.tooling import (
    ToolContext,
    ToolResult,
    ToolSpec,
)


def specs() -> list[ToolSpec]:
    """六件套的 ToolSpec 清单(read_only 注解: read_file/glob/grep/ls 为
    True——并行执行的依据; write/edit 为 False)。description 在此定稿,
    prompt 的工具指南段引用而不复制。"""
    ...


async def read_file(args: dict, ctx: ToolContext) -> ToolResult:
    """读文件(支持 offset/limit 分页; 二进制/超大文件保护)。"""
    ...


async def write_file(args: dict, ctx: ToolContext) -> ToolResult:
    """写文件(整文件覆盖; 目标目录不存在时创建)。"""
    ...


async def edit_file(args: dict, ctx: ToolContext) -> ToolResult:
    """精确字符串替换(old 必须唯一命中, 否则报错要求更长锚点——CC 语义)。"""
    ...


async def glob_files(args: dict, ctx: ToolContext) -> ToolResult:
    """按 glob 模式列文件(按修改时间排序)。"""
    ...


async def grep_files(args: dict, ctx: ToolContext) -> ToolResult:
    """正则内容检索(文件类型过滤/上下文行/命中数上限)。"""
    ...


async def list_dir(args: dict, ctx: ToolContext) -> ToolResult:
    """列目录(带类型/大小标注)。"""
    ...

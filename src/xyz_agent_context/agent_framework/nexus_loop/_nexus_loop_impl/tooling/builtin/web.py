"""
@file_name: web.py
@author: Bin.Liang
@date: 2026-07-27
@description: Web 二件套——web_search / web_fetch(薄封装复用现有 Brave
provider 与抓取栈, 经 HTTP/配置注入, 不 import 平台包)。

结果一律过不可信内容包裹(Hermes 纪律: untrusted 标记 + 中和内嵌分隔符),
与 MCP 结果同一套包裹实现(放 mcp_channel 还是上提 contracts 工具函数,
实现期定)。P3 组。
"""

from xyz_agent_context.agent_framework.nexus_loop.contracts.tooling import (
    ToolContext,
    ToolResult,
    ToolSpec,
)


def specs() -> list[ToolSpec]:
    """web_search / web_fetch 的 spec(read_only=True, 可并行)。"""
    ...


async def web_search(args: dict, ctx: ToolContext) -> ToolResult:
    """关键词搜索, 返回标题+摘要+URL 列表(数量上限进配置)。"""
    ...


async def web_fetch(args: dict, ctx: ToolContext) -> ToolResult:
    """抓取 URL 正文并转可读文本(尺寸截断 + untrusted 包裹)。"""
    ...

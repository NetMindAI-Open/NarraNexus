"""
@file_name: mcp_executor.py
@date: 2025-12-16
@author: NetMind.AI
We can use this script to execute the mcp tools independently.
"""

from urllib.parse import urlparse

from loguru import logger

from mcp.client.session import ClientSession
from mcp.client.sse import sse_client
from mcp.client.streamable_http import streamablehttp_client
from mcp.client.websocket import websocket_client
from mcp import types

from xyz_agent_context.utils.logging import redact, timed


# Max chars from a tool's stringified args / result that we put into a
# log line. Larger payloads are summarised by length only — the full
# bodies belong in TRACE / DEBUG, not INFO.
_PREVIEW_LIMIT = 200


def _get_mcp_client(mcp_server_url: str):
    """
    Automatically select the appropriate MCP client based on URL.

    Selection logic:
    - ws:// or wss:// -> WebSocket transport
    - http(s):// with /sse in path -> SSE transport
    - http(s):// other cases -> Streamable HTTP transport

    Args:
        mcp_server_url: URL of the MCP server

    Returns:
        The corresponding client context manager
    """
    parsed = urlparse(mcp_server_url)

    if parsed.scheme in ("ws", "wss"):
        # WebSocket transport
        return websocket_client(mcp_server_url)
    elif parsed.scheme in ("http", "https"):
        # Check if path is an SSE endpoint
        if "/sse" in parsed.path:
            return sse_client(mcp_server_url)
        else:
            return streamablehttp_client(mcp_server_url)
    else:
        raise ValueError(f"Unsupported URL scheme: {mcp_server_url}, please use http(s):// or ws(s)://")


async def mcp_tool_executor(mcp_server_url: str, mcp_tool_name: str, args: dict) -> str:
    """
    Execute an MCP tool independently.

    Automatically selects transport method based on URL:
    - ws:// or wss:// uses WebSocket transport
    - http(s):// with /sse in path uses SSE transport
    - http(s):// other cases use Streamable HTTP transport

    Args:
        mcp_server_url: URL of the MCP server
        mcp_tool_name: Name of the tool to execute
        args: Tool parameter dictionary

    Returns:
        Text content of the tool execution result

    Raises:
        ValueError: When the tool name does not exist or URL scheme is unsupported
        ConnectionError: When unable to connect to the MCP server
    """
    # Pre-call observability: full args body at DEBUG (with redaction),
    # one-line summary at INFO. The summary is what an operator scanning
    # the log file will see by default; the body is only there when DEBUG
    # is active.
    safe_args = redact(args) if isinstance(args, dict) else args
    args_repr = repr(safe_args)
    logger.info(
        "mcp.call tool={tool} url={url} args_size={size}",
        tool=mcp_tool_name,
        url=mcp_server_url,
        size=len(args_repr),
    )
    # The args body is at DEBUG; loguru itself filters on sink level so
    # the `repr` cost is acceptable but the redacted full text only ever
    # reaches a sink configured to accept DEBUG.
    logger.debug(
        "mcp.call.args tool={tool} args={args_preview}",
        tool=mcp_tool_name,
        args_preview=args_repr[:_PREVIEW_LIMIT],
    )

    with timed(f"mcp.{mcp_tool_name}", slow_threshold_ms=2000):
        client_context = _get_mcp_client(mcp_server_url)
        async with client_context as (read_stream, write_stream, *_):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()

                tools_response = await session.list_tools()
                available_tools = {tool.name: tool for tool in tools_response.tools}

                if mcp_tool_name not in available_tools:
                    available_names = list(available_tools.keys())
                    raise ValueError(
                        f"Tool '{mcp_tool_name}' does not exist. "
                        f"Available tools: {available_names}"
                    )

                result = await session.call_tool(mcp_tool_name, arguments=args)

                if result.content:
                    text_parts: list[str] = []
                    for content_block in result.content:
                        if isinstance(content_block, types.TextContent):
                            text_parts.append(content_block.text)
                        elif isinstance(content_block, types.ImageContent):
                            text_parts.append(f"[Image: {content_block.mimeType}]")
                        elif isinstance(content_block, types.EmbeddedResource):
                            text_parts.append(
                                f"[Embedded resource: {content_block.resource}]"
                            )
                    payload = "\n".join(text_parts) if text_parts else ""
                    logger.debug(
                        "mcp.call.result tool={tool} result_size={size} preview={preview}",
                        tool=mcp_tool_name,
                        size=len(payload),
                        preview=payload[:_PREVIEW_LIMIT],
                    )
                    return payload

                # Fallback: non-content result; preserve old behavior of
                # returning the raw result object so existing callers do
                # not break.
                return result  # type: ignore[return-value]


async def list_mcp_tools(mcp_server_url: str) -> list[dict]:
    """
    List all available tools on the MCP server.

    Args:
        mcp_server_url: URL of the MCP server

    Returns:
        List of tool information, each containing name, description, and inputSchema fields
    """
    client_context = _get_mcp_client(mcp_server_url)

    async with client_context as (read_stream, write_stream, *_):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            tools_response = await session.list_tools()

            return [
                {
                    "name": tool.name,
                    "description": tool.description,
                    "inputSchema": tool.inputSchema
                }
                for tool in tools_response.tools
            ]


# unit test 
async def main():
    mcp_server_url = "http://127.0.0.1:7804/sse"
    mcp_tool_name = "agent_send_content_to_agent_inbox"
    args = {
        "target_agent_id": "agent_a483bf7b",
        "content": "Good morning",
        "self_agent_id": "agent_ecb12faf"
    }
    result = await mcp_tool_executor(mcp_server_url, mcp_tool_name, args)
    logger.info(result)

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())

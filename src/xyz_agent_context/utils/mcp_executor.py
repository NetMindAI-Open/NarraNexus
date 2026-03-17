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
    # 1. Select appropriate transport method based on URL
    client_context = _get_mcp_client(mcp_server_url)

    # 2. Connect to MCP server and execute tool
    async with client_context as (read_stream, write_stream, *_):
        async with ClientSession(read_stream, write_stream) as session:
            # Initialize connection
            await session.initialize()

            # 3. Get available tools list and verify tool exists
            tools_response = await session.list_tools()
            available_tools = {tool.name: tool for tool in tools_response.tools}

            if mcp_tool_name not in available_tools:
                available_names = list(available_tools.keys())
                raise ValueError(
                    f"Tool '{mcp_tool_name}' does not exist. Available tools: {available_names}"
                )

            # 4. Call the specified tool
            result = await session.call_tool(mcp_tool_name, arguments=args)

            # 5. Parse and return results
            if result.content:
                # Extract text content
                text_parts = []
                for content_block in result.content:
                    if isinstance(content_block, types.TextContent):
                        text_parts.append(content_block.text)
                    elif isinstance(content_block, types.ImageContent):
                        text_parts.append(f"[Image: {content_block.mimeType}]")
                    elif isinstance(content_block, types.EmbeddedResource):
                        text_parts.append(f"[Embedded resource: {content_block.resource}]")
                return "\n".join(text_parts) if text_parts else ""

            return result


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

"""
@file_name: _chat_mcp_tools.py
@author: Bin Liang
@date: 2026-03-06
@description: ChatModule MCP Server tool definitions

Separates MCP tool registration logic from ChatModule main class,
keeping the module focused on Hook lifecycle and memory management.

Tools:
- send_message_to_user_directly: Agent speaks to user (the ONLY way to deliver messages)
- get_chat_history: Get chat history for a Chat Instance
"""

import json

from loguru import logger
from mcp.server.fastmcp import FastMCP

def create_chat_mcp_server(port: int, get_db_client_fn) -> FastMCP:
    """
    Create a ChatModule MCP Server instance

    Args:
        port: MCP Server port
        get_db_client_fn: Async function to get database connection (ChatModule.get_mcp_db_client)

    Returns:
        FastMCP instance with all tools configured
    """
    mcp = FastMCP("chat_module")
    mcp.settings.port = port

    @mcp.tool()
    async def get_chat_history(
        instance_id: str,
        limit: int = 20
    ) -> dict:
        """
        Get chat history for a specified Chat Instance.

        Each user has an independent Chat Instance within a Narrative, used to store that user's conversation history with the Agent.
        When a sales manager asks about interactions with a specific customer, this tool can be used to get that customer's complete chat history.
        Returned messages are sorted chronologically and contain both user and Agent conversation content.

        Args:
            instance_id: Chat Instance ID (format: chat_xxxxxxxx), used to locate a specific user's conversation
            limit: Maximum number of messages to return, default 20. Set to -1 to return all

        Returns:
            dict: Dictionary containing chat history, format:
            {
                "success": True/False,
                "instance_id": "chat_xxx",
                "total_messages": 10,
                "messages": [
                    {"role": "user", "content": "...", "timestamp": "..."},
                    {"role": "assistant", "content": "...", "timestamp": "..."},
                    ...
                ]
            }

        Example:
            # Get conversation history with customer Alice
            # Assuming Alice's Chat Instance ID is "chat_abc12345"
            get_chat_history(
                instance_id="chat_abc12345",
                limit=10
            )
        """
        db = await get_db_client_fn()

        table_name = "instance_json_format_memory_chat"

        # Check if table exists
        check_query = """
            SELECT COUNT(*) as cnt
            FROM information_schema.tables
            WHERE table_schema = DATABASE()
            AND table_name = %s
        """
        result = await db.execute(check_query, params=(table_name,), fetch=True)
        table_exists = result and len(result) > 0 and result[0].get("cnt", 0) > 0

        if not table_exists:
            return {
                "success": False,
                "instance_id": instance_id,
                "error": f"Chat history table {table_name} does not exist",
                "total_messages": 0,
                "messages": []
            }

        query = f"""
            SELECT `memory` FROM `{table_name}`
            WHERE `instance_id` = %s
        """

        try:
            result = await db.execute(query, params=(instance_id,), fetch=True)

            if not result or len(result) == 0 or not result[0].get("memory"):
                return {
                    "success": True,
                    "instance_id": instance_id,
                    "total_messages": 0,
                    "messages": [],
                    "note": "This Chat Instance has no chat history yet"
                }

            memory_str = result[0]["memory"]
            memory_data = json.loads(memory_str)
            messages = memory_data.get("messages", [])

            total_messages = len(messages)
            if limit > 0 and total_messages > limit:
                messages = messages[-limit:]

            formatted_messages = []
            for msg in messages:
                formatted_msg = {
                    "role": msg.get("role", "unknown"),
                    "content": msg.get("content", ""),
                }
                if "meta_data" in msg:
                    meta = msg["meta_data"]
                    if "timestamp" in meta:
                        formatted_msg["timestamp"] = meta["timestamp"]
                    if "event_id" in meta:
                        formatted_msg["event_id"] = meta["event_id"]

                formatted_messages.append(formatted_msg)

            return {
                "success": True,
                "instance_id": instance_id,
                "total_messages": total_messages,
                "returned_messages": len(formatted_messages),
                "messages": formatted_messages
            }

        except json.JSONDecodeError as e:
            logger.exception(f"ChatModule.get_chat_history: JSON parsing failed - {e}")
            return {
                "success": False,
                "instance_id": instance_id,
                "error": f"Chat history data format error: {str(e)}",
                "total_messages": 0,
                "messages": []
            }
        except Exception as e:
            logger.exception(f"ChatModule.get_chat_history: Query failed - {e}")
            return {
                "success": False,
                "instance_id": instance_id,
                "error": f"Query failed: {str(e)}",
                "total_messages": 0,
                "messages": []
            }

    @mcp.tool()
    async def send_message_to_user_directly(agent_id: str, user_id: str, content: str) -> dict:
        """
        Speak to the user - This is the ONLY way to deliver your response to the user.

        **CRITICAL**: Think of this as "opening your mouth to speak". All your internal reasoning,
        tool calls, and agent loop outputs are like thoughts in your mind - completely invisible
        to the user. The user ONLY sees what you say through this tool.

        Analogy: Imagine you and the user are face-to-face:
        - Your LLM reasoning = thinking in your head (user cannot hear)
        - Your tool calls = actions you take silently (user cannot see)
        - Calling this tool = opening your mouth to speak (user CAN hear)

        Without calling this tool, your response stays in your head - the user receives NOTHING!

        Args:
            agent_id: Your agent ID (the speaker).
            user_id: The user ID you are speaking to (the listener).
            content: What you want to say to the user. This is the actual message
                     the user will see. Make it clear, helpful, and appropriate.
                     Which is in markdown format.

        Returns:
            A confirmation dict indicating the response was delivered successfully.

        Example:
            # After thinking and gathering information, speak to the user:
            send_message_to_user_directly(
                agent_id="agent_123",
                user_id="user_456",
                content="Based on my analysis, here are the results you requested..."
            )
        """
        return {
            "success": True,
            "message": "Response delivered to user successfully",
            "user_id": user_id,
            "agent_id": agent_id,
            "content": content
        }

    return mcp

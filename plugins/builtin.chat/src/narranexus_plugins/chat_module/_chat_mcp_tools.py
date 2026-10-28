"""
@file_name: _chat_mcp_tools.py
@author: Bin Liang
@date: 2026-03-06
@description: ChatModule MCP Server tool definitions

Separates MCP tool registration logic from ChatModule main class,
keeping the module focused on Hook lifecycle and memory management.

Tools:
- reply_owner / notify_owner: the two registers of speaking to the owner
  (one on the desk per turn — see ChatModule.expressive_tools)
- get_chat_history: Get chat history for a Chat Instance
"""

from mcp.server.fastmcp import FastMCP

def create_chat_mcp_server() -> FastMCP:
    """
    Create a ChatModule MCP Server instance

    Args:

    Returns:
        FastMCP instance with all tools configured

    (No db-client function is needed anymore: get_chat_history routes through the
    AgentDataStore seam, which resolves its own db, and the owner-facing tools
    never touch the db.)
    """
    mcp = FastMCP("chat_module")

    @mcp.tool()
    async def get_chat_history(
        agent_id: str,
        instance_id: str,
        limit: int = 20
    ) -> dict:
        """
        Get chat history for a specified Chat Instance.

        Each user has an independent Chat Instance within a Narrative, used to store that user's conversation history with the Agent.
        When a sales manager asks about interactions with a specific customer, this tool can be used to get that customer's complete chat history.
        Returned messages are sorted chronologically and contain both user and Agent conversation content.

        Args:
            agent_id: Your agent ID (the owner of the Chat Instance). The instance
                must belong to you — another agent's instance returns empty history.
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
                agent_id="agent_123",
                instance_id="chat_abc12345",
                limit=10
            )
        """
        # Route through the AgentDataStore seam: DirectStore (local, unchanged
        # DB access) or HttpStore (cloud, backend API — no db creds in mcp),
        # chosen by NARRANEXUS_BACKEND_URL. The de-rawed, instance-scoped body
        # is the shared fetch_chat_history (_chat_reads), so this path and the
        # backend twin route stay byte-identical.
        from narranexus.platform.module_system.data_access import get_agent_data_store
        return await get_agent_data_store().get_chat_history(agent_id, instance_id, limit)

    # TWO tools, one destination — and that is the point.
    #
    # `send_message_to_user_directly` carried two OPPOSITE disciplines on one
    # name: ChatModule says answering your owner is expected on almost every
    # chat turn, while channel_prompts says notifying your owner from an IM turn
    # is something you do only for (a) an explicit mention, (b) a decision that
    # needs them, (c) information they track. One "you should", one "you should
    # not", distinguished by prose the model had to apply correctly.
    #
    # Split, each name carries its own discipline — and they never appear
    # together: the owner-chat turn is given `reply_owner` only, every other
    # turn `notify_owner` only (see ChatModule.expressive_tools /
    # disallowed_tools). So there is no choice to get wrong, and the rule
    # that applies is the one attached to the tool that is actually on the desk.
    #
    # Both are no-ops that return a confirmation. Delivery is not what they DO —
    # the platform detects the call in the turn's trace
    # (`MessageSourceHandler.extract_reply_text`) and routes the content. What
    # they carry is intent.

    @mcp.tool()
    async def reply_owner(agent_id: str, user_id: str, content: str) -> dict:
        """
        Answer your owner. They asked you something and are waiting.

        Your plain text is working narration — the owner never receives it as a
        message. This call is what reaches them.

        This is the expected way an owner-chat turn ends: a final answer, a
        summary of what you did, a clarifying question, or an explanation of why
        you cannot do what was asked. Silence is a deliberate exception (they
        said "ok" and clearly want nothing back), not a default.

        Args:
            agent_id: your own agent id
            user_id: the owner you are answering
            content: what to say, in markdown

        Returns:
            A confirmation that the answer was delivered.
        """
        return {
            "success": True,
            "message": "Reply delivered to owner",
            "user_id": user_id,
            "agent_id": agent_id,
            "content": content,
        }

    @mcp.tool()
    async def notify_owner(agent_id: str, user_id: str, content: str) -> dict:
        """
        Put something in your owner's chat window that they did not ask for.

        This turn is with somebody else — a person on an IM channel, a teammate,
        a peer agent. This tool does NOT reply to them; it speaks to your owner,
        who is not part of this conversation and will see it out of context.

        **Default is not to use it.** Routine conversation stays where it
        happened. Notify only when one of these is true:
          (a) your owner was explicitly mentioned or asked for;
          (b) a decision or action is needed from them;
          (c) something they specifically track was said.

        Replying to whoever contacted you is a different act, done with a
        different tool. Notifying your owner does not discharge that — the
        person waiting on the other channel cannot see what you told your owner.

        Args:
            agent_id: your own agent id
            user_id: your owner
            content: what they need to know, in markdown

        Returns:
            A confirmation that the notice reached the owner's window.
        """
        return {
            "success": True,
            "message": "Notice delivered to owner",
            "user_id": user_id,
            "agent_id": agent_id,
            "content": content,
        }

    return mcp

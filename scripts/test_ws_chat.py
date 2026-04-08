"""
WebSocket chat test helper.
Usage: uv run python scripts/test_ws_chat.py <agent_id> <user_id> "<message>"
"""
import asyncio
import json
import sys
import websockets


async def chat(agent_id: str, user_id: str, message: str, timeout: float = 3000):
    uri = "ws://localhost:8000/ws/agent/run"
    payload = {
        "agent_id": agent_id,
        "user_id": user_id,
        "input_content": message,
        "working_source": "chat",
    }

    stats = {"progress": 0, "agent_response": 0, "heartbeat": 0, "tool_calls": []}
    user_facing_reply = []
    all_messages = []

    async with websockets.connect(uri, max_size=10 * 1024 * 1024) as ws:
        await ws.send(json.dumps(payload))
        try:
            async with asyncio.timeout(timeout):
                async for raw in ws:
                    msg = json.loads(raw)
                    msg_type = msg.get("type", "")
                    all_messages.append(msg)

                    if msg_type == "heartbeat":
                        stats["heartbeat"] += 1
                    elif msg_type == "progress":
                        stats["progress"] += 1
                        details = msg.get("details") or {}
                        tool_name = details.get("tool_name", "") if isinstance(details, dict) else ""
                        if tool_name:
                            stats["tool_calls"].append(tool_name)
                        # Extract user-facing reply from send_message_to_user_directly
                        if tool_name.endswith("send_message_to_user_directly"):
                            args = details.get("arguments", {})
                            content = args.get("content", "")
                            if content:
                                user_facing_reply.append(content)
                    elif msg_type == "agent_response":
                        stats["agent_response"] += 1
                    elif msg_type == "error":
                        print(f"\n[ERROR] {msg.get('content', msg)}", file=sys.stderr)
                    elif msg_type == "complete":
                        break
        except TimeoutError:
            print("[TIMEOUT] WebSocket timed out", file=sys.stderr)

    reply = "\n".join(user_facing_reply)
    print("=" * 60)
    print(f"AGENT REPLY ({len(reply)} chars):")
    print("-" * 60)
    print(reply[:2000] if reply else "(no user-facing reply)")
    print("-" * 60)
    print(f"Stats: {stats['progress']} progress, {stats['agent_response']} agent_response, {stats['heartbeat']} heartbeat")
    print(f"Tool calls: {stats['tool_calls']}")
    print("=" * 60)
    return reply, stats, all_messages


if __name__ == "__main__":
    if len(sys.argv) < 4:
        print("Usage: uv run python scripts/test_ws_chat.py <agent_id> <user_id> '<message>'")
        sys.exit(1)
    asyncio.run(chat(sys.argv[1], sys.argv[2], sys.argv[3]))

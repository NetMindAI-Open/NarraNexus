"""
@file_name: ws_agent_run.py
@author: Bin Liang
@date: 2026-05-11
@description: Minimal WebSocket client for /ws/agent/run — sends a single
turn and streams events to stdout. Used to drive the P0 #2 fix verification
against the local backend.

Usage:
    uv run python scripts/ws_agent_run.py "你好啊"
    uv run python scripts/ws_agent_run.py "好"   # follow-up after a first turn
"""
from __future__ import annotations

import asyncio
import json
import sys

import websockets

AGENT_ID = "agent_97b0bde56ba5"  # 阿良
USER_ID = "binliang"
WS_URL = "ws://127.0.0.1:8000/ws/agent/run"


async def run(input_text: str) -> None:
    async with websockets.connect(WS_URL, max_size=8 * 1024 * 1024) as ws:
        payload = {
            "agent_id": AGENT_ID,
            "user_id": USER_ID,
            "input_content": input_text,
            "working_source": "chat",
        }
        await ws.send(json.dumps(payload))
        print(f"[ws] sent: {input_text!r}", flush=True)

        printed = 0
        async for raw in ws:
            try:
                msg = json.loads(raw)
            except Exception:
                print(f"[ws] non-json: {raw[:200]}", flush=True)
                continue
            mtype = msg.get("type", "?")
            if mtype == "progress":
                step = msg.get("step")
                title = msg.get("title", "")[:60]
                status = msg.get("status")
                print(f"[ws] progress step={step} status={status} {title}", flush=True)
            elif mtype == "agent_response":
                delta = msg.get("delta", "")[:80]
                print(f"[ws] response_delta: {delta}", flush=True)
            elif mtype == "agent_thinking":
                tc = msg.get("thinking_content", "")[:80]
                print(f"[ws] thinking: {tc}", flush=True)
            elif mtype == "error":
                print(f"[ws] ERROR: {msg.get('error_message')}", flush=True)
            elif mtype == "stop":
                print(f"[ws] stop", flush=True)
                break
            else:
                print(f"[ws] {mtype}: {str(msg)[:200]}", flush=True)
            printed += 1
            if printed > 500:
                print("[ws] too many events, stopping", flush=True)
                break


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: ws_agent_run.py <input>", file=sys.stderr)
        sys.exit(2)
    asyncio.run(run(sys.argv[1]))

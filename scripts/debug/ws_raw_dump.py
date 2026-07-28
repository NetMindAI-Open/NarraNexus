"""
@file_name: ws_raw_dump.py
@author: Bin Liang
@date: 2026-05-12
@description: Dump every WebSocket frame from /ws/agent/run with
type / timestamp / key fields visible, so we can design the inline
timeline UI on actual data, not guesses.

Outputs JSONL: one object per ws frame, with `_recv_at` set to the
client receive time (so we can tell whether the backend stamps its
own timestamps).
"""
from __future__ import annotations

import asyncio
import json
import sys
import time

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
        print(f"# sent: {input_text!r}", file=sys.stderr, flush=True)

        seq = 0
        async for raw in ws:
            recv_at = time.time()
            try:
                msg = json.loads(raw)
            except Exception:
                msg = {"_raw_non_json": raw[:200]}
            msg.setdefault("_seq", seq)
            msg["_recv_at"] = recv_at
            print(json.dumps(msg, ensure_ascii=False))
            sys.stdout.flush()
            seq += 1
            if msg.get("type") in ("complete", "error", "stop"):
                break


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: ws_raw_dump.py <input>", file=sys.stderr)
        sys.exit(2)
    asyncio.run(run(sys.argv[1]))

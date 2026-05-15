"""
@file_name: ws_multi_turn.py
@author: Bin Liang
@date: 2026-05-11
@description: Run a sequence of turns through /ws/agent/run, one
per WebSocket round-trip, summarising each turn. Used to drive
narrative-switch scenarios (e.g. A股 → 特朗普访华 → 回 A 股) so we
can observe NarrativeSelect + [CHAT-CTX] log flows.

Usage:
    uv run python scripts/ws_multi_turn.py \\
        "讨论一下中国 A 股最近怎么样" \\
        "你觉得新能源板块还能涨吗" \\
        "突然问下，美国特朗普访华怎么看" \\
        "回到 A 股，那金融板块呢"
"""
from __future__ import annotations

import asyncio
import json
import sys

import websockets

AGENT_ID = "agent_97b0bde56ba5"  # 阿良
USER_ID = "binliang"
WS_URL = "ws://127.0.0.1:8000/ws/agent/run"


async def one_turn(input_text: str, idx: int, total: int) -> dict:
    """Run one turn, return a per-turn summary."""
    print(f"\n{'=' * 78}\n[Turn {idx}/{total}] >>> {input_text!r}\n{'=' * 78}", flush=True)
    out = {
        "input": input_text,
        "reply": [],
        "thinking_snippets": [],
        "errors": [],
    }
    async with websockets.connect(WS_URL, max_size=8 * 1024 * 1024) as ws:
        payload = {
            "agent_id": AGENT_ID,
            "user_id": USER_ID,
            "input_content": input_text,
            "working_source": "chat",
        }
        await ws.send(json.dumps(payload))

        async for raw in ws:
            try:
                msg = json.loads(raw)
            except Exception:
                continue
            mtype = msg.get("type", "?")
            if mtype == "agent_response":
                d = msg.get("delta", "")
                if d:
                    out["reply"].append(d)
                    print(d, end="", flush=True)
            elif mtype == "agent_thinking":
                tc = msg.get("thinking_content", "")
                if tc:
                    out["thinking_snippets"].append(tc[:200])
            elif mtype == "error":
                out["errors"].append(msg.get("error_message", ""))
                print(f"\n[ERROR] {msg.get('error_message')}", flush=True)
            elif mtype == "complete":
                print(f"\n[turn {idx} complete]", flush=True)
                break
            elif mtype == "stop":
                break
    out["reply"] = "".join(out["reply"])
    return out


async def run_all(inputs: list[str]) -> None:
    summaries = []
    for i, text in enumerate(inputs, 1):
        s = await one_turn(text, i, len(inputs))
        summaries.append(s)
        # Small breather between turns so the backend's persist + cleanup
        # has time to settle before the next narrative selection round.
        await asyncio.sleep(2)

    print(f"\n\n{'=' * 78}\nSUMMARY ({len(summaries)} turns)\n{'=' * 78}")
    for i, s in enumerate(summaries, 1):
        first_think = (s["thinking_snippets"][0] if s["thinking_snippets"] else "")[:160]
        print(f"\n[{i}] input  : {s['input']}")
        print(f"    reply  : {s['reply'][:200]}")
        if first_think:
            print(f"    thinks : {first_think}")
        if s["errors"]:
            print(f"    ERRORS : {s['errors']}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: ws_multi_turn.py <turn1> <turn2> ...", file=sys.stderr)
        sys.exit(2)
    asyncio.run(run_all(sys.argv[1:]))

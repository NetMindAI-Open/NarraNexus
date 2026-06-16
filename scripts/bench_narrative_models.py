"""
@file_name: bench_narrative_models.py
@author: Bin Liang
@date: 2026-05-12
@description: Drive 10 turns of conversation through /ws/agent/run for
one agent, designed to exercise both narrative paths (continuity LLM
and unified-match LLM). The 10-turn script mixes three distinct
topics so we know in advance which turns will hit which LLM call.

The actual model selection comes from env vars read by
NarrativeConfig (NARRATIVE_JUDGE_MODEL / NARRATIVE_CONTINUITY_MODEL).
Restart the backend with different env to compare models — this
script does NOT switch models itself.

Usage:
    uv run python scripts/bench_narrative_models.py \\
        --agent-name "Mini Test"     # creates new agent then runs

Output: per-turn wall-clock + summary JSON to stdout.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from typing import Any

import httpx
import websockets

BACKEND = "http://127.0.0.1:8000"
WS_URL = "ws://127.0.0.1:8000/ws/agent/run"
USER_ID = "binliang"

# 10-turn script: 3 narratives interleaved so we exercise both
# narrative-select paths. Turns 1-3 = Japan travel (warm up + continuity
# checks). Turn 4 = hard switch (unified_match expected). Turns 5-6
# continue topic B. Turn 7 = switch back (unified_match). Turns 8-10
# = third topic (sci-fi books).
TURNS: list[str] = [
    "I'm planning a trip to Japan next month — any general tips?",
    "What is the best time of year to visit Mount Fuji?",
    "Do I need to book ryokan accommodation in advance?",
    "Different question — how do Bitcoin perpetual futures actually work?",
    "What does the maker-taker fee structure mean for retail traders?",
    "Is using 10x leverage considered risky for someone new to crypto?",
    "Going back to Japan — should I get a JR Pass for a 10-day trip?",
    "OK switching gears: recommend me a sci-fi novel like Three-Body Problem.",
    "Any Chinese sci-fi authors translated into English besides Liu Cixin?",
    "Which one of those would you start with if you only had time for one?",
]


async def create_agent(name: str) -> str:
    """POST /api/auth/agents → returns new agent_id."""
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(
            f"{BACKEND}/api/auth/agents",
            headers={"X-User-Id": USER_ID},
            json={"agent_name": name,
                  "agent_description": "narrative model A/B bench"},
        )
        r.raise_for_status()
        data = r.json()
        if not data.get("success"):
            raise RuntimeError(f"create_agent failed: {data}")
        agent_id = data["agent"]["agent_id"]
        print(f"[create_agent] {agent_id} ({name})", flush=True)
        return agent_id


async def one_turn(agent_id: str, text: str, idx: int) -> dict:
    """Run one WS turn; return timing + reply summary."""
    print(f"\n{'='*78}\n[Turn {idx}] >>> {text}\n{'='*78}", flush=True)
    started = time.monotonic()
    reply_chunks: list[str] = []
    errors: list[str] = []
    first_progress_ms: float | None = None
    last_progress_label: str | None = None
    progress_count = 0

    async with websockets.connect(WS_URL, max_size=8 * 1024 * 1024) as ws:
        await ws.send(json.dumps({
            "agent_id": agent_id,
            "user_id": USER_ID,
            "input_content": text,
            "working_source": "chat",
        }))
        async for raw in ws:
            try:
                msg = json.loads(raw)
            except Exception:
                continue
            mtype = msg.get("type", "?")
            if mtype == "progress":
                progress_count += 1
                if first_progress_ms is None:
                    first_progress_ms = (time.monotonic() - started) * 1000
                last_progress_label = msg.get("title", last_progress_label)
            elif mtype == "agent_response":
                d = msg.get("delta", "")
                if d:
                    reply_chunks.append(d)
            elif mtype == "error":
                errors.append(msg.get("error_message", ""))
            elif mtype == "complete":
                break
            elif mtype == "stop":
                break
    total_ms = (time.monotonic() - started) * 1000
    reply = "".join(reply_chunks)
    print(f"[Turn {idx}] reply: {reply[:200]}", flush=True)
    return {
        "idx": idx,
        "input": text,
        "total_ms": round(total_ms, 1),
        "first_progress_ms": round(first_progress_ms or 0, 1),
        "progress_events": progress_count,
        "last_step": last_progress_label,
        "reply_chars": len(reply),
        "reply_preview": reply[:160],
        "errors": errors,
    }


async def run_all(agent_id: str) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    overall_start = time.monotonic()
    for i, text in enumerate(TURNS, 1):
        try:
            row = await one_turn(agent_id, text, i)
        except Exception as e:  # noqa: BLE001
            row = {"idx": i, "input": text, "error": str(e)}
        results.append(row)
        # Tiny pause so step_5 background hooks have time to settle
        # before the next narrative selection round starts cold.
        await asyncio.sleep(2)
    overall_ms = (time.monotonic() - overall_start) * 1000
    print(f"\nOVERALL {len(TURNS)} turns: {overall_ms:.0f} ms "
          f"(avg {overall_ms/len(TURNS):.0f} ms/turn)")
    return results


async def main():
    p = argparse.ArgumentParser()
    p.add_argument("--agent-name", required=True,
                   help="display name for the new agent")
    p.add_argument("--out", default=None,
                   help="write JSON results to this file")
    args = p.parse_args()

    agent_id = await create_agent(args.agent_name)
    results = await run_all(agent_id)

    payload = {"agent_id": agent_id, "agent_name": args.agent_name,
               "user_id": USER_ID, "turns": results}
    if args.out:
        with open(args.out, "w") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
        print(f"\nWrote results: {args.out}", flush=True)
    else:
        print(json.dumps(payload, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())

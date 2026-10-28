"""
@file_name: hybrid_sim.py
@date: 2026-08-06
@description: Hybrid producer/observer simulator for F28 voice fast mode.

Hybrid occupies both ends of the voice chain, and both ends are plain
Matrix client-server API calls — so one script with one test account can
play Hybrid's mouth (producer: final STT as m.text + rtc voice_input
metadata) and Hybrid's ears (observer: the live base m.text, m.replace
increments and the final edit). This is the L2 rehearsal tool AND the
baseline instrument: the observer's event timeline yields first-live
latency, edit cadence and total duration for fast AND normal turns.

Usage (against the dev homeserver; credentials via env or flags):

  # Send one voice turn into a room (Hybrid producer)
  python hybrid_sim.py send-turn --room '!call:h' --text "What is the weather?" \
      --session rtc-s1 [--seq 1] [--invalid seq]

  # Watch a room and print the reply timeline with timings (Hybrid observer)
  python hybrid_sim.py observe --room '!call:h' --seconds 30

  # Scripted scenario: send, observe, assert (PRD 4.5 scenario table)
  python hybrid_sim.py run-scenario --room '!call:h' --scenario single_turn

Environment: MATRIX_HOMESERVER, MATRIX_TOKEN (the human test account).
The L1 in-process scenarios live in test_l1_scenarios.py and need no
homeserver at all.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import uuid
from typing import Any, Optional

import aiohttp

RTC_KEY = "ai.netmind.rtc.voice_input"
LIVE_KEY = "org.matrix.msc4357.live"

ENVELOPE = (
    '<narra-system-prompt version="1" mode="voice">\n'
    "Reply for a real-time voice call.\n"
    "</narra-system-prompt>\n\n"
)


def build_voice_content(
    text: str,
    *,
    session: str = "rtc-sim",
    turn: Optional[str] = None,
    invalid: Optional[str] = None,
    with_envelope: bool = True,
) -> dict:
    """The handoff §3.1 event content; ``invalid`` breaks one field."""
    meta: dict[str, Any] = {
        "version": 1,
        "rtc_session_id": session,
        "turn_id": turn or f"turn-{uuid.uuid4().hex[:8]}",
        "invocation_id": f"inv-{uuid.uuid4().hex[:8]}",
        "agent_profile_id": "sim-profile",
        "seq": 1,
        "transcript_final": True,
        "transport": "matrix",
        "voice_instructions": "Reply for a real-time voice call.",
    }
    if invalid == "seq":
        meta["seq"] = 2
    elif invalid == "version":
        meta["version"] = 2
    elif invalid == "transport":
        meta["transport"] = "gateway"
    elif invalid == "final":
        meta["transcript_final"] = False
    elif invalid == "missing-id":
        del meta["rtc_session_id"]
    elif invalid == "silent":
        meta["seq"] = 2
        del meta["voice_instructions"]
    body = (ENVELOPE + text) if with_envelope else text
    return {"msgtype": "m.text", "body": body, RTC_KEY: meta}


class MatrixSimClient:
    """Minimal client-server API wrapper: send + sync, nothing else."""

    def __init__(self, homeserver: str, token: str):
        self._base = homeserver.rstrip("/")
        self._headers = {"Authorization": f"Bearer {token}"}

    async def send(self, room_id: str, content: dict) -> str:
        txn = f"sim-{uuid.uuid4().hex}"
        url = (
            f"{self._base}/_matrix/client/v3/rooms/{room_id}"
            f"/send/m.room.message/{txn}"
        )
        async with aiohttp.ClientSession() as s:
            async with s.put(url, headers=self._headers, json=content) as r:
                r.raise_for_status()
                return (await r.json()).get("event_id", "")

    async def observe(self, room_id: str, seconds: float):
        """Yield (t_monotonic, event) for room timeline events."""
        since = None
        deadline = time.monotonic() + seconds
        async with aiohttp.ClientSession() as s:
            while time.monotonic() < deadline:
                params = {"timeout": "5000"}
                if since:
                    params["since"] = since
                async with s.get(
                    f"{self._base}/_matrix/client/v3/sync",
                    headers=self._headers,
                    params=params,
                ) as r:
                    r.raise_for_status()
                    data = await r.json()
                since = data.get("next_batch")
                room = (data.get("rooms", {}).get("join", {})).get(room_id, {})
                for ev in room.get("timeline", {}).get("events", []):
                    yield time.monotonic(), ev


def analyze_timeline(events: list[tuple[float, dict]], t_sent: float) -> dict:
    """Reply-stream metrics from an observed timeline (the baseline data)."""
    base_at = first_edit_at = final_at = None
    edits = 0
    for t, ev in events:
        if ev.get("type") != "m.room.message":
            continue
        content = ev.get("content", {})
        relates = content.get("m.relates_to", {})
        if relates.get("rel_type") == "m.replace":
            edits += 1
            if first_edit_at is None:
                first_edit_at = t
            if LIVE_KEY not in content:
                final_at = t
        elif LIVE_KEY in content and base_at is None:
            base_at = t
    return {
        "first_live_s": (base_at - t_sent) if base_at else None,
        "first_edit_s": (first_edit_at - t_sent) if first_edit_at else None,
        "final_s": (final_at - t_sent) if final_at else None,
        "edit_count": edits,
    }


async def _cmd_send_turn(args) -> int:
    client = _client(args)
    content = build_voice_content(
        args.text, session=args.session, invalid=args.invalid
    )
    event_id = await client.send(args.room, content)
    print(json.dumps({"sent": event_id, "session": args.session}))
    return 0


async def _cmd_observe(args) -> int:
    client = _client(args)
    t0 = time.monotonic()
    events = []
    async for t, ev in client.observe(args.room, args.seconds):
        events.append((t, ev))
        content = ev.get("content", {})
        kind = (
            "final"
            if content.get("m.relates_to", {}).get("rel_type") == "m.replace"
            and LIVE_KEY not in content
            else "edit"
            if content.get("m.relates_to", {}).get("rel_type") == "m.replace"
            else "base-live"
            if LIVE_KEY in content
            else ev.get("type", "?")
        )
        print(f"{t - t0:8.2f}s  {kind:9s}  {content.get('body', '')[:80]}")
    print(json.dumps(analyze_timeline(events, t0), indent=2))
    return 0


async def _cmd_run_scenario(args) -> int:
    client = _client(args)
    if args.scenario == "single_turn":
        t0 = time.monotonic()
        await client.send(args.room, build_voice_content("What is the weather today?"))
        events = [e async for e in client.observe(args.room, args.seconds)]
        metrics = analyze_timeline(events, t0)
        ok = metrics["first_live_s"] is not None and metrics["final_s"] is not None
        print(json.dumps({"scenario": "single_turn", "ok": ok, **metrics}))
        return 0 if ok else 1
    if args.scenario == "invalid_metadata":
        for bad in ("seq", "version", "transport", "final", "missing-id"):
            await client.send(
                args.room, build_voice_content(f"probe {bad}", invalid=bad)
            )
        print(json.dumps({"scenario": "invalid_metadata", "sent": 5,
                          "expect": "5 plain replies, zero live events"}))
        return 0
    print(f"unknown scenario: {args.scenario}", file=sys.stderr)
    return 2


def _client(args) -> MatrixSimClient:
    homeserver = args.homeserver or os.environ.get("MATRIX_HOMESERVER", "")
    token = args.token or os.environ.get("MATRIX_TOKEN", "")
    if not homeserver or not token:
        print("MATRIX_HOMESERVER / MATRIX_TOKEN required", file=sys.stderr)
        raise SystemExit(2)
    return MatrixSimClient(homeserver, token)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--homeserver", default="")
    parser.add_argument("--token", default="")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_send = sub.add_parser("send-turn")
    p_send.add_argument("--room", required=True)
    p_send.add_argument("--text", required=True)
    p_send.add_argument("--session", default="rtc-sim")
    p_send.add_argument("--invalid", default=None,
                        choices=["seq", "version", "transport", "final", "missing-id"])

    p_obs = sub.add_parser("observe")
    p_obs.add_argument("--room", required=True)
    p_obs.add_argument("--seconds", type=float, default=30.0)

    p_run = sub.add_parser("run-scenario")
    p_run.add_argument("--room", required=True)
    p_run.add_argument("--scenario", required=True)
    p_run.add_argument("--seconds", type=float, default=30.0)

    args = parser.parse_args()
    handler = {
        "send-turn": _cmd_send_turn,
        "observe": _cmd_observe,
        "run-scenario": _cmd_run_scenario,
    }[args.cmd]
    return asyncio.run(handler(args))


if __name__ == "__main__":
    raise SystemExit(main())

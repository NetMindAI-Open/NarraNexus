"""
@file_name: seed_mixed_chat_history.py
@author: Bin Liang
@date: 2026-05-11
@description: Seed a fresh ChatModule instance with synthetic rows that
cover every WorkingSource × message_type combination, then leave it
referenced from a real narrative + instance_narrative_link so the
backend picks it up via hook_data_gathering.

The point is to verify the per-source dispatch end-to-end:
- Lark "real reply" rows (working_source=lark, message_type=chat) MUST
  survive into chat_history with the [Lark · X] prefix
- Activity rows (message_type=activity, any working_source) MUST be
  filtered out
- message_bus rows MUST get the [Bus · from agent=Y] prefix
- chat rows are the baseline

Usage:
    uv run python scripts/seed_mixed_chat_history.py
    # Then send a turn via ws_agent_run / ws_multi_turn against agent
    # 阿良 — backend log should show long_term/short_term reflecting the
    # seeded rows (after filtering) with proper source prefixes.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

DB_PATH = Path.home() / ".nexusagent" / "nexusagent.db"
AGENT_ID = "agent_97b0bde56ba5"          # 阿良
USER_ID = "binliang"

# A brand-new "seed" instance kept distinct from production instances so
# we never overwrite real conversations. It also lets us toggle this in
# and out of a narrative cleanly.
SEED_INSTANCE_ID = "chat_seed_mixed"
SEED_NARRATIVE_ID = f"nar_seed_mixed_{uuid.uuid4().hex[:8]}"


def utc_iso(offset_minutes: int) -> str:
    """Return an ISO timestamp `offset_minutes` ago (negative=past)."""
    return (datetime.now(timezone.utc) + timedelta(minutes=offset_minutes)).isoformat()


def main() -> None:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    # --- 1. Synthetic messages covering every meaningful combination ---
    # Times spread across the last ~2 hours so they sort sensibly.
    msgs = [
        # chat trigger — normal user↔agent dialogue
        {
            "role": "user",
            "content": "你之前提到的那本 Rust 书，叫什么名字来着？",
            "meta_data": {
                "event_id": "evt_seed_1",
                "timestamp": utc_iso(-120),
                "instance_id": SEED_INSTANCE_ID,
                "working_source": "chat",
            },
        },
        {
            "role": "assistant",
            "content": "《Programming Rust》—— 巨经典的一本，O'Reilly 出的。",
            "meta_data": {
                "event_id": "evt_seed_1",
                "timestamp": utc_iso(-119),
                "instance_id": SEED_INSTANCE_ID,
                "working_source": "chat",
            },
        },

        # lark trigger — Loki @阿良 in a group, agent really did reply
        # via lark_cli (post-P0#2-FixA, this row is written as
        # message_type=chat with the agent's real reply text).
        {
            "role": "user",
            "content": "[Lark · Loki · ou_loki · oc_room]\n[12:30] @Loki: 阿良，今晚开会吗？",
            "meta_data": {
                "event_id": "evt_seed_2",
                "timestamp": utc_iso(-90),
                "instance_id": SEED_INSTANCE_ID,
                "working_source": "lark",
                "channel_tag": {
                    "channel": "lark",
                    "sender_name": "Loki",
                    "sender_id": "ou_loki",
                    "room_id": "oc_room",
                    "room_name": "顺风耳, Loki, 阿良",
                },
            },
        },
        {
            "role": "assistant",
            "content": "今晚 8 点，老地方。带酒。🍶",
            "meta_data": {
                "event_id": "evt_seed_2",
                "timestamp": utc_iso(-89),
                "instance_id": SEED_INSTANCE_ID,
                "working_source": "lark",
                "channel_tag": {
                    "channel": "lark",
                    "sender_name": "Loki",
                    "sender_id": "ou_loki",
                    "room_id": "oc_room",
                    "room_name": "顺风耳, Loki, 阿良",
                },
            },
        },

        # lark trigger — agent cascade-forwarded message, did NOT reply
        # (real background activity row, content rewritten by the
        # _build_activity_summary path). MUST be filtered out.
        {
            "role": "assistant",
            "content": "Handled a message from Loki in 顺风耳, Loki, 阿良 +7",
            "meta_data": {
                "event_id": "evt_seed_3",
                "timestamp": utc_iso(-60),
                "instance_id": SEED_INSTANCE_ID,
                "working_source": "lark",
                "message_type": "activity",
                "channel_tag": {
                    "channel": "lark",
                    "sender_name": "Loki",
                    "sender_id": "ou_loki",
                    "room_id": "oc_room",
                    "room_name": "顺风耳, Loki, 阿良",
                },
            },
        },

        # message_bus trigger — another agent talked to 阿良 via bus,
        # and 阿良 used send_message_to_user_directly to relay back.
        {
            "role": "user",
            "content": "[Message Bus - Incoming Messages]\nFrom: agent_X\n请帮忙把 Q3 营销报告发我。",
            "meta_data": {
                "event_id": "evt_seed_4",
                "timestamp": utc_iso(-45),
                "instance_id": SEED_INSTANCE_ID,
                "working_source": "message_bus",
                "from_agent": "agent_X",
            },
        },
        {
            "role": "assistant",
            "content": "好的，Q3 营销报告我整理好之后通过 bus 发给你。",
            "meta_data": {
                "event_id": "evt_seed_4",
                "timestamp": utc_iso(-44),
                "instance_id": SEED_INSTANCE_ID,
                "working_source": "message_bus",
                "from_agent": "agent_X",
            },
        },

        # job trigger — scheduled job ran, agent did NOT reply to user
        # (the activity row that the new filter must drop).
        {
            "role": "assistant",
            "content": "Executed a background job",
            "meta_data": {
                "event_id": "evt_seed_5",
                "timestamp": utc_iso(-30),
                "instance_id": SEED_INSTANCE_ID,
                "working_source": "job",
                "message_type": "activity",
            },
        },

        # chat trigger — the latest real turn (user follow-up).
        {
            "role": "user",
            "content": "对了，那本书有中文版吗？",
            "meta_data": {
                "event_id": "evt_seed_6",
                "timestamp": utc_iso(-10),
                "instance_id": SEED_INSTANCE_ID,
                "working_source": "chat",
            },
        },
        {
            "role": "assistant",
            "content": "有的，《Rust 程序设计》就是它的中文版，电子工业出版社出的。",
            "meta_data": {
                "event_id": "evt_seed_6",
                "timestamp": utc_iso(-9),
                "instance_id": SEED_INSTANCE_ID,
                "working_source": "chat",
            },
        },
    ]

    # --- 2. Create the seed ChatModule instance ---
    cur.execute("DELETE FROM module_instances WHERE instance_id = ?", (SEED_INSTANCE_ID,))
    cur.execute("""
        INSERT INTO module_instances
          (instance_id, agent_id, user_id, module_class, status, is_public,
           created_at, updated_at, last_used_at)
        VALUES (?, ?, ?, 'ChatModule', 'active', 0,
                datetime('now'), datetime('now'), datetime('now'))
    """, (SEED_INSTANCE_ID, AGENT_ID, USER_ID))

    # --- 3. Insert chat memory ---
    memory_payload = json.dumps({
        "messages": msgs,
        "last_event_id": "evt_seed_6",
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }, ensure_ascii=False)

    cur.execute("DELETE FROM instance_json_format_memory_chat WHERE instance_id = ?",
                (SEED_INSTANCE_ID,))
    cur.execute("""
        INSERT INTO instance_json_format_memory_chat
          (instance_id, memory, created_at, updated_at)
        VALUES (?, ?, datetime('now'), datetime('now'))
    """, (SEED_INSTANCE_ID, memory_payload))

    # --- 4. Create a synthetic narrative + link the seed instance ---
    cur.execute("""
        INSERT OR REPLACE INTO narratives
          (narrative_id, agent_id, narrative_info, event_ids, type, is_special,
           created_at, updated_at)
        VALUES (?, ?, ?, ?, 'CHAT', 'other',
                datetime('now'), datetime('now'))
    """, (
        SEED_NARRATIVE_ID,
        AGENT_ID,
        json.dumps({
            "name": "Seed Mixed History",
            "description": "Synthetic narrative used to exercise per-source dispatch end-to-end.",
            "current_summary": "Talks about Rust books, mixed with Lark/Bus/Job rows.",
            "actors": [
                {"id": AGENT_ID, "type": "agent"},
                {"id": USER_ID, "type": "user"},
            ],
        }, ensure_ascii=False),
        json.dumps([m["meta_data"]["event_id"] for m in msgs]),
    ))

    cur.execute("DELETE FROM instance_narrative_links WHERE instance_id = ?",
                (SEED_INSTANCE_ID,))
    cur.execute("""
        INSERT INTO instance_narrative_links
          (instance_id, narrative_id, link_type, local_status,
           created_at, updated_at)
        VALUES (?, ?, 'active', 'active',
                datetime('now'), datetime('now'))
    """, (SEED_INSTANCE_ID, SEED_NARRATIVE_ID))

    conn.commit()
    conn.close()

    print(f"[seed] Seeded instance {SEED_INSTANCE_ID} with {len(msgs)} mixed rows:")
    counts: dict[str, int] = {}
    for m in msgs:
        meta = m["meta_data"]
        key = f"{meta['working_source']}/{meta.get('message_type', 'chat')}/{m['role']}"
        counts[key] = counts.get(key, 0) + 1
    for key, n in sorted(counts.items()):
        print(f"  - {key}: {n}")
    print(f"[seed] Linked to narrative {SEED_NARRATIVE_ID}")
    print()
    print("Now send a turn through ws_agent_run.py and grep [CHAT-CTX] in")
    print("the backend log to see the filter + prefix work in production.")


if __name__ == "__main__":
    main()

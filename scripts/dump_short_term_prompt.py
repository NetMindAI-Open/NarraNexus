"""
@file_name: dump_short_term_prompt.py
@author: Bin Liang
@date: 2026-05-11
@description: Reproduce the *exact* system-prompt fragment ContextRuntime
builds for the "short reply lands in default narrative" scenario,
using real chat history from the local SQLite DB.

We bypass AsyncDatabaseClient (it would trigger auto_migrate on a 5 MB
real DB and stall) and read messages with stdlib sqlite3 directly,
then feed them into ContextRuntime._build_short_term_memory_prompt —
a pure function over a list of message dicts.

Usage:
    uv run python scripts/dump_short_term_prompt.py
"""
from __future__ import annotations

import json
import os
import sqlite3

DB_PATH = os.path.expanduser("~/.nexusagent/nexusagent.db")
AGENT_ID = "agent_97b0bde56ba5"          # 阿良
USER_ID = "binliang"
import os as _os
CURRENT_INSTANCE_ID = _os.environ.get(
    "DUMP_INSTANCE_ID", "chat_b51f070d"  # default: GreetingAndCourtesy (3 msgs)
)
SIMULATED_INPUT = _os.environ.get("DUMP_INPUT", "好")


def load_instance_messages(conn: sqlite3.Connection, instance_id: str) -> list[dict]:
    row = conn.execute(
        "SELECT memory FROM instance_json_format_memory_chat WHERE instance_id = ?",
        (instance_id,),
    ).fetchone()
    if not row or not row[0]:
        return []
    data = json.loads(row[0])
    return list(data.get("messages", []))


def load_short_term(conn: sqlite3.Connection) -> list[dict]:
    """Mimic ChatModule._load_short_term_memory **after** Phase 3 fix:
    same selection as before, but message_type=activity rows are filtered
    out (these were background work, not user dialogue).

    Take all OTHER active ChatModule instances of the same agent+user,
    pull their messages, sort by timestamp desc, cap at 15, then sort
    asc for chronological output.
    """
    rows = conn.execute(
        """
        SELECT instance_id FROM module_instances
         WHERE agent_id = ? AND user_id = ?
           AND module_class = 'ChatModule' AND status = 'active'
           AND instance_id != ?
         ORDER BY last_used_at DESC
        """,
        (AGENT_ID, USER_ID, CURRENT_INSTANCE_ID),
    ).fetchall()

    # Mirror the two-stage budgeting in chat_module._load_short_term_memory.
    SHORT_TERM_MAX = 15
    SHORT_TERM_PER_INSTANCE = 5
    collected: list[dict] = []
    dropped_activity = 0

    for (inst_id,) in rows:
        msgs = load_instance_messages(conn, inst_id)
        keepers: list[dict] = []
        for msg in msgs:
            meta = msg.get("meta_data") or {}
            if meta.get("message_type") == "activity":
                dropped_activity += 1
                continue
            if (meta.get("working_source", "chat") != "chat"
                    and msg.get("role") != "assistant"):
                continue
            if "meta_data" not in msg:
                msg["meta_data"] = {}
            msg["meta_data"]["instance_id"] = inst_id
            msg["meta_data"]["memory_type"] = "short_term"
            keepers.append(msg)

        # Stage A: per-instance cap.
        if len(keepers) > SHORT_TERM_PER_INSTANCE:
            keepers.sort(
                key=lambda m: m.get("meta_data", {}).get("timestamp", ""),
                reverse=True,
            )
            keepers = keepers[:SHORT_TERM_PER_INSTANCE]
        collected.extend(keepers)

    print(f"[dump] short_term: dropped {dropped_activity} activity rows pre-cap")

    # Stage B: global cap, then chronological ordering.
    collected.sort(
        key=lambda m: m.get("meta_data", {}).get("timestamp", ""),
        reverse=True,
    )
    collected = collected[:SHORT_TERM_MAX]
    collected.sort(key=lambda m: m.get("meta_data", {}).get("timestamp", ""))
    return collected


SHORT_TERM_TOKEN_LIMIT = 40000


def _build_short_term_section(
    short_term_messages: list[dict],
    header: str,
) -> str:
    """Inline mirror of ContextRuntime._build_short_term_memory_prompt
    (production code path that we want to replay)."""
    from datetime import datetime, timezone

    prompt = header
    by_inst: dict[str, list[dict]] = {}
    for msg in short_term_messages:
        inst = (msg.get("meta_data") or {}).get("instance_id", "unknown")
        by_inst.setdefault(inst, []).append(msg)

    groups = list(reversed(by_inst.items()))
    budget = SHORT_TERM_TOKEN_LIMIT - len(prompt)
    sections: list[str] = []

    for inst_id, msgs in groups:
        if budget <= 0:
            break
        first_ts = ""
        for m in msgs:
            ts = (m.get("meta_data") or {}).get("timestamp", "")
            if ts:
                first_ts = ts
                break
        time_ago = ""
        if first_ts:
            try:
                msg_time = datetime.fromisoformat(first_ts.replace("Z", "+00:00"))
                now = datetime.now(timezone.utc)
                minutes = int((now - msg_time).total_seconds() / 60)
                if minutes < 1:
                    time_ago = "Just now"
                elif minutes < 60:
                    time_ago = f"{minutes} minutes ago"
                else:
                    hours = minutes // 60
                    if hours < 24:
                        time_ago = f"{hours} hours ago"
                    else:
                        days = hours // 24
                        time_ago = f"{days} days ago"
            except Exception:
                time_ago = "Recently"

        section = f"\n**[{time_ago}]**\n"
        for m in msgs:
            if budget <= 0:
                break
            role_label = "User" if m.get("role") == "user" else "Assistant"
            line = f"- {role_label}: {m.get('content', '')}\n"
            if len(section) + len(line) > budget:
                break
            section += line

        budget -= len(section)
        sections.append(section)

    sections.reverse()
    prompt += "".join(sections)
    return prompt


def main() -> None:
    # Import channel modules so they register their MessageSourceHandlers.
    # In the live process this happens at startup; here we trigger it
    # manually so the dump renders rows with the right source prefixes.
    import xyz_agent_context.module.lark_module  # noqa: F401
    import xyz_agent_context.message_bus  # noqa: F401

    conn = sqlite3.connect(DB_PATH)

    long_term_raw = load_instance_messages(conn, CURRENT_INSTANCE_ID)
    # Apply the same Phase 3 activity filter to long_term.
    long_term = []
    dropped = 0
    for m in long_term_raw:
        meta = m.get("meta_data") or {}
        if meta.get("message_type") == "activity":
            dropped += 1
            continue
        if "meta_data" not in m:
            m["meta_data"] = {}
        m["meta_data"]["memory_type"] = "long_term"
        m["meta_data"]["instance_id"] = CURRENT_INSTANCE_ID
        long_term.append(m)
    print(f"[dump] long_term: dropped {dropped} activity rows")

    short_term = load_short_term(conn)
    conn.close()

    print("=" * 78)
    print(f"Scenario: user='{USER_ID}', agent='{AGENT_ID}', input={SIMULATED_INPUT!r}")
    print(f"Instance: {CURRENT_INSTANCE_ID} (bound to a default narrative)")
    print("=" * 78)
    print(f"long_term  rows: {len(long_term)}")
    print(f"short_term rows (cross-narrative, capped at 15): {len(short_term)}")

    print("\n--- long_term messages (real conversation flow) ---")
    for i, m in enumerate(long_term):
        content = (m.get("content") or "").replace("\n", " ")[:140]
        print(f"  [{i}] {m.get('role'):9s} | {content}")

    print("\n--- short_term messages (cross-narrative) ---")
    for i, m in enumerate(short_term):
        content = (m.get("content") or "").replace("\n", " ")[:140]
        inst = (m.get("meta_data") or {}).get("instance_id", "?")
        ts = (m.get("meta_data") or {}).get("timestamp", "")
        print(f"  [{i}] {m.get('role'):9s} | inst={inst} | ts={ts} | {content}")

    # Use the real SHORT_TERM_MEMORY_HEADER + inline the
    # _build_short_term_memory_prompt logic so we don't pull in
    # ContextRuntime (which requires a DB client at construction time).
    from xyz_agent_context.context_runtime.prompts import SHORT_TERM_MEMORY_HEADER
    short_term_section = _build_short_term_section(short_term, SHORT_TERM_MEMORY_HEADER)

    fake_base_system = (
        "[Imagine: BasicInfoModule + AwarenessModule + Channel + RAG prompts "
        "would be here in a real turn. We focus on the short-term memory "
        "section appended below.]"
    )
    enhanced_system_prompt = fake_base_system + "\n\n" + short_term_section

    final_messages = [{"role": "system", "content": enhanced_system_prompt}]
    # Phase 4: prefix every long_term row with its MessageSource handler's
    # prefix so the LLM can tell channel-specific rows apart.
    from xyz_agent_context.channel.message_source_handler import (
        MessageSourceRegistry,
    )
    for msg in long_term:
        ws = (msg.get("meta_data") or {}).get("working_source", "chat")
        handler = MessageSourceRegistry.get(ws)
        prefix = handler.format_row_prefix(msg)
        raw = msg.get("content", "") or ""
        final_messages.append({
            "role": msg.get("role", "user"),
            "content": f"{prefix} {raw}" if prefix else raw,
        })
    final_messages.append({"role": "user", "content": SIMULATED_INPUT})

    print("\n" + "=" * 78)
    print("FINAL messages[] sent to LLM (structure):")
    print("=" * 78)
    for i, msg in enumerate(final_messages):
        content = msg.get("content") or ""
        print(f"\n--- messages[{i}] role={msg.get('role')} (len={len(content)} chars) ---")
        print(content[:6000])
        if len(content) > 6000:
            print(f"... [TRUNCATED — {len(content) - 6000} more chars]")


if __name__ == "__main__":
    main()

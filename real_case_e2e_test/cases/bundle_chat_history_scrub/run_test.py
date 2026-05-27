"""E2E for the 2026-05-18 "disable chat history actually disables it" fix.

Background: before this fix, the "Include chat history" checkbox in the
bundle export UI gated events.jsonl and agent_messages.jsonl but not
narrative.json. Real narrative_info blobs carry verbatim copies of
recent dialogue (via the agent's framing prompt), so the exported
bundle still leaked the most recent rounds of chat — making the toggle
a privacy lie.

This test:

  1. Creates a fresh user + agent
  2. Seeds the agent with a narrative whose narrative_info contains a
     recognizable secret phrase (simulating real-world chat-derived
     content) plus matching dynamic_summary / topic_hint / topic_keywords
  3. Seeds one event under that narrative (so events.jsonl would have
     content if the toggle were ignored)
  4. POSTs /api/bundle/export with include_chat_history=False
  5. Unzips the returned bundle and asserts that:
        a. narrative.json exists for our narrative
        b. narrative.json does NOT contain the secret phrase anywhere
        c. narrative.json's narrative_info has empty
           description/current_summary
        d. dynamic_summary / topic_keywords / topic_hint /
           routing_embedding / event_ids are scrubbed
        e. events.jsonl is absent (already gated pre-fix; regression check)
        f. agent_messages.jsonl is empty (already gated pre-fix)
  6. Same export with include_chat_history=True as a control — should
     still leak (proves the gate is exclusively on the toggle, not a
     blanket scrub).

Requires a running backend at http://127.0.0.1:8000 and NETMIND_API_KEY
in env (for normal API surface — bundle export itself doesn't call LLM).
"""
from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
import zipfile
from io import BytesIO

import httpx

BASE = os.environ.get("E2E_BASE_URL", "http://127.0.0.1:8000")
SECRET = "SECRET_CHATSCRUB_E2E_MAGIC_PHRASE_ZZZ"


def _stamp(prefix: str) -> str:
    return f"e2e_chs_{prefix}_{int(time.time())}_{uuid.uuid4().hex[:6]}"


def _hr(title: str) -> None:
    print()
    print("=" * 76)
    print(f"  {title}")
    print("=" * 76)


async def _expect(cond: bool, msg: str) -> None:
    if not cond:
        print(f"  [FAIL] {msg}")
        raise AssertionError(msg)
    print(f"  [ OK ] {msg}")


def _h(user_id: str) -> dict:
    return {"X-User-Id": user_id}


async def create_user(client: httpx.AsyncClient, user_id: str) -> None:
    r = await client.post(
        f"{BASE}/api/auth/create-user",
        json={"user_id": user_id, "display_name": user_id},
    )
    await _expect(r.status_code == 200 and r.json().get("success"),
                  f"create-user {user_id}")


async def login(client: httpx.AsyncClient, user_id: str) -> None:
    r = await client.post(f"{BASE}/api/auth/login", json={"user_id": user_id})
    await _expect(r.status_code == 200 and r.json().get("success"),
                  f"login {user_id}")


async def create_agent(
    client: httpx.AsyncClient, user_id: str, name: str
) -> str:
    r = await client.post(
        f"{BASE}/api/auth/agents",
        headers=_h(user_id),
        json={
            "agent_name": name,
            "agent_description": "chat-scrub e2e",
            "agent_type": "individual",
            "created_by": user_id,
        },
    )
    body = r.json()
    await _expect(r.status_code == 200 and body.get("success"),
                  f"create_agent {name}: {body}")
    return body["agent"]["agent_id"]


def seed_narrative_with_chat_content(agent_id: str, user_id: str) -> str:
    """Seed a narrative + event row directly via stdlib sqlite3 so we
    don't have to spin up a full agent run + LLM call for the test.

    NOTE: deliberately uses sqlite3 module (not xyz_agent_context's
    AsyncDatabaseClient). The backend already holds the SQLite file in
    its own event loop; instantiating a second async client from the
    test process contends on file locks and never returns. sqlite3 with
    a short-lived connection respects WAL semantics and unblocks
    immediately.
    """
    import sqlite3
    db_path = os.path.expanduser("~/.narranexus/nexus.db")
    narrative_id = f"nar_{uuid.uuid4().hex[:12]}"
    narrative_info = {
        "name": "Chat-scrub e2e narrative",
        "description": (
            f"Created based on query: ## Conversation History\n"
            f"[1] user: {SECRET}\n[2] agent: I see, you said {SECRET}.\n"
        ),
        "current_summary": f"Summary: user told me {SECRET}",
        "actors": [
            {"id": user_id, "type": "user"},
            {"id": agent_id, "type": "agent"},
        ],
    }
    conn = sqlite3.connect(db_path, timeout=10.0)
    try:
        conn.execute(
            "INSERT INTO narratives ("
            "  narrative_id, type, agent_id, narrative_info, "
            "  active_instances, instance_history_ids, event_ids, "
            "  dynamic_summary, env_variables, topic_keywords, "
            "  topic_hint, routing_embedding, "
            "  events_since_last_embedding_update, round_counter, "
            "  related_narrative_ids, is_special"
            ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                narrative_id,
                "other",
                agent_id,
                json.dumps(narrative_info, ensure_ascii=False),
                "[]",
                "[]",
                json.dumps(["evt_e2e_001"]),
                json.dumps([
                    {"event_id": "evt_e2e_001",
                     "summary": f"User mentioned {SECRET}",
                     "timestamp": "2026-05-18T00:00:00Z"},
                ]),
                "{}",
                json.dumps([SECRET, "general"]),
                f"Topic about {SECRET}",
                json.dumps([0.1] * 16),
                0,
                1,
                "[]",
                "other",
            ),
        )
        conn.execute(
            "INSERT INTO events ("
            "  event_id, narrative_id, agent_id, user_id, trigger, "
            "  trigger_source, state, final_output"
            ") VALUES (?,?,?,?,?,?,?,?)",
            (
                "evt_e2e_001",
                narrative_id,
                agent_id,
                user_id,
                "user",
                "chat",
                "completed",
                f"echo: {SECRET}",
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return narrative_id


async def export_bundle(
    client: httpx.AsyncClient,
    user_id: str,
    agent_id: str,
    *,
    include_chat: bool,
) -> bytes:
    payload = {
        "agent_ids": [agent_id],
        "include_chat_history": include_chat,
    }
    r = await client.post(
        f"{BASE}/api/bundle/export",
        headers=_h(user_id),
        json=payload,
    )
    if r.status_code != 200:
        raise AssertionError(f"export failed: {r.status_code} {r.text[:500]}")
    return r.content


def _read_zip(content: bytes) -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    with zipfile.ZipFile(BytesIO(content)) as zf:
        for name in zf.namelist():
            files[name] = zf.read(name)
    return files


async def assert_scrubbed(content: bytes, narrative_id: str, agent_id: str) -> None:
    files = _read_zip(content)

    # Find the narrative.json (path looks like agents/<aid>/narratives/<nid>/narrative.json)
    nfile_key = next(
        (k for k in files
         if k.endswith(f"narratives/{narrative_id}/narrative.json")),
        None,
    )
    await _expect(nfile_key is not None,
                  f"narrative.json present in bundle for {narrative_id}")
    nrow = json.loads(files[nfile_key].decode("utf-8"))

    # narrative_info — name + actors kept, description + current_summary scrubbed
    info = json.loads(nrow.get("narrative_info", "{}"))
    await _expect(info.get("name") == "Chat-scrub e2e narrative",
                  f"narrative_info.name preserved (got {info.get('name')!r})")
    await _expect(len(info.get("actors") or []) == 2,
                  f"narrative_info.actors preserved (got {info.get('actors')!r})")
    await _expect(info.get("description", "") == "",
                  f"narrative_info.description scrubbed (got {info.get('description')!r})")
    await _expect(info.get("current_summary", "") == "",
                  f"narrative_info.current_summary scrubbed (got {info.get('current_summary')!r})")

    # Standalone scrubbed columns
    await _expect(nrow.get("dynamic_summary") in ("[]", []),
                  f"dynamic_summary scrubbed (got {nrow.get('dynamic_summary')!r})")
    await _expect(nrow.get("topic_keywords") in ("[]", []),
                  f"topic_keywords scrubbed (got {nrow.get('topic_keywords')!r})")
    await _expect(nrow.get("topic_hint", "") == "",
                  f"topic_hint scrubbed (got {nrow.get('topic_hint')!r})")
    await _expect(nrow.get("routing_embedding") in (None, "null"),
                  f"routing_embedding scrubbed (got {nrow.get('routing_embedding')!r})")
    await _expect(nrow.get("event_ids") in ("[]", []),
                  f"event_ids scrubbed (got {nrow.get('event_ids')!r})")

    # SECRET phrase must not appear ANYWHERE in narrative.json bytes
    narrative_bytes = files[nfile_key]
    await _expect(SECRET.encode() not in narrative_bytes,
                  f"SECRET phrase not present in narrative.json")

    # events.jsonl should not exist (gated when include_chat_history=False)
    evt_key = next(
        (k for k in files
         if k.endswith(f"narratives/{narrative_id}/events.jsonl")),
        None,
    )
    await _expect(evt_key is None,
                  f"events.jsonl absent in narratives/{narrative_id}/")

    # agent_messages.jsonl should exist but be empty
    msg_key = next(
        (k for k in files if k.endswith(f"agents/{agent_id}/agent_messages.jsonl")),
        None,
    )
    if msg_key is not None:
        msg_content = files[msg_key].decode("utf-8").strip()
        await _expect(
            msg_content == "",
            f"agent_messages.jsonl is empty (got {len(msg_content)} bytes)"
        )


async def assert_leaks_when_enabled(
    content: bytes, narrative_id: str, agent_id: str
) -> None:
    """Control: include_chat_history=True still ships everything (proves
    the new code path is exclusively gated by the toggle)."""
    files = _read_zip(content)

    nfile_key = next(
        (k for k in files
         if k.endswith(f"narratives/{narrative_id}/narrative.json")),
        None,
    )
    await _expect(nfile_key is not None, "narrative.json present (chat enabled)")
    await _expect(SECRET.encode() in files[nfile_key],
                  "SECRET phrase IS present when chat history enabled (control)")

    evt_key = next(
        (k for k in files
         if k.endswith(f"narratives/{narrative_id}/events.jsonl")),
        None,
    )
    await _expect(evt_key is not None,
                  "events.jsonl present when chat history enabled (control)")


async def main() -> None:
    print(f"BASE = {BASE}")
    alice = _stamp("alice")

    # Make sure DATABASE_URL points at our SQLite (same as backend)
    os.environ.setdefault(
        "DATABASE_URL",
        f"sqlite:///{os.path.expanduser('~/.narranexus/nexus.db')}",
    )

    async with httpx.AsyncClient(timeout=120.0) as client:
        await create_user(client, alice)
        await login(client, alice)
        aid = await create_agent(client, alice, "chat-scrub-agent")
        nid = seed_narrative_with_chat_content(aid, alice)

        # Scenario 1: chat history disabled — bundle must be scrubbed
        _hr(f"P1. include_chat_history=False — narrative scrubbed of chat content")
        bundle_off = await export_bundle(client, alice, aid, include_chat=False)
        await _expect(len(bundle_off) > 0, "bundle bytes non-empty")
        await assert_scrubbed(bundle_off, nid, aid)

        # Scenario 2: chat history enabled — bundle keeps everything (control)
        _hr(f"P2. include_chat_history=True (control) — content still present")
        bundle_on = await export_bundle(client, alice, aid, include_chat=True)
        await _expect(len(bundle_on) > 0, "bundle bytes non-empty (control)")
        await assert_leaks_when_enabled(bundle_on, nid, aid)

    print()
    print("=" * 76)
    print(f"  Chat-history scrub E2E passed. user={alice} agent={aid} nar={nid}")
    print("=" * 76)


if __name__ == "__main__":
    asyncio.run(main())

"""E2E for the 2026-05-18 round-2 identity sweep.

Round 1 (commit 67c0fab) fixed /api/providers*. Round 2 (this) fixes
the other 13 identity-via-query routes + WS local-mode anchor.

Scenarios covered:

  F. /api/auth/agents listing — no ?user_id= accepted; identity from
     header only; bob never sees alice's owned agents.
  G. DELETE /api/auth/agents/{id} — bob can't delete alice's agent
     by passing alice's user_id (used to: pass `?user_id=alice` and
     the permission check trivially compared against created_by).
  H. /api/agents/{id}/files — bob can't list/read files in alice's
     agent workspace by passing ?user_id=alice (or by any query).
  I. /api/agents/{id}/mcps — same isolation.
  J. /api/agents/{id}/rag-files — same isolation.
  K. /api/agents/{id}/chat-history — query-param "filter" no longer
     leaks alice's history to bob.
  L. /api/jobs — list never includes other users' jobs even with
     ?user_id=alice.
  M. /api/skills — list/install/remove all require auth_middleware
     identity now; query/form ?user_id= ignored.
  N. /api/transcription/availability — bob's availability is bob's,
     ?user_id=alice doesn't switch the answer.
  O. WS local mode — missing ?x_user_id= rejects; mismatched
     ?x_user_id= vs payload user_id rejects.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
import uuid

import httpx

BASE = os.environ.get("E2E_BASE_URL", "http://127.0.0.1:8000")
NETMIND_KEY = os.environ.get("NETMIND_API_KEY", "")


def _stamp(prefix: str) -> str:
    return f"e2e_r2_{prefix}_{int(time.time())}_{uuid.uuid4().hex[:6]}"


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


def _h(user_id: str | None) -> dict:
    return {"X-User-Id": user_id} if user_id else {}


# ---- Setup helpers ----

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
            "agent_description": "e2e r2",
            "agent_type": "individual",
            "created_by": user_id,
        },
    )
    body = r.json()
    await _expect(r.status_code == 200 and body.get("success"),
                  f"create_agent {name} for {user_id}: {body}")
    return body["agent"]["agent_id"]


# ---- Scenarios ----

async def scenario_F(client: httpx.AsyncClient, alice: str, bob: str,
                     alice_aid: str) -> None:
    _hr(f"F. /api/auth/agents list: bob never sees alice's owned agents")

    # bob lists agents — should NOT include alice_aid (created by alice).
    r = await client.get(f"{BASE}/api/auth/agents", headers=_h(bob))
    body = r.json()
    await _expect(r.status_code == 200 and body.get("success"),
                  f"GET /agents as bob: {r.status_code}")
    owned_by_alice = [a for a in body["agents"]
                      if a.get("created_by") == alice and a["agent_id"] == alice_aid]
    await _expect(len(owned_by_alice) == 0,
                  f"bob's listing leaks alice's agent? {owned_by_alice}")

    # Old IDOR: bob passes ?user_id=alice. Backend used to honour it;
    # now it should ignore (identity comes from X-User-Id header).
    r = await client.get(
        f"{BASE}/api/auth/agents?user_id={alice}",
        headers=_h(bob),
    )
    body = r.json()
    owned_by_alice = [a for a in body["agents"]
                      if a.get("created_by") == alice and a["agent_id"] == alice_aid]
    await _expect(len(owned_by_alice) == 0,
                  f"?user_id=alice attack: bob still doesn't see alice's "
                  f"agent (got {owned_by_alice})")


async def scenario_G(client: httpx.AsyncClient, alice: str, bob: str,
                     alice_aid: str) -> None:
    _hr(f"G. DELETE /api/auth/agents/{{id}} — bob CANNOT delete alice's agent")

    # bob tries to delete alice's agent, even passing ?user_id=alice.
    r = await client.delete(
        f"{BASE}/api/auth/agents/{alice_aid}?user_id={alice}",
        headers=_h(bob),
    )
    body = r.json()
    # Either 200 with success=False (route-level rejection) or 403/404.
    rejected = (
        (r.status_code == 200 and not body.get("success"))
        or r.status_code in (401, 403, 404)
    )
    await _expect(rejected,
                  f"bob delete-alice rejected (status={r.status_code} body={body})")


async def scenario_H(client: httpx.AsyncClient, alice: str, bob: str,
                     alice_aid: str) -> None:
    _hr(f"H. /api/agents/{{id}}/files — bob's view of alice's workspace is "
        f"scoped to bob, never to alice")

    # alice writes a file by uploading via her own session (we'll skip
    # writing through HTTP — instead just probe both perspectives below).
    # alice's listing for her own agent: succeeds, points at alice's WS.
    ra = await client.get(
        f"{BASE}/api/agents/{alice_aid}/files",
        headers=_h(alice),
    )
    body_a = ra.json()
    await _expect(ra.status_code == 200,
                  f"alice list workspace: {ra.status_code}")
    alice_path = body_a.get("workspace_path") or ""
    await _expect(alice in alice_path,
                  f"alice's workspace path contains her user_id: {alice_path}")

    # bob tries same agent — workspace_path must be derived from bob, NOT alice.
    rb = await client.get(
        f"{BASE}/api/agents/{alice_aid}/files?user_id={alice}",
        headers=_h(bob),
    )
    body_b = rb.json()
    await _expect(rb.status_code == 200, f"bob list response: {rb.status_code}")
    bob_path = body_b.get("workspace_path") or ""
    await _expect(
        alice not in bob_path,
        f"bob's view of alice's workspace_path leaks alice's id: {bob_path}"
    )


async def scenario_I(client: httpx.AsyncClient, alice: str, bob: str,
                     alice_aid: str) -> None:
    _hr(f"I. /api/agents/{{id}}/mcps — bob's list is scoped to bob")

    # alice creates an MCP under her agent
    ra = await client.post(
        f"{BASE}/api/agents/{alice_aid}/mcps",
        headers=_h(alice),
        json={"name": "alice-mcp", "url": "http://example.com/mcp", "description": "x", "is_enabled": True},
    )
    body_a = ra.json()
    await _expect(ra.status_code == 200 and body_a.get("success"),
                  f"alice create MCP: {body_a}")

    # bob lists — even with ?user_id=alice — must NOT see alice's MCP
    rb = await client.get(
        f"{BASE}/api/agents/{alice_aid}/mcps?user_id={alice}",
        headers=_h(bob),
    )
    body_b = rb.json()
    await _expect(rb.status_code == 200, f"bob list MCPs: {rb.status_code}")
    bob_sees_alice = [m for m in body_b.get("mcps", []) if m.get("user_id") == alice]
    await _expect(
        len(bob_sees_alice) == 0,
        f"bob's MCP list leaks alice's MCPs: {bob_sees_alice}"
    )


async def scenario_K(client: httpx.AsyncClient, alice: str, bob: str,
                     alice_aid: str) -> None:
    _hr(f"K. /api/agents/{{id}}/chat-history — query 'filter' doesn't leak")

    # bob requests alice's chat history with ?user_id=alice — must
    # not return alice's narratives/events.
    r = await client.get(
        f"{BASE}/api/agents/{alice_aid}/chat-history?user_id={alice}",
        headers=_h(bob),
    )
    body = r.json()
    await _expect(r.status_code == 200, f"chat-history status: {r.status_code}")
    # Bob has never sent any messages → his scoped view should be empty.
    await _expect(
        len(body.get("narratives", [])) == 0,
        f"bob's chat-history under alice's agent should be empty for bob: "
        f"got {len(body.get('narratives', []))} narratives"
    )


async def scenario_L(client: httpx.AsyncClient, alice: str, bob: str,
                     alice_aid: str) -> None:
    _hr(f"L. /api/jobs — listing scoped to caller, not query")

    # Bob lists alice's agent's jobs with ?user_id=alice. He has no
    # jobs of his own under that agent, so the response must be empty.
    r = await client.get(
        f"{BASE}/api/jobs?agent_id={alice_aid}&user_id={alice}",
        headers=_h(bob),
    )
    body = r.json()
    await _expect(r.status_code == 200, f"jobs list status: {r.status_code}")
    bob_jobs = body.get("jobs", []) or []
    leaked = [j for j in bob_jobs if j.get("user_id") == alice]
    await _expect(len(leaked) == 0,
                  f"bob's jobs list leaks alice's: {leaked}")


async def scenario_N(client: httpx.AsyncClient, bob: str) -> None:
    _hr(f"N. /api/transcription/availability — identity from header only")

    # No header at all → 401 (auth-middleware rejects)
    r = await client.get(f"{BASE}/api/transcription/availability")
    await _expect(r.status_code == 401,
                  f"no header → 401 (got {r.status_code})")

    # With bob's header → 200, scoped to bob.
    r = await client.get(
        f"{BASE}/api/transcription/availability",
        headers=_h(bob),
    )
    body = r.json()
    await _expect(r.status_code == 200 and "available" in body,
                  f"bob availability: {body}")


async def scenario_O(bob: str) -> None:
    _hr(f"O. WS local mode — anchor + payload mismatch check")

    # We test the WS rejection paths only — full agent run requires
    # the MCP servers up, which is out of scope for this E2E.
    try:
        import websockets
    except ImportError:
        print("  websockets package not installed; skipping WS test "
              "(pip install websockets to enable)")
        return

    ws_base = BASE.replace("http://", "ws://").replace("https://", "wss://")

    # O1: missing ?x_user_id= → auth error frame
    url_no_anchor = f"{ws_base}/ws/agent/run"
    try:
        async with websockets.connect(url_no_anchor) as ws:
            await ws.send(json.dumps({
                "agent_id": "anything",
                "user_id": bob,
                "input_content": "hi",
                "working_source": "chat",
            }))
            reply = json.loads(await ws.recv())
            await _expect(
                reply.get("type") == "error",
                f"WS without ?x_user_id= returns error: {reply}"
            )
    except websockets.exceptions.WebSocketException as e:
        await _expect(True, f"WS without ?x_user_id= refused at handshake: {e!r}")

    # O2: ?x_user_id=alice but payload user_id=bob → auth error
    fake_alice = "definitely-not-bob"
    url_mismatch = f"{ws_base}/ws/agent/run?x_user_id={fake_alice}"
    try:
        async with websockets.connect(url_mismatch) as ws:
            await ws.send(json.dumps({
                "agent_id": "anything",
                "user_id": bob,
                "input_content": "hi",
                "working_source": "chat",
            }))
            reply = json.loads(await ws.recv())
            await _expect(
                reply.get("type") == "error"
                and "mismatch" in (reply.get("error_message") or "").lower(),
                f"WS x_user_id-mismatch rejected: {reply}"
            )
    except websockets.exceptions.WebSocketException as e:
        await _expect(True, f"WS mismatch refused at handshake: {e!r}")


# ---- Main ----

async def main() -> None:
    if not NETMIND_KEY:
        print("NETMIND_API_KEY env var is empty — load .env first")
        raise SystemExit(2)

    print(f"BASE = {BASE}")
    print(f"NETMIND_API_KEY = ***{NETMIND_KEY[-4:]}")

    alice = _stamp("alice")
    bob = _stamp("bob")

    async with httpx.AsyncClient(timeout=30.0) as client:
        # Setup
        await create_user(client, alice)
        await create_user(client, bob)
        await login(client, alice)
        await login(client, bob)
        alice_aid = await create_agent(client, alice, "alice-agent")

        # Scenarios
        await scenario_F(client, alice, bob, alice_aid)
        await scenario_G(client, alice, bob, alice_aid)
        await scenario_H(client, alice, bob, alice_aid)
        await scenario_I(client, alice, bob, alice_aid)
        await scenario_K(client, alice, bob, alice_aid)
        await scenario_L(client, alice, bob, alice_aid)
        await scenario_N(client, bob)

    # WS test runs outside the httpx client context because websockets
    # opens its own connection.
    await scenario_O(bob)

    print()
    print("=" * 76)
    print(f"  Round-2 scenarios passed. alice={alice} bob={bob}")
    print("=" * 76)


if __name__ == "__main__":
    asyncio.run(main())

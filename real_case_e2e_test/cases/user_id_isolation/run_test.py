"""End-to-end test for the 2026-05-18 cross-user write/read bug fix.

Scenarios covered:

  A. Identity required: hit /api/providers with NO X-User-Id → expect 401
     (the fallback to "first user in users table" must be gone)
  B. Fresh user creates a NetMind provider → row lands under the fresh
     user's user_id, NOT under any older account (this is the core bug)
  C. Cross-user isolation on read: a second fresh user listing
     /api/providers must see an EMPTY config, not the first user's
  D. Agent under the fresh user resolves all three LLM slots correctly
     (no LLMConfigNotConfigured) once slots are bound
  E. Identity-via-query attack: trying ?user_id=alice while logged in
     as bob no longer succeeds (query param is ignored entirely)

This script talks to a running backend at http://127.0.0.1:8000. Start
the backend with `uvicorn backend.main:app --port 8000` before running.
"""
from __future__ import annotations

import asyncio
import os
import time
import uuid

import httpx

BASE = os.environ.get("E2E_BASE_URL", "http://127.0.0.1:8000")
NETMIND_KEY = os.environ.get("NETMIND_API_KEY", "")


def _stamp(prefix: str) -> str:
    return f"e2e_{prefix}_{int(time.time())}_{uuid.uuid4().hex[:6]}"


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


async def create_user(client: httpx.AsyncClient, user_id: str) -> None:
    r = await client.post(
        f"{BASE}/api/auth/create-user",
        json={"user_id": user_id, "display_name": user_id},
    )
    body = r.json()
    await _expect(r.status_code == 200 and body.get("success"),
                  f"create-user {user_id}: {r.status_code} body={body}")


async def login(client: httpx.AsyncClient, user_id: str) -> None:
    r = await client.post(f"{BASE}/api/auth/login", json={"user_id": user_id})
    body = r.json()
    await _expect(r.status_code == 200 and body.get("success"),
                  f"login {user_id}: {r.status_code} body={body}")


def _headers(user_id: str | None) -> dict:
    return {"X-User-Id": user_id} if user_id else {}


async def scenario_A_no_identity(client: httpx.AsyncClient) -> None:
    _hr("A. Identity required: /api/providers without X-User-Id → 401")
    r = await client.get(f"{BASE}/api/providers")
    await _expect(
        r.status_code == 401,
        f"GET /api/providers without header returns 401 (got {r.status_code})",
    )
    body = r.json()
    await _expect(
        "X-User-Id" in (body.get("detail") or ""),
        f"401 body mentions missing header: {body}",
    )


async def scenario_B_fresh_user_writes_to_self(
    client: httpx.AsyncClient, user_id: str
) -> str:
    _hr(f"B. Fresh user {user_id!r} adds NetMind → rows land under self")

    # Sanity: list providers (should be empty)
    r = await client.get(f"{BASE}/api/providers", headers=_headers(user_id))
    body = r.json()
    await _expect(r.status_code == 200, f"GET /api/providers as {user_id}: {r.status_code}")
    await _expect(
        body["data"]["providers"] == {},
        f"providers must be empty for fresh user (got {len(body['data']['providers'])})",
    )

    # Add NetMind with default_slots auto-fill (this is the flow the UI
    # uses for "Quick Add")
    payload = {
        "card_type": "netmind",
        "api_key": NETMIND_KEY,
        "default_slots": {
            "agent": {"protocol": "anthropic", "model": "anthropic/claude-opus-4-7"},
            "helper_llm": {"protocol": "openai", "model": "deepseek-ai/DeepSeek-V4-Flash"},
            "embedding": {"protocol": "openai", "model": "BAAI/bge-m3"},
        },
    }
    r = await client.post(
        f"{BASE}/api/providers",
        headers=_headers(user_id),
        json=payload,
    )
    body = r.json()
    await _expect(
        r.status_code == 200 and body.get("success"),
        f"POST /api/providers as {user_id}: status={r.status_code} body={body}",
    )
    provider_ids = body.get("provider_ids", [])
    await _expect(
        len(provider_ids) == 2,
        f"NetMind creates 2 providers (anthropic + openai), got {len(provider_ids)}",
    )

    # Re-read to confirm slots got bound (default_slots in the same call)
    r = await client.get(f"{BASE}/api/providers", headers=_headers(user_id))
    body = r.json()
    slots = body["data"]["slots"]
    for slot_name in ("agent", "helper_llm", "embedding"):
        await _expect(
            slots[slot_name]["config"] is not None,
            f"slot {slot_name!r} bound after Quick Add",
        )

    return provider_ids[0]


async def scenario_C_cross_user_isolation(
    client: httpx.AsyncClient, user_a: str, user_b: str
) -> None:
    _hr(f"C. Cross-user isolation: {user_b!r} must see EMPTY config, not {user_a!r}'s")

    # user_b should see empty providers and empty slots even though
    # user_a just configured everything
    r = await client.get(f"{BASE}/api/providers", headers=_headers(user_b))
    body = r.json()
    await _expect(r.status_code == 200, f"GET /api/providers as {user_b}: {r.status_code}")
    await _expect(
        body["data"]["providers"] == {},
        f"{user_b!r} sees 0 providers (got {len(body['data']['providers'])}): leak from {user_a!r}!"
        if body["data"]["providers"] else "providers empty for second fresh user",
    )
    for slot_name in ("agent", "helper_llm", "embedding"):
        await _expect(
            body["data"]["slots"][slot_name]["config"] is None,
            f"slot {slot_name!r} empty for {user_b!r}",
        )


async def scenario_D_validate_slots(
    client: httpx.AsyncClient, user_id: str
) -> None:
    _hr(f"D. Validate slots for {user_id!r}: must all be configured")
    r = await client.get(
        f"{BASE}/api/providers/slots/validate",
        headers=_headers(user_id),
    )
    body = r.json()
    await _expect(r.status_code == 200, f"validate status {r.status_code}")
    await _expect(
        body.get("all_configured") is True,
        f"all_configured=True (got {body})",
    )


async def scenario_E_query_identity_attack(
    client: httpx.AsyncClient, attacker_user_id: str, target_user_id: str
) -> None:
    """Trying ?user_id=<target> while authenticated as <attacker> must
    not return <target>'s data."""
    _hr(
        f"E. Identity-via-query attack: as {attacker_user_id!r} hitting "
        f"?user_id={target_user_id} must NOT leak {target_user_id!r}'s data"
    )
    # Try via query string. Backend should ignore the query and use the
    # X-User-Id header (= attacker_user_id), so the response should
    # contain the attacker's (empty) config, not the target's.
    r = await client.get(
        f"{BASE}/api/providers?user_id={target_user_id}",
        headers=_headers(attacker_user_id),
    )
    body = r.json()
    await _expect(r.status_code == 200, f"GET /api/providers status {r.status_code}")
    await _expect(
        body["data"]["providers"] == {},
        f"query string user_id is IGNORED — attacker sees their own empty config, "
        f"not target's data. (Got {len(body['data']['providers'])} providers.)",
    )


async def main() -> None:
    if not NETMIND_KEY:
        print("NETMIND_API_KEY env var is empty — load .env first")
        raise SystemExit(2)
    print(f"BASE = {BASE}")
    print(f"NETMIND_API_KEY = ***{NETMIND_KEY[-4:]}")

    user_a = _stamp("alice")  # primary test user
    user_b = _stamp("bob")    # isolation peer

    async with httpx.AsyncClient(timeout=30.0) as client:
        # Setup
        await create_user(client, user_a)
        await create_user(client, user_b)
        await login(client, user_a)
        await login(client, user_b)

        # Scenarios
        await scenario_A_no_identity(client)
        prov_a = await scenario_B_fresh_user_writes_to_self(client, user_a)
        await scenario_C_cross_user_isolation(client, user_a, user_b)
        await scenario_D_validate_slots(client, user_a)
        await scenario_E_query_identity_attack(
            client, attacker_user_id=user_b, target_user_id=user_a
        )

    print()
    print("=" * 76)
    print(f"  All scenarios passed. user_a={user_a} user_b={user_b}")
    print("=" * 76)


if __name__ == "__main__":
    asyncio.run(main())

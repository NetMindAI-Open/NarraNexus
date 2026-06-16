"""
@file_name: verify_arena_provision.py
@author: Bin Liang
@date: 2026-06-15
@description: End-to-end verification of ArenaProvisioningService against the
              real local stack (sqlite + real workspace + real Arena API).
              Exercises the full pipeline and asserts every artifact, then
              re-runs to prove idempotency. Prints per-step timings.

Usage:
    uv run python scripts/verify_arena_provision.py
    uv run python scripts/verify_arena_provision.py --user-id binliang
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, "src")

from xyz_agent_context.settings import settings  # noqa: E402
from xyz_agent_context.utils.db_factory import get_db_client  # noqa: E402


def _check(cond: bool, label: str) -> None:
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
    if not cond:
        raise SystemExit(f"FAILED: {label}")


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--user-id", default="binliang")
    args = ap.parse_args()
    user_id = args.user_id

    db = await get_db_client()
    from xyz_agent_context.utils.schema_registry import auto_migrate
    await auto_migrate(db._backend)

    # Ensure the user exists (provision attaches created_by=user_id).
    from xyz_agent_context.repository.user_repository import UserRepository
    urepo = UserRepository(db)
    if not await urepo.get_user(user_id):
        await urepo.add_user(user_id=user_id, user_type="local", display_name=user_id)
        print(f"  (created local user {user_id})")

    # Clean any prior arena agent for this user so we exercise the cold path.
    # Idempotency now keys on the agents table (no credentials table).
    prior = await db.get("agents", filters={"created_by": user_id})
    cleared = 0
    for a in prior:
        md = json.loads(a.get("agent_metadata") or "{}")
        if md.get("provisioned_source") == "arena":
            await db.delete("agents", {"agent_id": a["agent_id"]})
            cleared += 1
    if cleared:
        print(f"  (cleared {cleared} prior arena agent(s) for cold-path test)")

    from xyz_agent_context.services.arena_provisioning_service import ArenaProvisioningService
    svc = ArenaProvisioningService(db)

    print("=" * 64)
    print("VERIFY ArenaProvisioningService — COLD provision")
    print("=" * 64)
    res = await svc.provision(user_id)
    print(json.dumps(res, indent=2, default=str))

    agent_id = res["agent_id"]
    print("\n--- assertions ---")
    _check(res["success"] and not res["reused"], "cold provision succeeded (not reused)")
    _check(res["status"] == "provisioned", "status == provisioned")

    # 1. agent row, named with the gamertag
    agent = await db.get_one("agents", {"agent_id": agent_id})
    _check(agent is not None, "agent row exists")
    _check(agent["agent_name"] == res["arena_name"], "agent_name == arena gamertag")
    meta = json.loads(agent.get("agent_metadata") or "{}")
    _check(meta.get("provisioned_source") == "arena", "agent_metadata.provisioned_source == arena")
    _check(bool(meta.get("bootstrap_greeting")), "agent_metadata.bootstrap_greeting set")

    # 2. instances (incl. AwarenessModule) + awareness text
    insts = await db.get("module_instances", filters={"agent_id": agent_id})
    classes = {i["module_class"] for i in insts}
    _check("AwarenessModule" in classes, f"AwarenessModule instance exists ({sorted(classes)})")
    aware_inst = next(i for i in insts if i["module_class"] == "AwarenessModule")
    aw = await db.get_one("instance_awareness", {"instance_id": aware_inst["instance_id"]})
    _check(aw is not None and res["arena_name"] in aw["awareness"], "awareness mentions gamertag")
    _check("arena42.ai" in aw["awareness"], "awareness mentions arena")

    # 3. arena identity in agent_metadata (no credentials table); the api_key is
    #    NOT in the DB — it lives only in the workspace.
    _check(meta.get("arena_agent_id") == res["arena_agent_id"],
           "agent_metadata.arena_agent_id matches")
    _check(meta.get("arena_agent_name") == res["arena_name"],
           "agent_metadata.arena_agent_name matches")
    full_meta = json.dumps(meta)
    _check("arena_sk_" not in full_meta, "api_key NOT in agent_metadata (secret stays in workspace)")

    # 4. workspace skill files
    ws = Path(settings.base_working_path) / f"{agent_id}_{user_id}"
    sd = ws / "skills" / "arena"
    for f in ("SKILL.md", ".skill_meta.json", "credentials.json", "arena_profile.json"):
        _check((sd / f).exists(), f"workspace skill file {f} written")
    _check((sd / "credentials.json").stat().st_mode & 0o777 == 0o600, "credentials.json is chmod 0600")

    # 5. Arena Bootstrap.md
    bs = ws / "Bootstrap.md"
    _check(bs.exists(), "Arena Bootstrap.md written")
    bs_text = bs.read_text()
    _check(res["arena_name"] in bs_text and "PAUSED" in bs_text, "Bootstrap.md is Arena-flavored")

    # 6. three PAUSED jobs
    jobs = await db.get("instance_jobs", filters={"agent_id": agent_id})
    _check(len(jobs) == 3, f"3 jobs created (got {len(jobs)})")
    _check(all(j["status"] == "paused" for j in jobs), "all jobs status == paused")
    titles = {j["title"] for j in jobs}
    _check({"Arena heartbeat", "Arena competition scan", "Arena inbox check"} == titles,
           f"job titles correct ({titles})")
    # none should be due-fireable while paused
    print(f"    job next_run_times: {[j['next_run_time'] for j in jobs]}")

    # 7. SkillModule round-trip (runtime consumes the files)
    from xyz_agent_context.module.skill_module.skill_module import SkillModule
    sm = SkillModule(agent_id=agent_id, user_id=user_id, database_client=db)
    env = sm.get_all_skill_env_vars()
    _check(env.get("ARENA_API_KEY", "").startswith("arena_sk_"),
           "SkillModule decodes a real ARENA_API_KEY")

    # 8. timings
    print(f"\n  TIMINGS (ms): {json.dumps(res['timings_ms'])}")
    print(f"  >>> total create→arena-done: {res['timings_ms']['total']} ms")

    # 9. idempotency — WARM re-run returns same agent fast
    print("\n" + "=" * 64)
    print("VERIFY — WARM re-run (idempotency)")
    print("=" * 64)
    res2 = await svc.provision(user_id)
    print(json.dumps(res2, indent=2, default=str))
    _check(res2["reused"] and res2["status"] == "reused", "warm path reused")
    _check(res2["agent_id"] == agent_id, "warm path returns the same agent_id")
    _check(res2["timings_ms"]["total"] < res["timings_ms"]["total"], "warm path is faster")
    user_agents = await db.get("agents", filters={"created_by": user_id})
    arena_agents = [a for a in user_agents
                    if json.loads(a.get("agent_metadata") or "{}").get("provisioned_source") == "arena"]
    _check(len(arena_agents) == 1, "still exactly one arena agent (no duplicate)")

    print("\n=== ALL CHECKS PASSED ===")
    print(f"agent_id={agent_id} arena={res['arena_name']} ({res['arena_agent_id']})")


if __name__ == "__main__":
    asyncio.run(main())

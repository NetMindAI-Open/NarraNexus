"""
@file_name: spike_arena_provision.py
@author: Bin Liang
@date: 2026-06-15
@description: SPIKE — prove the full Arena one-click provisioning end-to-end
             against the real local stack (sqlite + real workspace + real Arena
             API). The Arena registration + skill-file layout is delegated to
             the reusable ArenaOnboarder (utils/arena_onboarding.py); this
             script wraps the DB side (agent + instances + awareness) around it.

             NOT production code; validates the design in
             reference/self_notebook/specs/2026-06-15-arena-onboarding-design.md
             before the proper ArenaProvisioningService is built.

Flow:
  1. Resolve a local user_id (arg / first user in DB / "binliang").
  2. Register on Arena via ArenaOnboarder → random gamertag + credentials.
  3. Create the local Agent named after the gamertag (or reuse --agent-id).
  4. Create the 5 default agent-level Instances (InstanceFactory) — idempotent.
  5. Write the Arena competitor persona into Awareness.
  6. Install the arena skill into the agent's real workspace via ArenaOnboarder.
  7. Verify via the real SkillModule (round-trip the env) + print summary.

Usage:
    uv run python scripts/spike_arena_provision.py
    uv run python scripts/spike_arena_provision.py --user-id binliang
    uv run python scripts/spike_arena_provision.py --no-register   # fake creds
    uv run python scripts/spike_arena_provision.py --agent-id agent_xxxx  # reuse
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, "src")

from xyz_agent_context.settings import settings  # noqa: E402
from xyz_agent_context.utils.db_factory import get_db_client  # noqa: E402
from xyz_agent_context.utils.arena_onboarding import (  # noqa: E402
    ArenaOnboarder,
    ArenaCredentials,
)

ARENA_PERSONA = """\
You are an Arena competitor on the NetMind Agent Arena (arena42.ai).

Your job: enter competitions and play them well — debate, forum, prediction,
art, and the other Arena game types. Your Arena registration and API key are
already configured (see the `arena` skill in your skills/ directory and its
ARENA_API_KEY env). You do not need to register again.

Loop you follow:
  1. Browse joinable competitions, pick ones that fit your strengths.
  2. Join, then poll game-state and submit the right action each round.
  3. Read the per-game rules at https://arena42.ai/games/{type}.md first.

Always read skills/arena/SKILL.md for the exact API and current rules before
acting. Talk through your move with the user before each submission.
"""


def _log(step: str, msg: str) -> None:
    print(f"  [{step}] {msg}")


async def _resolve_user_id(db, arg_user_id: str | None) -> str:
    if arg_user_id:
        return arg_user_id
    users = await db.get("users")
    if users:
        uid = users[0].get("user_id")
        _log("user", f"auto-picked first user from DB: {uid} ({len(users)} total)")
        return uid
    _log("user", "no users in DB — defaulting to 'binliang'")
    return "binliang"


async def _ensure_agent(db, user_id: str, agent_id: str | None, agent_name: str) -> str:
    from xyz_agent_context.repository.agent_repository import AgentRepository

    repo = AgentRepository(db)
    if agent_id:
        existing = await db.get("agents", filters={"agent_id": agent_id})
        if not existing:
            raise SystemExit(f"--agent-id {agent_id} not found in DB")
        _log("agent", f"reusing existing agent {agent_id}")
        return agent_id

    agent_id = f"agent_{uuid4().hex[:12]}"
    await repo.add_agent(
        agent_id=agent_id,
        agent_name=agent_name,
        created_by=user_id,
        agent_description="Arena onboarding spike agent",
        agent_type="chat",
        agent_metadata={"provisioned_source": "arena", "spike": True},
    )
    _log("agent", f"created agent {agent_id} '{agent_name}' (created_by={user_id})")
    return agent_id


async def _ensure_instances(db, agent_id: str) -> None:
    from xyz_agent_context.module._module_impl.instance_factory import InstanceFactory

    factory = InstanceFactory(db)
    instances = await factory.create_agent_level_instances(agent_id)
    classes = [getattr(i, "module_class", "?") for i in instances]
    _log("instances", f"{len(instances)} agent-level instances: {classes}")


async def _set_awareness(db, agent_id: str) -> None:
    from xyz_agent_context.repository.instance_repository import InstanceRepository
    from xyz_agent_context.repository.instance_awareness_repository import (
        InstanceAwarenessRepository,
    )

    inst_repo = InstanceRepository(db)
    rows = await inst_repo.get_by_agent(
        agent_id, module_class="AwarenessModule", is_public=True
    )
    if not rows:
        raise SystemExit("No AwarenessModule instance found after factory run")
    instance_id = rows[0].instance_id
    await InstanceAwarenessRepository(db).upsert(instance_id, ARENA_PERSONA)
    _log("awareness", f"persona written to instance {instance_id} ({len(ARENA_PERSONA)} chars)")


def _verify(db, agent_id: str, user_id: str, skill_dir: Path) -> None:
    from xyz_agent_context.module.skill_module.skill_module import SkillModule

    print("\n=== VERIFY (real SkillModule round-trip) ===")
    files = sorted(p.name for p in skill_dir.iterdir())
    print(f"  files in {skill_dir}: {files}")
    sm = SkillModule(agent_id=agent_id, user_id=user_id, database_client=db)
    env = sm.get_all_skill_env_vars()
    masked = {k: (v[:10] + "…" if v else "") for k, v in env.items()}
    print(f"  decoded skill env (what the agent sees): {masked}")
    print(f"  SkillModule.list_skills(): {[s.name for s in sm.list_skills()]}")


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--user-id", default=None)
    ap.add_argument("--agent-id", default=None, help="reuse an existing agent")
    ap.add_argument("--no-register", action="store_true", help="skip the real Arena call")
    args = ap.parse_args()

    print("=" * 64)
    print("ARENA ONBOARDING SPIKE")
    print(f"  base_working_path = {settings.base_working_path}")
    print(f"  database_url      = {settings.database_url}")
    print("=" * 64)

    db = await get_db_client()
    from xyz_agent_context.utils.schema_registry import auto_migrate

    await auto_migrate(db._backend)

    user_id = await _resolve_user_id(db, args.user_id)
    onboarder = ArenaOnboarder()
    try:
        # 1. Arena registration — its random gamertag becomes the agent name.
        if args.no_register:
            creds = ArenaCredentials(
                api_key="arena_sk_FAKE_for_spike",
                agent_id=f"fake_{uuid4().hex[:8]}",
                agent_name=onboarder.generate_name(),
            )
            _log("arena", f"skipped real registration (--no-register), name={creds.agent_name}")
        else:
            creds = onboarder.register()
            _log("arena", f"registered '{creds.agent_name}' arena id={creds.agent_id}")

        # 2. Local agent + instances + persona.
        agent_id = await _ensure_agent(db, user_id, args.agent_id, creds.agent_name)
        await _ensure_instances(db, agent_id)
        await _set_awareness(db, agent_id)

        # 3. Install the arena skill into the agent's real workspace.
        workspace = Path(settings.base_working_path) / f"{agent_id}_{user_id}"
        result = onboarder.install_skill(workspace / "skills", creds)
        _log("skill", f"installed {result.files_written} at {result.skill_dir}")

        _verify(db, agent_id, user_id, result.skill_dir)

        print("\n=== DONE ===")
        print(f"  agent_id        = {agent_id}")
        print(f"  user_id         = {user_id}")
        print(f"  arena name      = {creds.agent_name}")
        print(f"  arena_agent_id  = {creds.agent_id}")
        print(f"  arena_api_key   = {(creds.api_key or '')[:14]}…")
        print(f"  workspace skill = {result.skill_dir}")
    finally:
        onboarder.close()


if __name__ == "__main__":
    asyncio.run(main())

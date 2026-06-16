"""
@file_name: verify_arena_onboarding.py
@author: Bin Liang
@date: 2026-06-15
@description: Verify ArenaOnboarder (utils/arena_onboarding.py) is usable from
             an external script: pass a workspace path → a whole Arena setup is
             registered and laid down. Then prove the written files are
             consumable by the REAL SkillModule, and that the key is live.

Usage:
    uv run python scripts/verify_arena_onboarding.py
    uv run python scripts/verify_arena_onboarding.py --no-register   # offline file-layout check
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, "src")

from xyz_agent_context.utils.arena_onboarding import ArenaOnboarder, ArenaCredentials  # noqa: E402


def _check(cond: bool, label: str) -> None:
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
    if not cond:
        raise SystemExit(f"verification failed: {label}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-register", action="store_true")
    args = ap.parse_args()

    # An arbitrary agent workspace, exactly the shape SkillModule expects:
    # {base}/{agent_id}_{user_id}/skills/<skill>/
    agent_id, user_id = "agent_verify0001", "binliang"
    tmp_base = Path(tempfile.mkdtemp(prefix="arena_verify_"))
    workspace = tmp_base / f"{agent_id}_{user_id}"
    workspace.mkdir(parents=True)

    print("=" * 60)
    print("VERIFY ArenaOnboarder")
    print(f"  workspace = {workspace}")
    print("=" * 60)

    onboarder = ArenaOnboarder()
    try:
        if args.no_register:
            creds = ArenaCredentials(
                api_key="arena_sk_FAKE0000000000",
                agent_id="agent_FAKEVERIFY",
                agent_name="Brave_Frost_Fox",
            )
            skill_md = "# Arena (offline stub)\n"
            result = onboarder.install_skill(workspace / "skills", creds, skill_md=skill_md)
        else:
            # The headline use-case: one call, just a workspace path.
            result = onboarder.onboard(workspace, verify=True)
            creds = result.credentials

        print("\n--- onboard result ---")
        print(f"  arena name      = {creds.agent_name}")
        print(f"  arena agent_id  = {creds.agent_id}")
        print(f"  api_key         = {(creds.api_key or '')[:14]}…")
        print(f"  skill_dir       = {result.skill_dir}")
        print(f"  files_written   = {result.files_written}")

        print("\n--- assertions ---")
        sd = result.skill_dir
        _check(sd.exists() and sd.name == "arena", "skill dir created at skills/arena")
        _check((sd / "SKILL.md").stat().st_size > 0, "SKILL.md non-empty")
        meta = json.loads((sd / ".skill_meta.json").read_text())
        _check("ARENA_API_KEY" in meta.get("env_config", {}), "env_config has ARENA_API_KEY (base64)")
        _check(meta["source_type"] == "skill_md_url", "meta.source_type == skill_md_url")
        cj = json.loads((sd / "credentials.json").read_text())
        _check(cj.get("agent_id") == creds.agent_id, "credentials.json agent_id matches")

        # The real test: does the running agent's SkillModule consume these files?
        from xyz_agent_context.module.skill_module.skill_module import SkillModule

        sm = SkillModule(agent_id=agent_id, user_id=user_id, database_client=None)
        sm.skills_dir = workspace / "skills"  # point at our temp workspace
        listed = [s.name for s in sm.list_skills()]
        _check("arena" in listed, f"SkillModule.list_skills() sees 'arena' -> {listed}")
        env = sm.get_all_skill_env_vars()
        _check(env.get("ARENA_API_KEY") == creds.api_key,
               "SkillModule decodes ARENA_API_KEY back to the real key")

        if not args.no_register:
            me = onboarder.verify_credentials(creds)
            _check(me.get("status") == "active", f"GET /agents/me -> active (credits={me.get('credits')})")
            _check(me.get("id") == creds.agent_id, "/agents/me id matches registered id")

        print("\n=== ALL CHECKS PASSED ===")
    finally:
        onboarder.close()


if __name__ == "__main__":
    main()

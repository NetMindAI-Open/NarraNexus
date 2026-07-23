"""
@file_name: m0003_cloud_codex_to_claude.py
@author: NetMind.AI
@date: 2026-07-16
@description: Migration 0003 — cloud-only reset of the ``codex_cli`` agent
framework to ``claude_code``.

Why: cloud policy became "Claude Code + NetMind key only". Users who had
previously chosen the ``codex_cli`` framework are dead-locked — their persisted
``user_slots[slot_name='agent'].agent_framework = 'codex_cli'`` needs an
OpenAI-protocol agent slot, but the NetMind onboard binds the agent slot to the
ANTHROPIC leg, so the runtime protocol check fails and the agent can't start;
and the framework-switch gate was direction-insensitive, so they couldn't flip
back either. Flipping the framework value to ``claude_code`` makes the existing
anthropic slot consistent (claude_code → anthropic), unlocking them.

Why a migration (not a login hook): a login hook only reaches users who
re-authenticate; a user with a still-valid session would stay locked until the
token expires. This runs once at deploy and fixes EVERY existing user with no
re-login. The cloud policy prevents new ``codex_cli`` users, so no ongoing hook
is needed.

CLOUD-ONLY (binding rule #7): the migration runner also runs on ``bash run.sh``
/ DMG, where a user may legitimately run ``codex_cli`` with their own key —
those must NEVER be flipped. The first line no-ops outside cloud mode.

Idempotent + non-destructive (binding rule #6): plain value updates guarded by
``agent_framework='codex_cli'``; a re-run matches zero rows. Only the ``agent``
slot is touched — ``helper_llm`` is left alone (its protocol requirement is
looser and it is not the blocker).

BOTH framework layers are flipped (binding rule #8 — sweep the neighbours):
``user_slots`` (the owner default) AND ``agent_slots`` (the per-agent override,
which step_3 resolves FIRST). A user whose per-agent override was left on
``codex_cli`` would otherwise stay locked even after the owner default flips.

Dual-dialect via ``db.update`` (both filters are equality) rather than
hand-written SQL — the client's UPDATE returns the affected-row count on both
the SQLite and MySQL backends, so this runs correctly on the cloud MySQL it
actually executes against without a separate dialect test.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, TYPE_CHECKING

from loguru import logger

from xyz_agent_context.utils.deployment_mode import is_cloud_mode

from . import Migration

if TYPE_CHECKING:
    from xyz_agent_context.utils.database import AsyncDatabaseClient


async def _apply(db: "AsyncDatabaseClient") -> Dict:
    # Cloud gate FIRST — local/DMG users legitimately run codex; never flip them.
    if not is_cloud_mode():
        return {"skipped": "local mode"}

    now = datetime.now(timezone.utc).isoformat()
    flip = {"agent_framework": "claude_code", "updated_at": now}
    where = {"slot_name": "agent", "agent_framework": "codex_cli"}

    users = await db.update("user_slots", where, flip)       # owner default
    agents = await db.update("agent_slots", where, flip)     # per-agent override
    logger.info(
        f"[migrate 0003] cloud codex_cli → claude_code: "
        f"{users} user_slots + {agents} agent_slots flipped"
    )
    return {"migrated_user_slots": users, "migrated_agent_slots": agents}


MIGRATION = Migration(
    id="0003_cloud_codex_to_claude",
    description="Cloud-only reset of codex_cli agent framework to claude_code",
    apply=_apply,
)

"""
@file_name: lifecycle.py
@author: Bin Liang
@date: 2026-08-20
@description: The bootstrap-phase judgment shared by the two BACKEND greeting
writers — the step_1 seed and ChatModule.persist_turn — and by
context_runtime's Bootstrap prompt injection/auto-delete.

Scope caveat: this is the definition for those three, NOT for the whole repo.
`backend/routes/auth.py` computes a SEPARATE, looser `AgentInfo.bootstrap_active`
that the FRONTEND trusts (it gates the static greeting bubble): a plain
`os.path.isfile(Bootstrap.md)` with NO event-count threshold, because a list
endpoint can't afford a per-agent COUNT. The two agree everywhere except the
narrow "over threshold but Bootstrap.md not yet auto-deleted" window, where the
frontend may show the overlay while these writers decline to persist. Unifying
them needs the list endpoint's N+1 solved first (tracked in
reference/self_notebook/todo); until then, touch both together.

`bootstrap_active` used to live inline in context_runtime (inject the
Bootstrap.md prompt) AND, once the step_1 greeting seed appeared, needed to be
recomputed identically in bootstrap/greeting_seed so the two writers stay in
lockstep. Copying it (workspace resolver + Bootstrap.md check + event-count
threshold, raw SQL and all) meant a silent drift risk: change the threshold
semantics or the workspace layout on one side and an agent past its bootstrap
phase gets re-greeted on a new narrative while the runtime thinks it's done.

This module owns that judgment. context_runtime and greeting_seed both call
`is_bootstrap_active`; context_runtime keeps its own auto-delete side-effect
(removing Bootstrap.md when over threshold) and uses the returned
`event_count`/`threshold`/`bootstrap_path` to do it — the helper decides, the
runtime acts. `bootstrap/` is the home of bootstrap semantics: the runtime LOGIC
module imports this judgment, not the reverse. (The only pre-existing
bootstrap→context_runtime edge is profiles.py importing the `context_runtime.prompts`
constants leaf — a leaf, no cycle; untouched here.)
"""
from __future__ import annotations

import os
from typing import TYPE_CHECKING, NamedTuple, Optional

from loguru import logger

from narranexus.platform.bootstrap.profiles import auto_delete_threshold_from_meta
from narranexus.platform.utils.workspace_paths import resolve_existing_workspace

if TYPE_CHECKING:
    from narranexus.platform.utils.db.database import AsyncDatabaseClient


class BootstrapStatus(NamedTuple):
    """Result of a bootstrap-phase check.

    - active: Bootstrap.md present AND under the auto-delete threshold (the exact
      condition under which context_runtime injects the prompt and either writer
      greets).
    - present: Bootstrap.md exists on disk (distinguishes "no bootstrap" from
      "bootstrap present but expired" — the latter is when context_runtime
      auto-deletes).
    - event_count / threshold: for the caller's auto-delete decision + logging.
      event_count is None when it was NOT queried (file absent, or a
      threshold-None semantic-only profile) — do not read it as "0 events".
    - bootstrap_path: the resolved Bootstrap.md path (so the caller can remove it
      without re-resolving the workspace).
    """

    active: bool
    present: bool
    event_count: Optional[int]
    threshold: Optional[int]
    bootstrap_path: str


async def is_bootstrap_active(
    db: "AsyncDatabaseClient",
    agent_id: str,
    owner_id: str,
    agent_metadata: Optional[dict],
) -> BootstrapStatus:
    """Whether `agent_id` is still bootstrapping for its owner.

    `owner_id` is the agent's creator (both callers pass created_by): the
    workspace is resolved under the owner, matching where Bootstrap.md is
    written. Caller does the owner-vs-current-user gate. Uses the READ-side
    workspace resolver so migrated (legacy flat) agents still resolve. A count
    query failure is treated as active (fail-open, matching the historical
    runtime behavior) but is now logged.
    """
    from narranexus.platform.settings import settings

    bootstrap_path = os.path.join(
        str(resolve_existing_workspace(agent_id, owner_id, settings.base_working_path)),
        "Bootstrap.md",
    )
    if not os.path.isfile(bootstrap_path):
        return BootstrapStatus(
            active=False, present=False, event_count=None, threshold=None,
            bootstrap_path=bootstrap_path,
        )

    threshold = auto_delete_threshold_from_meta(agent_metadata)
    if threshold is None:
        # Semantic-only profile: never auto-deletes; active while the file exists.
        # No count query — event_count stays None (not queried, not "0 events").
        return BootstrapStatus(
            active=True, present=True, event_count=None, threshold=None,
            bootstrap_path=bootstrap_path,
        )

    try:
        rows = await db.execute(
            "SELECT COUNT(*) AS cnt FROM events WHERE agent_id = %s",
            (agent_id,),
            fetch=True,
        )
        event_count = rows[0]["cnt"] if rows else 0
    except Exception as e:  # noqa: BLE001 — fail-open like the runtime, but audible
        logger.warning(
            f"[bootstrap] event-count query failed for {agent_id}; treating as active: {e}"
        )
        event_count = 0

    return BootstrapStatus(
        active=event_count < threshold, present=True, event_count=event_count,
        threshold=threshold, bootstrap_path=bootstrap_path,
    )

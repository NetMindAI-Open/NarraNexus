"""
@file_name: workspace_cwd.py
@author:
@date: 2026-08-14
@description: Shared subprocess-CWD resolution for channel CLI passthroughs.

lark-cli / narra-cli write default-relative outputs (downloads, artifacts)
to their CWD, so the subprocess must be spawned inside the agent's own
workspace or those files land outside the agent's Read sandbox (the
2026-05-28 "transcript downloaded to a path I can't read" P0). The owner
lookup goes through the channel seam so it behaves identically in
direct-db and zero-cred deployments. One shared implementation + one
owner cache — this used to be a per-channel copy in each CLI client, and
the copies drifted.

It lives in data_access (not utils/) because it NEEDS the seam: putting
it lower would give utils/ its first upward edge into module/, and would
also move it out of the pyright gate's include scope.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from loguru import logger

from narranexus.platform.module_system.data_access.factory import (
    get_channel_credential_store,
)

_cwd_owner_cache: dict[str, str] = {}


async def resolve_agent_workspace_cwd(
    agent_id: str, *, log_tag: str
) -> Optional[Path]:
    """Return the agent's workspace dir to use as a CLI subprocess CWD.

    ``log_tag`` keeps the per-channel log prefix (``lark-cli`` /
    ``narra-cli``) that ops runbooks grep for. Returns None if:
      - the agent has no owner (orphaned bind / corrupted row) — the empty
        owner is NOT cached, so a later re-bind re-resolves;
      - the owner lookup or workspace mkdir fails.
    Callers MUST tolerate None — they fall back to inheriting the parent
    CWD, which is wrong for downloads but safe for everything else.
    """
    user_id = _cwd_owner_cache.get(agent_id)
    if user_id is None:
        try:
            user_id = await get_channel_credential_store().get_agent_owner(agent_id)
            if not user_id:
                logger.debug(
                    f"[{log_tag}] no owner for {agent_id}; "
                    f"subprocess will inherit parent CWD"
                )
                return None
            _cwd_owner_cache[agent_id] = user_id
        except Exception as e:  # noqa: BLE001 — CWD is optional by contract
            logger.debug(f"[{log_tag}] could not resolve user_id for {agent_id}: {e}")
            return None
    try:
        from narranexus.platform.utils.attachment_storage import get_workspace_path

        ws = get_workspace_path(agent_id, user_id)
        ws.mkdir(parents=True, exist_ok=True)
        return ws
    except Exception as e:  # noqa: BLE001 — CWD is optional by contract
        logger.debug(f"[{log_tag}] workspace path resolution failed for {agent_id}: {e}")
        return None

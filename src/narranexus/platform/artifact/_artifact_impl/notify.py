"""
@file_name: notify.py
@date: 2026-08-18
@description: Staging chokepoint for artifact_changed events (the outbox).

Every registry write — register, target re-register (including the dedup
in-place branch), delete, heal repoint — calls stage_artifact_event right
after its DB write succeeds. Rows land in instance_artifact_events;
BackgroundRun drains them after each tool-output event and re-emits through
the recorder+broadcaster pipeline, so the frontend learns about registry
changes from the backend instead of grepping tool names out of the chat
stream (spec 2026-08-18-artifact-events-inventory-pointer §3).

Two contract lines shape everything here:
- Best-effort: the registry write OWNS success. A staging failure logs a
  warning and vanishes — the frontend full-pull on open/switch/reconnect is
  the self-healing floor, so a lost event is a delay, never corruption.
- The payload is the FULL artifact row, file_path included. The HTTP list
  routes already return file_path to the authenticated owner, so excluding
  it here would not hide anything — it would only split the frontend store
  into two Artifact shapes (HtmlRenderer branches on file_path), which is
  fragility without secrecy. One wire shape, same as the routes.
"""
from __future__ import annotations

import json
from typing import Optional

from loguru import logger

from narranexus.platform.repository.artifact_event_repository import (
    ArtifactEventRepository,
)
from narranexus.platform.schema.artifact_schema import Artifact

ARTIFACT_EVENT_TYPE = "artifact_changed"

# The full action vocabulary. "registered": a new row was minted.
# "updated": an existing row's pointer/metadata was overwritten in place
# (target re-register or the agent-scoped dedup branch). "deleted": the row
# is gone. "repointed": heal moved the pointer — extra carries old/new path
# tails and whether the content hash verified the candidate.
ACTIONS = ("registered", "updated", "deleted", "repointed")


async def stage_artifact_event(
    db,
    *,
    action: str,
    artifact: Artifact,
    external: bool = False,
    extra: Optional[dict] = None,
) -> None:
    """Stage one artifact_changed event row; never raises."""
    assert action in ACTIONS, f"unknown artifact event action: {action}"
    try:
        payload = {
            "type": ARTIFACT_EVENT_TYPE,
            "action": action,
            "external": external,
            "artifact": artifact.model_dump(mode="json"),
        }
        if extra:
            payload["extra"] = extra
        await ArtifactEventRepository(db).stage(
            agent_id=artifact.agent_id,
            payload_json=json.dumps(payload, ensure_ascii=False),
        )
    except Exception as e:  # noqa: BLE001 — best-effort by contract (see module docstring)
        logger.warning(f"artifact event staging failed ({action}): {e}")

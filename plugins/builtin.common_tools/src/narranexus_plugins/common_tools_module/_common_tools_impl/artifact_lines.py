"""
@file_name: artifact_lines.py
@date: 2026-08-18
@description: Shared per-artifact line rendering for the agent-facing
inventory surfaces.

The state block (every turn) and the list_artifacts tool (on demand) must
show identical lines — same id/kind/title, same path rules — or the agent
has to learn two dialects for one inventory. This module is the single
owner of that formatting; both callers import it.

Path rules (moved verbatim from the state block, 2026-08-18):
- An entry inside the agent's OWN workspace renders as a short relative
  path — the form its file tools take.
- Anything else (a teammate's team artifact, an entry in the team shared
  folder) renders ABSOLUTE: a bare base-relative path would resolve
  against the READER's workspace (the confinement layer rebases relative
  paths there deliberately) and point at a file that does not exist.
- URL tabs point the agent at the readable text snapshot (content.md)
  when it exists on disk, so the agent can SEE the page content.
"""
from __future__ import annotations

import os
import posixpath
from typing import List, Tuple

from narranexus.platform.schema.artifact_schema import (
    URL_ARTIFACT_KIND,
    URL_TAB_CONTENT_FILENAME,
)
from narranexus.platform.settings import settings
from narranexus.platform.utils.workspace_paths import agent_workspace_relpath


def format_artifact_lines(
    artifacts, *, agent_id: str, user_id: str
) -> List[Tuple[str, str]]:
    """Render one (artifact_id, line) pair per artifact.

    EXPLICIT pairing (review #334 I11): the state block attaches per-artifact
    markers to these lines, and a positional zip would silently misalign the
    moment any kind ever renders two lines — the id travels with its line."""
    # No user → no own-workspace prefix can match (the relpath helpers refuse
    # an empty id since 2026-10-28), so every artifact renders by absolute path.
    workspace_prefixes = (
        f"{agent_workspace_relpath(agent_id, user_id)}/",
        f"{agent_id}_{user_id}/",  # legacy flat layout
    ) if user_id else ()
    base = os.path.realpath(settings.base_working_path)

    lines: List[Tuple[str, str]] = []
    for a in artifacts:
        rel = a.file_path
        outside_own_workspace = True
        for prefix in workspace_prefixes:
            if rel.startswith(prefix):
                rel = rel[len(prefix):]
                outside_own_workspace = False
                break
        if outside_own_workspace:
            rel = os.path.join(base, a.file_path)
        content_rel = None
        if a.kind == URL_ARTIFACT_KIND:
            candidate = posixpath.join(posixpath.dirname(rel), URL_TAB_CONTENT_FILENAME)
            if os.path.isfile(
                os.path.join(base, a.file_path.rsplit("/", 1)[0], URL_TAB_CONTENT_FILENAME)
            ):
                content_rel = candidate
        if content_rel is not None:
            lines.append((
                a.artifact_id,
                f"- `{a.artifact_id}` [{a.kind}] {a.title!r} → web page; "
                f"Read `{content_rel}` to see its text content",
            ))
        else:
            lines.append(
                (a.artifact_id, f"- `{a.artifact_id}` [{a.kind}] {a.title!r} → `{rel}`")
            )
    return lines

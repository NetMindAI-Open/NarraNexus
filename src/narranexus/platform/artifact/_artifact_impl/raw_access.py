"""
@file_name: raw_access.py
@author: Bin Liang
@date: 2026-07-21
@description: Resolve an artifact + sub-path to the on-disk file it serves.

The public raw route (`backend/routes/artifacts/public.py`) serves an
artifact's root directory: the entry file at `/raw/{token}/`, sibling assets
at `/raw/{token}/{name}`. This module owns everything that is NOT HTTP:
pointer lookup, workspace path resolution (flat→nested fallback), path-escape
confinement, the workspace-root single-file constraint, and media-type
selection. The route keeps token verification and response headers (CSP).

Extracted from `backend/routes/artifacts/public.py` (2026-07-21).

Failure → structured ArtifactError:
- ArtifactNotFound (404): artifact missing, token/agent mismatch, requested
  path outside the artifact root. 404 (not 403) so probes cannot map which
  paths exist.
- ArtifactContentGone (410): row exists but the pointer is broken (file_path
  empty or entry/asset off-disk). 410 is the frontend's self-heal trigger.
"""

from __future__ import annotations

import mimetypes
import os
from dataclasses import dataclass

from loguru import logger

from narranexus.platform.artifact._artifact_impl.errors import (
    ArtifactContentGone,
    ArtifactNotFound,
    ArtifactPathEscape,
)
from narranexus.platform.repository.artifact_repository import ArtifactRepository
from narranexus.platform.schema.artifact_schema import ArtifactKind
from narranexus.platform.settings import settings


# Kinds that are internal render markers, not transport MIMEs — serving them
# as Content-Type makes a directly-opened URL an unknown binary (the
# Shenzhen-r2 ".bin download" bug). The entry's real type comes from its
# extension instead. Keep this in step with the frontend's set of
# non-static-downloadExt kinds (kindRegistry.ts) — review #349 M3 caught
# the two drifting after one round.
_RENDER_MARKER_KINDS = frozenset({
    "application/vnd.officecli-live",
    "application/x-url",  # entry is tabs/<slug>/page.url.json → application/json
})

# Explicit map for the office extensions, NOT mimetypes.guess_type: the
# stdlib's knowledge of pptx/docx/xlsx comes from the platform's mime.types
# file, so guess_type is right on a dev laptop and octet-stream in a slim
# container — an environment-axis false green.
_OFFICE_MIME_BY_EXT = {
    ".pptx": "application/vnd.openxmlformats-officedocument"
             ".presentationml.presentation",
    ".docx": "application/vnd.openxmlformats-officedocument"
             ".wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument"
             ".spreadsheetml.sheet",
}


@dataclass(frozen=True)
class ResolvedRawFile:
    """A raw-route resolution: which file to serve and how to label it."""

    path: str  # absolute, realpath-resolved file path
    media_type: str
    is_entry: bool  # True → this is the artifact's entry file (drives CSP choice)
    kind: ArtifactKind  # the artifact's registered kind


async def resolve_raw_file(
    *,
    repo: ArtifactRepository,
    agent_id: str,
    artifact_id: str,
    file_path: str = "",
) -> ResolvedRawFile:
    """Resolve which on-disk file a raw request serves.

    `file_path` is the sub-path under the artifact root directory; empty means
    the entry file itself.

    Rules (entry and sub-paths realpath-ed, owner roots lexical, so a symlink
    anywhere fails closed):
    - The entry must sit under one of its OWNER's roots (that agent's
      workspace candidates, or its team folder) — see `confine_entry`.
    - Single-file mode: when the entry sits directly at a CONTAINER root —
      the agent's workspace, or the team's shared folder for a team artifact —
      the dirname tree would be that entire root, so serving siblings would
      expose every other file in it. Sub-path requests are refused (the
      entry's own basename is tolerated as an alias).
    - Sub-paths are confined to the artifact root.

    Media type: the entry serves as the artifact's `kind`, EXCEPT for
    render-marker kinds (`_RENDER_MARKER_KINDS`) whose real transport type
    comes from the entry's extension; assets are guessed via `mimetypes`
    (the kind describes the entry, not a sibling style.css).

    Raises:
        ArtifactNotFound / ArtifactContentGone — see module docstring.
    """
    art = await repo.get_by_id(artifact_id)
    if art is None or art.agent_id != agent_id:
        raise ArtifactNotFound("artifact not found")
    if not art.file_path:
        # Legacy (pre-pointer-model) row that was never re-registered.
        raise ArtifactContentGone("artifact has no content pointer on disk")

    from .confinement import confine_entry, owner_roots

    base = os.path.realpath(settings.base_working_path)
    # Resolve with a flat→nested fallback so artifacts whose file_path was
    # stored under the old flat layout still serve after the nested flip —
    # and pin the result to the OWNER's roots, not merely to base (which is
    # every tenant's root; 2026-10-28).
    try:
        entry_abs = confine_entry(art.file_path, art.agent_id, art.user_id, base, team_id=art.team_id)
    except ArtifactPathEscape:
        logger.warning(f"path-escape blocked: artifact={artifact_id} entry={art.file_path!r}")
        raise ArtifactNotFound("artifact not found")
    artifact_root = os.path.dirname(entry_abs)

    # Single-file mode: when the entry sits directly at a CONTAINER root
    # (any of the agent's workspace dirs, or the team folder), the dirname
    # tree would be that whole root — serving siblings would expose every
    # other file the agent owns. Refuse sub-path requests in that case; only
    # the entry serves. This is the soft replacement for the old "entry must
    # be in a subdirectory" hard rule.
    #
    # The container roots are EXACTLY the owner roots confine_entry just
    # admitted the entry under: all workspace layout candidates (nested,
    # legacy flat, legacy `_user_` infix) plus the team folder. Using only the
    # first EXISTING workspace dir missed an entry sitting at the legacy flat
    # root whenever the nested dir also existed, and a view token — over a
    # route that deliberately bypasses JWT, so the token IS the capability —
    # then served the agent's top-level credentials. Lexical on both sides:
    # confine_entry already proved the realpath-ed entry lies under one of
    # these literal roots, so artifact_root is never realpath-ed again here.
    container_roots = set(
        owner_roots(art.agent_id, art.user_id, base, team_id=art.team_id)
    )
    if artifact_root in container_roots and file_path:
        if os.path.normpath(file_path) == os.path.basename(entry_abs):
            file_path = ""
        else:
            raise ArtifactNotFound(
                "sibling assets are not served for an entry that sits "
                "directly at the workspace or team-folder root — put the "
                "entry in its own subdirectory to serve assets alongside it"
            )

    if file_path:
        requested = os.path.realpath(os.path.join(artifact_root, file_path))
        if not requested.startswith(artifact_root + os.sep):
            logger.warning(f"path-escape blocked: artifact={artifact_id} file_path={file_path!r}")
            raise ArtifactNotFound("not found")
        target = requested
    else:
        target = entry_abs

    if not os.path.isfile(target):
        logger.warning(f"artifact file missing on disk: {target}")
        raise ArtifactContentGone("artifact file missing on disk")

    is_entry = target == entry_abs
    if is_entry and art.kind in _RENDER_MARKER_KINDS:
        ext = os.path.splitext(target)[1].lower()
        media_type = (
            _OFFICE_MIME_BY_EXT.get(ext)
            or mimetypes.guess_type(target)[0]
            or "application/octet-stream"
        )
    elif is_entry:
        media_type = art.kind
    else:
        media_type = mimetypes.guess_type(target)[0] or "application/octet-stream"

    return ResolvedRawFile(path=target, media_type=media_type, is_entry=is_entry, kind=art.kind)

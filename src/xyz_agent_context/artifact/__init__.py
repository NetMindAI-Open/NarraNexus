"""
@file_name: __init__.py
@author: Bin Liang
@date: 2026-07-21
@description: Public API of the artifact subsystem (pointer model).

An artifact is a POINTER to an entry file the agent wrote inside its own
workspace: the DB row stores the workspace-relative path plus metadata; the
file's directory is the "artifact root" served wholesale so multi-file HTML
apps can reference sibling assets. Content is never copied.

This package is the single public seam for artifact business logic. Every
consumer — MCP tool, HTTP routes, bootstrap — goes through `ArtifactService`;
the concrete logic lives in `_artifact_impl/` (private, never imported from
outside this package).
"""

from xyz_agent_context.artifact._artifact_impl.errors import (
    ArtifactContentGone,
    ArtifactError,
    ArtifactKindMismatch,
    ArtifactNotFound,
    ArtifactPathEscape,
    ArtifactTooLarge,
)
from xyz_agent_context.artifact._artifact_impl.raw_access import ResolvedRawFile
from xyz_agent_context.artifact._artifact_impl.registration import (
    ALL_KINDS,
    MAX_ARTIFACT_BYTES,
)
from xyz_agent_context.artifact.artifact_service import ArtifactService

__all__ = [
    "ArtifactService",
    "ResolvedRawFile",
    "ArtifactError",
    "ArtifactTooLarge",
    "ArtifactNotFound",
    "ArtifactKindMismatch",
    "ArtifactPathEscape",
    "ArtifactContentGone",
    "ALL_KINDS",
    "MAX_ARTIFACT_BYTES",
]

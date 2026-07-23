"""
@file_name: errors.py
@author: Bin Liang
@date: 2026-07-21
@description: Structured exception hierarchy for the artifact subsystem.

Every error carries a `.code` attribute that maps 1:1 onto an HTTP status, so
the MCP wrapper and the HTTP routes convert failures uniformly without
inspecting exception types individually.
"""

from __future__ import annotations


class ArtifactError(Exception):
    """Base class for artifact errors. The .code attribute maps to HTTP status."""

    code: int = 400


class ArtifactTooLarge(ArtifactError):
    code = 413


class ArtifactNotFound(ArtifactError):
    code = 404


class ArtifactKindMismatch(ArtifactError):
    code = 400


class ArtifactPathEscape(ArtifactError):
    code = 400


class ArtifactContentGone(ArtifactError):
    """Row exists but the pointed-at content is gone (file_path empty or the
    entry file is off-disk). Maps to 410 — the frontend treats 410 as the
    self-heal trigger, distinct from 404 (no such artifact)."""

    code = 410

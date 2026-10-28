"""
@file_name: surface.py
@date: 2026-06-08
@description: Resolve which surface this backend process serves.

Local and desktop run identical backend code; launchers distinguish them with
NARRA_SURFACE. When that label is absent, the canonical deployment-mode
resolver still distinguishes cloud from local so hosted facts cannot be
silently mislabeled. Resolved once at import.
"""
from __future__ import annotations

import os

from loguru import logger

from narranexus.platform.utils.deployment_mode import get_deployment_mode

_VALID = {"local", "desktop", "cloud"}


def resolve_surface() -> str:
    raw = (os.environ.get("NARRA_SURFACE") or "").strip().lower()
    if raw in _VALID:
        return raw
    inferred = get_deployment_mode()
    reason = "missing" if not raw else f"invalid value {raw!r}"
    logger.warning(
        f"[analytics] NARRA_SURFACE {reason}; inferred {inferred!r} "
        "from deployment mode"
    )
    return inferred


SURFACE: str = resolve_surface()

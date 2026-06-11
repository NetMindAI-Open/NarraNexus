"""
@file_name: surface.py
@date: 2026-06-08
@description: Resolve which surface this backend process serves.

local/desktop run identical backend code; the only difference is the
launcher-injected NARRA_SURFACE env (run.sh=local, Tauri sidecar=desktop).
cloud is detected separately and this phase routes to NullSink (see
analytics/__init__.py). Resolved once at import.
"""
from __future__ import annotations

import os

_VALID = {"local", "desktop", "cloud"}


def resolve_surface() -> str:
    raw = (os.environ.get("NARRA_SURFACE") or "").strip().lower()
    return raw if raw in _VALID else "local"


SURFACE: str = resolve_surface()

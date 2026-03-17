"""
@file_name: config.py
@author: Bin Liang
@date: 2026-03-06
@description: Centralized backend configuration

All tuneable constants live here so they can be overridden via environment
variables without touching code.  Import from this module instead of
scattering os.getenv() and magic numbers across route files.

Usage:
    from backend.config import settings
    settings.cors_origins   # list[str]
    settings.ws_heartbeat_interval  # int (seconds)
"""

import os
from pathlib import Path
from typing import List


def _parse_list(raw: str) -> List[str]:
    """Split a comma-separated string into a trimmed, non-empty list."""
    return [s.strip() for s in raw.split(",") if s.strip()]


class Settings:
    """
    Read-once, module-level settings object.

    Every attribute falls back to a sensible default when the corresponding
    environment variable is absent.
    """

    # ── CORS ─────────────────────────────────────────────────────────────────
    _DEFAULT_CORS_ORIGINS = (
        "http://localhost:5173,"
        "http://localhost:3000,"
        "http://localhost:8000,"
        "http://127.0.0.1:5173,"
        "http://127.0.0.1:3000,"
        "http://127.0.0.1:8000"
    )
    cors_origins: List[str] = _parse_list(
        os.getenv("CORS_ORIGINS", _DEFAULT_CORS_ORIGINS)
    )

    # ── WebSocket ────────────────────────────────────────────────────────────
    # Heartbeat interval to prevent proxy/SSH idle-timeout disconnections
    ws_heartbeat_interval: int = int(os.getenv("WS_HEARTBEAT_INTERVAL", "15"))

    # ── Frontend static files ────────────────────────────────────────────────
    # Path to the built frontend dist directory
    frontend_dist: Path = Path(
        os.getenv(
            "FRONTEND_DIST",
            str(Path(__file__).resolve().parent.parent / "frontend" / "dist"),
        )
    )

    # ── Upload limits ────────────────────────────────────────────────────────
    # Maximum upload size in bytes (default 50 MB)
    max_upload_bytes: int = int(os.getenv("MAX_UPLOAD_BYTES", str(50 * 1024 * 1024)))


settings = Settings()

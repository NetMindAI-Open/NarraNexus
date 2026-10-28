"""
@file_name: deployment.py
@author: Bin Liang
@date: 2026-09-03
@description: The single answer to "is this process a cloud deployment or a local one?".

Three copies of this predicate existed (``backend/auth.py``,
``utils/deployment_mode.py``, ``settings.py``) with slightly different
heuristics; the plugin loader's fail-closed rule (D1: no user plugins on the
cloud) needs exactly one. This is it; the other three forward here.

Precedence:
1. ``NARRANEXUS_DEPLOYMENT_MODE`` (``cloud`` | ``local``, case-insensitive,
   trimmed) — the explicit contract cloud deployments set in ``.env``.
2. ``DATABASE_URL`` pointing at a non-sqlite backend → cloud.
3. ``DB_HOST`` set (the pre-``DATABASE_URL`` cloud configuration) → cloud.
4. Otherwise local — the safe default: a packaged desktop app must never
   demand a cloud login because an env var failed to propagate.
"""
from __future__ import annotations

import os
from typing import Literal, Mapping

DEPLOYMENT_MODE_ENV_VAR = "NARRANEXUS_DEPLOYMENT_MODE"
DeploymentMode = Literal["cloud", "local"]
_VALID_MODES: tuple[DeploymentMode, ...] = ("cloud", "local")


def get_deployment_mode(environ: Mapping[str, str] | None = None) -> DeploymentMode:
    env = os.environ if environ is None else environ
    explicit = env.get(DEPLOYMENT_MODE_ENV_VAR, "").strip().lower()
    if explicit in _VALID_MODES:
        return explicit  # type: ignore[return-value]
    db_url = env.get("DATABASE_URL", "").strip().lower()
    if db_url:
        return "local" if db_url.startswith("sqlite") else "cloud"
    if env.get("DB_HOST", "").strip():
        return "cloud"
    return "local"


def is_cloud_mode(environ: Mapping[str, str] | None = None) -> bool:
    return get_deployment_mode(environ) == "cloud"


def is_local_mode(environ: Mapping[str, str] | None = None) -> bool:
    return get_deployment_mode(environ) == "local"


__all__ = ["DEPLOYMENT_MODE_ENV_VAR", "DeploymentMode", "get_deployment_mode", "is_cloud_mode", "is_local_mode"]

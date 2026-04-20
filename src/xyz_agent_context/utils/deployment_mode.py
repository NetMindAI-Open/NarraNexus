"""
@file_name: deployment_mode.py
@author: Bin Liang
@date: 2026-04-20
@description: Single source of truth for "are we running in cloud or local mode?".

Why this exists
---------------
Several parts of the system need to behave differently depending on whether
the current NarraNexus process is:

  - a shared **cloud** deployment (multi-tenant server, AWS-hosted, strict
    filesystem/network isolation between users), or
  - a **local** deployment (desktop app / dev install on the user's own
    machine, relaxed access, the user trusts the agent with their files).

Before this module, two copies of a `_is_cloud_mode()` helper in
`agent_framework/{system,user}_provider_service.py` inferred the mode from
the database URL. That works for the provider code but (a) duplicates
logic, (b) is an indirect proxy, and (c) can't be overridden cleanly when
a deployment wants a different policy than the URL suggests.

Deployment-side contract
------------------------
Cloud deployments set ``NARRANEXUS_DEPLOYMENT_MODE=cloud`` in their
``.env`` file (see ``stacks/narranexus-app/.env.example``). Local
installs do nothing — the default is ``local``, which is also the
safer default for ad-hoc / dev environments.

Priority:

1. ``NARRANEXUS_DEPLOYMENT_MODE`` env var (case-insensitive, trimmed).
   Values: ``cloud`` or ``local``. Anything else → fall back to local.
2. Legacy heuristic: if ``DATABASE_URL`` points at a non-sqlite DB,
   treat as cloud. This keeps existing cloud deployments working
   without requiring them to redeploy just to set a new env var.
3. Otherwise → ``local``.
"""
from __future__ import annotations

import os
from typing import Literal


DEPLOYMENT_MODE_ENV_VAR = "NARRANEXUS_DEPLOYMENT_MODE"

DeploymentMode = Literal["cloud", "local"]

_VALID_MODES: tuple[DeploymentMode, ...] = ("cloud", "local")


def get_deployment_mode() -> DeploymentMode:
    """Return ``"cloud"`` or ``"local"`` based on env.

    See module docstring for the precedence rules.
    """
    explicit = os.environ.get(DEPLOYMENT_MODE_ENV_VAR, "").strip().lower()
    if explicit in _VALID_MODES:
        return explicit  # type: ignore[return-value]

    # Legacy heuristic — preserves behaviour for cloud deployments that
    # haven't updated their .env yet.
    db_url = os.environ.get("DATABASE_URL", "").strip().lower()
    if db_url and not db_url.startswith("sqlite"):
        return "cloud"

    return "local"


def is_cloud_mode() -> bool:
    return get_deployment_mode() == "cloud"


def is_local_mode() -> bool:
    return get_deployment_mode() == "local"

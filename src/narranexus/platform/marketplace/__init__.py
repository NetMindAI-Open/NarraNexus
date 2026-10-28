"""
@file_name: __init__.py
@author: NetMind.AI
@date: 2026-07-24
@description: Marketplace domain subpackage — skill + team marketplace.

Groups the marketplace feature area in one place, matching the repo's
domain-subpackage convention (artifact/, memory/, message_bus/, ...):

- `skill_marketplace_service.py` / `team_marketplace_service.py` — the
  public service seams consumers import directly.
- `_skill_marketplace_impl/` — private implementation (registry, install
  pipeline, artifact store, scanner, secret box). Consumers INSIDE the
  platform import it directly; consumers outside (the skills and teams plugin
  packages) go through the four names re-exported below.
- `resources/marketplace_skills/` — first-party skills vendored with the
  package, seeded into the catalog by `marketplace/_skill_marketplace_seed`.

The re-exports are LAZY (PEP 562). Three reasons they exist at all and one
why they are lazy:

- `builtin.skills` and `builtin.teams` ship as separate wheels since batch 6b
  and were importing `marketplace._skill_marketplace_impl.*` — a private
  module of another package, which `docs/API_POLICY.md` §1 forbids and the
  new import-linter contract "plugins never import a private platform module"
  now blocks.
- These four ARE the seam those two plugins need (an artifact/template store,
  the install pipeline, the secret box); the service modules do not expose
  them.
- Lazily, because `install_pipeline` pulls the whole marketplace registry and
  `secret_box` touches the key file: importing `narranexus.platform.marketplace`
  to reach `skill_marketplace_service` must not pay for either.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ._skill_marketplace_impl.artifact_store import ArtifactStore, get_template_store
    from ._skill_marketplace_impl.install_pipeline import InstallPipeline
    from ._skill_marketplace_impl.secret_box import get_secret_box

_LAZY: dict[str, str] = {
    "ArtifactStore": "._skill_marketplace_impl.artifact_store",
    "get_template_store": "._skill_marketplace_impl.artifact_store",
    "InstallPipeline": "._skill_marketplace_impl.install_pipeline",
    "get_secret_box": "._skill_marketplace_impl.secret_box",
}


def __getattr__(name: str):
    """Resolve the public marketplace seam lazily (PEP 562)."""
    where = _LAZY.get(name)
    if where is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib

    value = getattr(importlib.import_module(where, __name__), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(_LAZY) | set(globals()))


__all__ = ["ArtifactStore", "InstallPipeline", "get_secret_box", "get_template_store"]

"""
@file_name: _skill_marketplace_seed.py
@author: NetMind.AI
@date: 2026-07-22
@description: Bootstrap seed for the Skill Marketplace — publishes the
first-party skills vendored in the repo (marketplace_skills/) into this
registry host's catalog + artifact store.

Why this exists (parallel to _team_marketplace_seed): the Team Marketplace
auto-seeds from narra.nexus, but skills had NO auto-seed, so a fresh cloud
deploy showed an empty Skills tab AND default-skill install (NETMIND vision /
audio) found nothing to install. This seed makes the repo-vendored,
first-party skills — the `default: true` NetMind multimodal fallbacks in
particular — available out of the box, exactly like officecli's vendored
SKILL.md is materialized without any manual step.

Scope: ONLY the skills physically present under `marketplace_skills/` in the
repo (first-party). Third-party skills (clawhub, etc.) are NOT seeded here —
they carry license/attribution concerns and are published deliberately via
scripts/publish_skill.py.

Idempotent: a skill whose (id, version) is already in the catalog AND whose
blob is already in the store is skipped (no re-scan, no re-upload). Runs on
the registry host only (cloud / SKILL_MARKETPLACE_LOCAL_REGISTRY); a pure
desktop client proxies to the cloud and needs no local catalog.
"""

import json
import os
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Optional

from loguru import logger


def _skills_root() -> Optional[Path]:
    """Locate the repo-vendored marketplace_skills/ directory.

    Env override MARKETPLACE_SKILLS_DIR wins; otherwise resolve relative to
    this file (src/xyz_agent_context/repository/ -> repo root). Returns None
    if it can't be found (e.g. a pip-installed package without the dir), so
    the seed degrades to a no-op instead of raising."""
    override = os.environ.get("MARKETPLACE_SKILLS_DIR")
    if override:
        p = Path(override)
        return p if p.is_dir() else None
    candidate = Path(__file__).resolve().parents[3] / "marketplace_skills"
    return candidate if candidate.is_dir() else None


def _zip_skill(skill_dir: Path, dest: Path) -> None:
    """Zip a skill directory under a top-level folder named after the skill,
    excluding install bookkeeping."""
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(skill_dir.rglob("*")):
            if path.is_file() and path.name not in (".skill_meta.json", "_meta.json"):
                zf.write(path, f"{skill_dir.name}/{path.relative_to(skill_dir).as_posix()}")


async def seed_skill_marketplace(db_client) -> int:
    """Publish repo-vendored first-party skills into the catalog + store.

    Returns the number of skills present after seeding. Best-effort per skill
    (one failure never aborts the rest), idempotent, safe to run every boot."""
    from xyz_agent_context._skill_marketplace_impl.registry import (
        PublishRejectedError,
        RegistryService,
    )

    root = _skills_root()
    if root is None:
        logger.debug("Skill seed: marketplace_skills/ not found — nothing to seed")
        return 0

    registry = RegistryService(db_client)
    ok = 0
    for skill_dir in sorted(root.iterdir()):
        if not skill_dir.is_dir() or skill_dir.name.startswith("."):
            continue
        manifest_file = skill_dir / "manifest.json"
        skill_md = skill_dir / "SKILL.md"
        if not skill_md.exists():
            continue
        try:
            skill_id: Optional[str] = None
            version: Optional[str] = None
            if manifest_file.exists():
                manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
                skill_id = manifest.get("id")
                version = manifest.get("version")

            # Idempotency: skip when this exact version is already catalogued
            # and its blob is in the store.
            if skill_id and version:
                existing = await registry.catalog.get_version(skill_id, version)
                if existing and registry.store.exists(existing.s3_key):
                    ok += 1
                    continue

            tmp = Path(tempfile.mkdtemp(prefix="nx-skill-seed-"))
            try:
                zip_path = tmp / f"{skill_dir.name}.zip"
                _zip_skill(skill_dir, zip_path)
                entry = await registry.publish(zip_path, "narranexus-team")
                ok += 1
                logger.info(
                    f"Skill seed: published {entry.skill_id}@{entry.version}"
                    f"{' (default)' if entry.is_default else ''}"
                )
            finally:
                shutil.rmtree(tmp, ignore_errors=True)
        except PublishRejectedError as e:
            rules = sorted({i.rule for i in e.report.issues if i.severity == "high"})
            logger.warning(f"Skill seed: {skill_dir.name} REJECTED by scan ({rules}) — skipped")
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Skill seed: {skill_dir.name} skipped — {type(e).__name__}: {e}")
    logger.info(f"Skill seed: {ok} first-party skill(s) present")
    return ok

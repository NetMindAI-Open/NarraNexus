"""
@file_name: evermemos_sync.py
@author: Bin Liang
@date: 2026-03-26
@description: Sync LLM slot config → EverMemOS .env

When the embedding or helper_llm slot changes, this module derives the
corresponding EverMemOS environment variables and writes them to
.evermemos/.env. EverMemOS picks up changes on its next restart.

Mapping:
  helper_llm slot → LLM_PROVIDER, LLM_MODEL, LLM_BASE_URL, LLM_API_KEY
  embedding slot  → VECTORIZE_PROVIDER, VECTORIZE_MODEL, VECTORIZE_BASE_URL,
                    VECTORIZE_API_KEY, VECTORIZE_DIMENSIONS
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from loguru import logger

from xyz_agent_context.schema.provider_schema import LLMConfig


# =============================================================================
# Constants
# =============================================================================

def _find_evermemos_dir() -> Optional[Path]:
    """
    Locate the .evermemos directory.

    Search order:
      1. PROJECT_DIR env var (set by desktop app)
      2. Current working directory
      3. Repository root (3 levels up from this file)
    """
    # Desktop app sets PROJECT_DIR
    project_dir = os.getenv("PROJECT_DIR")
    if project_dir:
        candidate = Path(project_dir) / ".evermemos"
        if candidate.is_dir():
            return candidate

    # CWD (typical for `uv run uvicorn ...` from project root)
    candidate = Path.cwd() / ".evermemos"
    if candidate.is_dir():
        return candidate

    # Fallback: relative to this source file
    candidate = Path(__file__).resolve().parents[3] / ".evermemos"
    if candidate.is_dir():
        return candidate

    return None


# =============================================================================
# Env file read/write helpers
# =============================================================================

def _read_env_file(path: Path) -> dict[str, str]:
    """Parse a .env file into a dict (ignores comments and blank lines)."""
    result: dict[str, str] = {}
    if not path.is_file():
        return result
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key, _, value = line.partition("=")
            result[key.strip()] = value.strip()
    return result


def _write_env_file(path: Path, data: dict[str, str]) -> None:
    """Write a dict as KEY=VALUE lines to a .env file."""
    lines = [f"{k}={v}" for k, v in data.items()]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# =============================================================================
# Sync logic
# =============================================================================

def sync_evermemos_from_config(config: LLMConfig) -> bool:
    """
    Derive EverMemOS env values from the current LLM slot configuration
    and merge them into .evermemos/.env.

    Only updates LLM + VECTORIZE fields. Infrastructure, rerank, and other
    settings are left untouched.

    Args:
        config: The current LLMConfig (providers + slots)

    Returns:
        True if sync succeeded, False if .evermemos not found or no slots configured
    """
    evermemos_dir = _find_evermemos_dir()
    if evermemos_dir is None:
        logger.debug("EverMemOS directory not found — skipping sync")
        return False

    env_path = evermemos_dir / ".env"

    # Read existing env to preserve non-LLM settings
    env_data = _read_env_file(env_path)

    # If .env doesn't exist yet, seed from template
    if not env_data:
        template_path = evermemos_dir / "env.template"
        if template_path.is_file():
            env_data = _read_env_file(template_path)

    updated = False

    # ── Helper LLM → EverMemOS LLM ──
    helper_slot = config.slots.get("helper_llm")
    if helper_slot and helper_slot.provider_id:
        provider = config.providers.get(helper_slot.provider_id)
        if provider:
            env_data["LLM_PROVIDER"] = "openai"
            env_data["LLM_MODEL"] = helper_slot.model
            env_data["LLM_BASE_URL"] = provider.base_url or ""
            env_data["LLM_API_KEY"] = provider.api_key or ""
            updated = True
            logger.info(
                f"EverMemOS LLM synced: model={helper_slot.model}, "
                f"base_url={provider.base_url}"
            )

    # ── Embedding → EverMemOS VECTORIZE ──
    emb_slot = config.slots.get("embedding")
    if emb_slot and emb_slot.provider_id:
        provider = config.providers.get(emb_slot.provider_id)
        if provider:
            # Use "deepinfra" as provider type — it's a generic OpenAI-compatible
            # client inside EverMemOS (despite the name)
            env_data["VECTORIZE_PROVIDER"] = "deepinfra"
            env_data["VECTORIZE_MODEL"] = emb_slot.model
            env_data["VECTORIZE_BASE_URL"] = provider.base_url or ""
            env_data["VECTORIZE_API_KEY"] = provider.api_key or ""

            # Always truncate to 1024 dimensions for Milvus schema stability.
            # This ensures switching embedding models (1536d, 3072d, 4096d, etc.)
            # won't cause a Milvus SchemaNotReadyException — the collection
            # always stores 1024d vectors regardless of the upstream model.
            vectorize_dimensions = "1024"
            env_data["VECTORIZE_DIMENSIONS"] = vectorize_dimensions

            # Disable fallback — we rely on the primary provider
            env_data["VECTORIZE_FALLBACK_PROVIDER"] = "none"

            updated = True
            logger.info(
                f"EverMemOS VECTORIZE synced: model={emb_slot.model}, "
                f"base_url={provider.base_url}, dims={vectorize_dimensions}"
            )

    # ── Disable rerank by default (can be configured manually) ──
    if "RERANK_PROVIDER" not in env_data:
        env_data["RERANK_PROVIDER"] = "none"

    if updated:
        _write_env_file(env_path, env_data)
        logger.info(f"EverMemOS .env written to {env_path}")

    return updated

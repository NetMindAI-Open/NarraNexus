"""
@file_name: derive.py
@author: Bin Liang
@date: 2026-05-13
@description: Pure helpers — derive_driver_type, derive_billing_policy,
              derive_auth_ref, is_slot_broken, pick_default_model.

These are deliberately stateless functions kept out of the Driver
classes so they can be reused by:

* :mod:`backfill` — one-shot migration of legacy ``user_providers`` rows
* :mod:`self_heal` — runtime decision "is this slot broken?" + "what's
  the safe default?"
* tests — drive table-driven cases without instantiating a Driver

No DB access here. Caller passes in the relevant fields and gets a
deterministic answer.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional


# =============================================================================
# Driver type derivation
# =============================================================================

def derive_driver_type(
    source: Optional[str],
    auth_type: Optional[str],
    protocol: Optional[str],
) -> Optional[str]:
    """Map a legacy (source, auth_type, protocol) triple to a driver_type
    string usable as the ``DRIVER_REGISTRY`` key.

    Returns ``None`` for unrecognised combinations — the backfill loop
    logs a warning and skips the row rather than guessing.

    The truth table:

    ===========  =========  =========  ======================
    source       protocol   auth_type  → driver_type
    ===========  =========  =========  ======================
    user         anthropic  *          custom_anthropic
    user         openai     *          custom_openai
    netmind      *          *          netmind
    yunwu        *          *          yunwu
    openrouter   *          *          openrouter
    claude_oauth *          oauth      claude_oauth
    system       *          *          system_pool   (cloud-only)
    ===========  =========  =========  ======================
    """
    src = (source or "").lower()
    proto = (protocol or "").lower()

    if src == "user":
        if proto == "anthropic":
            return "custom_anthropic"
        if proto == "openai":
            return "custom_openai"
        return None  # unknown protocol on user-source — backfill skips

    if src in ("netmind", "yunwu", "openrouter"):
        return src

    if src == "claude_oauth":
        return "claude_oauth"

    if src == "system":
        return "system_pool"

    return None


# =============================================================================
# Billing policy derivation
# =============================================================================

def derive_billing_policy(
    source: Optional[str],
    auth_type: Optional[str],
) -> str:
    """Decide which entry of ``cost_tracker`` plus quota logic to use.

    * ``system_quota`` — cloud-only system pool rows; ``cost_tracker``
      additionally deducts from ``user_quotas``.
    * ``external_oauth`` — Claude Code OAuth rows; tokens are accounted
      by Anthropic itself so we only log to ``cost_records``.
    * ``user_pays`` (default) — user-owned credential, log to
      ``cost_records`` only, no quota deduction.

    The decision is intentionally simple — protocol and base_url don't
    affect billing, only the credential source does.
    """
    src = (source or "").lower()
    auth = (auth_type or "").lower()

    if src == "system":
        return "system_quota"
    if auth == "oauth":
        return "external_oauth"
    return "user_pays"


# =============================================================================
# Auth reference derivation
# =============================================================================

# Sentinel string written into ``user_providers.auth_ref`` for OAuth
# rows. Drivers expand this token to the actual path at use-time so
# the value is portable across OSes (Linux / macOS / Windows have
# different home dirs).
CLAUDE_CLI_CREDENTIALS_REF = "claude-cli:~/.claude/.credentials.json"


def derive_auth_ref(auth_type: Optional[str]) -> Optional[str]:
    """Returns the canonical ``auth_ref`` value for a legacy row.

    Only OAuth rows get a non-null value; everything else uses
    ``api_key`` directly and leaves the reference empty.
    """
    auth = (auth_type or "").lower()
    if auth == "oauth":
        return CLAUDE_CLI_CREDENTIALS_REF
    return None


def resolve_claude_credentials_path(auth_ref: Optional[str]) -> Optional[Path]:
    """Expand a ``claude-cli:`` sentinel into a real filesystem path.

    Order:

    1. ``CLAUDE_CLI_CREDENTIALS_PATH`` env var (explicit override)
    2. ``CLAUDE_CLI_HOME`` env var + ``.credentials.json``
    3. ``~/.claude/.credentials.json`` (default)

    Returns ``None`` if the auth_ref is missing or doesn't look like an
    OAuth reference.
    """
    if not auth_ref or not auth_ref.startswith("claude-cli:"):
        return None

    override = os.environ.get("CLAUDE_CLI_CREDENTIALS_PATH")
    if override:
        return Path(override).expanduser()

    cli_home = os.environ.get("CLAUDE_CLI_HOME")
    if cli_home:
        return Path(cli_home).expanduser() / ".credentials.json"

    # Strip "claude-cli:" prefix and expand ~
    return Path(auth_ref.split(":", 1)[1]).expanduser()


# =============================================================================
# Slot-broken detection + default-model picker
# =============================================================================

def _normalise_models(value) -> list[str]:
    """Accept either a JSON-text column or an already-decoded list and
    return a list[str]. Tolerates None / bad JSON.
    """
    if value is None:
        return []
    if isinstance(value, list):
        return [str(m) for m in value]
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
            if isinstance(decoded, list):
                return [str(m) for m in decoded]
        except (ValueError, TypeError):
            return []
    return []


# Sentinel values that ``slot.model`` is allowed to carry without being
# considered broken. ``"default"`` in particular is the UI / config
# convention meaning "use whatever the caller passed in OR the catalog
# default" — see ``OpenAIAgentsSDK._resolve_model``. Auto-repairing
# such a row would silently pin the slot to a concrete model and
# defeat the per-call-site override behaviour.
SLOT_MODEL_SENTINELS: frozenset[str] = frozenset({"default"})


def is_slot_broken(slot_model: str, card_models) -> bool:
    """Return True if ``slot_model`` is NOT present in the card's
    ``models`` array and is not a sentinel value.

    The check is intentionally against the card's array, NOT against
    the global catalog — a user who configured a private model that
    happens to not be in our catalog is fine; only a slot that points
    at a model the user's own card claims not to have is broken.

    Special cases:

    * ``"default"`` (or other sentinels in :data:`SLOT_MODEL_SENTINELS`)
      → not broken. The resolver downstream handles sentinel resolution.
    * Empty / None ``slot_model`` → broken (mis-configured row).
    """
    if slot_model in SLOT_MODEL_SENTINELS:
        return False
    if not slot_model:
        return True
    models = _normalise_models(card_models)
    return slot_model not in models


def pick_default_model(card_models, source: Optional[str], protocol: Optional[str]) -> Optional[str]:
    """Choose a safe default model to swap a broken slot to.

    Strategy:

    1. First element of the card's own ``models`` array (user-curated).
    2. First element of ``model_catalog.get_default_models(source, protocol)``
       — falls back to our canonical recommendation per aggregator+protocol.
    3. ``None`` if neither has a usable value — the caller should leave
       the slot alone and log an error.
    """
    on_card = _normalise_models(card_models)
    if on_card:
        return on_card[0]

    # Catalog fallback. Lazy import to avoid circular deps at module load.
    from xyz_agent_context.agent_framework.model_catalog import get_default_models

    try:
        catalog = get_default_models(source or "", protocol or "")
    except Exception:  # noqa: BLE001 — get_default_models shouldn't raise but be safe
        catalog = []
    if catalog:
        return catalog[0]
    return None


__all__ = [
    "derive_driver_type",
    "derive_billing_policy",
    "derive_auth_ref",
    "resolve_claude_credentials_path",
    "is_slot_broken",
    "pick_default_model",
    "CLAUDE_CLI_CREDENTIALS_REF",
]

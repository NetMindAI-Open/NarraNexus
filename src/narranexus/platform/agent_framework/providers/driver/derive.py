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

from narranexus.platform.schema.provider_schema import SUBSCRIPTION_AUTH_TYPES


# =============================================================================
# Driver type derivation
# =============================================================================

def derive_driver_type(
    source: Optional[str],
    auth_type: Optional[str],
    protocol: Optional[str],
) -> Optional[str]:
    """Map a legacy (source, auth_type, protocol) triple to a driver_type
    string usable as the ``driver_registry()`` key.

    Returns ``None`` for unrecognised combinations — the backfill loop
    logs a warning and skips the row rather than guessing.

    The truth table:

    ===========  =========  =========  ======================
    source       protocol   auth_type  → driver_type
    ===========  =========  =========  ======================
    user         anthropic  *          custom_anthropic
    user         openai     *          custom_openai
    netmind      *          *          netmind
    netmind_free *          *          netmind_free  (cloud free tier)
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

    # netmind_free is the platform free-tier card — the same NetMind card shape
    # with our gateway wallet as the key, so it maps 1:1 like the others.
    if src in ("netmind", "netmind_free", "yunwu", "openrouter"):
        return src

    if src == "claude_oauth":
        return "claude_oauth"

    if src == "codex_oauth":
        return "codex_oauth"

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
    if auth in SUBSCRIPTION_AUTH_TYPES:
        # Both subscription transports (host-CLI oauth and setup-token) are
        # billed by Anthropic itself — log to cost_records, no quota deduction.
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

# The two CLI-subscription sources — the single membership set shared by
# derive_auth_ref's guard and user_service's _cli_subscription_row_fields.
# Adding a third CLI framework (e.g. gemini_cli) means extending BOTH this
# set and the per-source branches below; the helper's non-None backstop
# turns a half-done extension into a loud ValueError instead of a silent
# driver_type=NULL row.
CLI_SUBSCRIPTION_SOURCES = frozenset({"claude_oauth", "codex_oauth"})
CODEX_CLI_CREDENTIALS_REF = "codex-cli:~/.codex/auth.json"


def derive_auth_ref(
    source: Optional[str],
    auth_type: Optional[str],
) -> Optional[str]:
    """The canonical ``auth_ref`` for a CLI-subscription row — the single
    truth table serving insert time (``_cli_subscription_row_fields``),
    read time (``ProviderCard.from_row``), the startup backfill, and the
    codex config builders.

    Signature is ``(source, auth_type)`` with NO default, aligned with the
    sibling ``derive_driver_type(source, auth_type, protocol)`` /
    ``derive_billing_policy(source, auth_type)``: since the source guard
    landed, omitting ``source`` could only ever return None — a default
    that silently reproduces the P1 this table exists to prevent (empty
    auth_ref on a freshly inserted row). Omission is now a TypeError.

    Only host-CLI OAuth rows of the two CLI-subscription sources get a
    non-null value; everything else uses ``api_key`` directly and leaves
    the reference empty. The source guard lives HERE, not in the callers:
    both consumers (the startup backfill and ``ProviderCard.from_row``'s
    read-time fallback) must answer identically, and an unguarded default
    handed the CLAUDE sentinel to any row whose free-string auth_type said
    "oauth" — letting a card with no credential of its own verify green
    against the host's claude subscription (review round 2, 2026-08-27).
    """
    auth = (auth_type or "").lower()
    src = (source or "").lower()
    if auth != "oauth" or src not in CLI_SUBSCRIPTION_SOURCES:
        return None
    if src == "codex_oauth":
        return CODEX_CLI_CREDENTIALS_REF
    return CLAUDE_CLI_CREDENTIALS_REF


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


def resolve_codex_credentials_path(auth_ref: Optional[str]) -> Optional[Path]:
    """Expand a ``codex-cli:`` sentinel into a real filesystem path.

    Mirrors :func:`resolve_claude_credentials_path`. Order:

    1. ``CODEX_CLI_CREDENTIALS_PATH`` env var (explicit override)
    2. ``CODEX_HOME`` env var + ``auth.json`` (Codex's own override)
    3. ``~/.codex/auth.json`` (default)
    """
    if not auth_ref or not auth_ref.startswith("codex-cli:"):
        return None

    override = os.environ.get("CODEX_CLI_CREDENTIALS_PATH")
    if override:
        return Path(override).expanduser()

    codex_home = os.environ.get("CODEX_HOME")
    if codex_home:
        return Path(codex_home).expanduser() / "auth.json"

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


def pick_default_model(
    card_models,
    source: Optional[str],
    protocol: Optional[str],
    slot_name: Optional[str],
) -> Optional[str]:
    """Choose a safe default model to swap a broken slot to.

    Strategy (first hit wins):

    1. The platform's onboarding default for ``source`` + ``slot_name``
       (``model_catalog.get_onboard_default_model``), when it is on the card —
       so a repair lands where a fresh onboarding would have, not on an
       arbitrary list head. NetMind cards carry the synced catalogue whose head
       is an omni model; taking the head silently re-pointed dozens of slots
       there.
    2. First element of the card's own ``models`` array (user-curated).
    3. Head of ``model_catalog.get_default_models(source, protocol)`` — only
       reached for an empty card.
    4. ``None`` if nothing is usable — the caller should leave the slot alone
       and log an error.

    The catalogue head is deliberately NOT preferred over the card head even
    when it is on the card: it is the head of a committed ledger snapshot (an
    alphabetical list, not a recommendation) and for plain BYOK sources it is
    a flagship id the user never chose.
    """
    # Lazy import to avoid circular deps at module load.
    from narranexus.platform.agent_framework.providers.model_catalog import (
        get_default_models,
        get_onboard_default_model,
    )

    on_card = _normalise_models(card_models)

    onboard_default = get_onboard_default_model(source or "", slot_name)
    if onboard_default and onboard_default in on_card:
        return onboard_default

    if on_card:
        return on_card[0]

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
    "resolve_codex_credentials_path",
    "is_slot_broken",
    "pick_default_model",
    "CLAUDE_CLI_CREDENTIALS_REF",
    "CODEX_CLI_CREDENTIALS_REF",
    "CLI_SUBSCRIPTION_SOURCES",
]

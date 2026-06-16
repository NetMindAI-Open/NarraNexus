"""
@file_name: resolver.py
@author: Bin Liang
@date: 2026-05-07
@description: Resolve an ordered list of transcription candidates for a user

This is the single source of truth for "who can transcribe this user's
audio, and in what order do we try them." It walks the same provider
records the LLM resolver uses but **derives** transcription capability
from each ProviderConfig's base_url — there is no separate
"transcription provider" concept in the data model.

Priority (high → low) — see design doc §7:

  1. user's own OpenAI **official** provider (api.openai.com)
  2. user's own NetMind provider (any *.netmind.ai)
  3. user's other OpenAI-multipart-compatible providers
     (Yunwu, self-hosted whisper.cpp). OpenRouter intentionally
     skipped — its Whisper is JSON+base64, no backend yet.
  4. legacy ``settings.openai_api_key`` (.env classic, local mode only)
  5. system-default NetMind from settings.system_default_netmind_*
     (cloud free tier — only when public_base_url is also configured)

Quota: transcription does NOT call ``cost_tracker.record_cost``
anywhere in the codebase; the system-default NetMind credential is
flagged ``is_system_free_tier=True`` for parity / observability, but
no quota deduction happens for it. See design doc §3 for rationale.
"""
from __future__ import annotations

from typing import List, Optional

from loguru import logger

from xyz_agent_context.agent_framework.transcription.credential import (
    TranscriptionBackendKind,
    TranscriptionCredential,
)
from xyz_agent_context.schema.provider_schema import (
    ProviderConfig,
    ProviderProtocol,
)
from xyz_agent_context.settings import settings


# NetMind's OpenAI-protocol aggregator endpoint that user-providers
# point at. We use it as the *signal* "this user has a NetMind key" but
# the actual transcription runs against NetMind's native /v1/generation,
# which lives at ``https://api.netmind.ai`` (no /inference-api prefix).
_NETMIND_NATIVE_BASE_URL = "https://api.netmind.ai"
_WHISPER_OPENAI_MODEL = "whisper-1"
_WHISPER_NETMIND_MODEL = "openai/whisper"


def _is_active_openai_proto(prov: ProviderConfig) -> bool:
    return (
        bool(prov.is_active)
        and prov.protocol == ProviderProtocol.OPENAI
        and bool(prov.api_key)
        and bool(prov.base_url)
    )


def _is_official_openai(base_url: str) -> bool:
    return "api.openai.com" in (base_url or "").lower()


def _is_netmind(base_url: str) -> bool:
    return "netmind.ai" in (base_url or "").lower()


def _is_openrouter(base_url: str) -> bool:
    return "openrouter.ai" in (base_url or "").lower()


def _to_openai_credential(
    prov: ProviderConfig, source_tag: str
) -> TranscriptionCredential:
    return TranscriptionCredential(
        backend_kind=TranscriptionBackendKind.OPENAI_MULTIPART,
        api_key=prov.api_key,
        base_url=prov.base_url,
        model=_WHISPER_OPENAI_MODEL,
        source_tag=source_tag,
    )


def _to_netmind_user_credential(
    prov: ProviderConfig, source_tag: str
) -> TranscriptionCredential:
    """Map a user's NetMind OpenAI-aggregator provider to a NetMind
    native-transcription credential.

    User configures `https://api.netmind.ai/inference-api/openai/v1`
    for chat. The same API key works for the native /v1/generation
    endpoint at `https://api.netmind.ai`. We keep the user's api_key
    and override the base_url to the native root.
    """
    return TranscriptionCredential(
        backend_kind=TranscriptionBackendKind.NETMIND,
        api_key=prov.api_key,
        base_url=_NETMIND_NATIVE_BASE_URL,
        model=_WHISPER_NETMIND_MODEL,
        source_tag=source_tag,
    )


async def resolve_candidates(user_id: Optional[str]) -> List[TranscriptionCredential]:
    """Return an ordered list of transcription candidates for ``user_id``.

    Empty list ⇒ user has no transcription available.

    All exceptions are swallowed and reported via ``logger.debug`` —
    the caller treats "empty list" and "lookup raised" identically.
    """
    candidates: List[TranscriptionCredential] = []

    # NetMind's `/v1/generation` worker only accepts an http/https
    # `audio_url` — it pulls the audio from us, never the other way.
    # So the credential is only viable on deployments that expose a
    # publicly-fetchable backend (cloud, or self-hosted with
    # PUBLIC_BASE_URL set). On a Tauri / `bash run.sh` machine behind
    # NAT this is False — we skip every NetMind candidate (user-
    # configured AND system default) so the user gets a clean
    # "configure OpenAI" dialog instead of a silent transcription
    # failure when NetMind tries to GET an unreachable URL.
    has_public_ingress = bool((settings.public_base_url or "").strip())

    # --- Tier 1-3: user-configured providers ------------------------------
    if user_id:
        try:
            from xyz_agent_context.agent_framework.user_provider_service import (
                UserProviderService,
            )
            from xyz_agent_context.utils.db_factory import get_db_client

            db = await get_db_client()
            user_cfg = await UserProviderService(db).get_user_config(user_id)
            providers = list((user_cfg.providers or {}).values()) if user_cfg else []

            # Tier 1: user OpenAI official
            for prov in providers:
                if _is_active_openai_proto(prov) and _is_official_openai(prov.base_url):
                    candidates.append(_to_openai_credential(
                        prov, source_tag=f"user_provider:{prov.source.value}:openai_official",
                    ))

            # Tier 2: user NetMind — gated on public ingress (see
            # `has_public_ingress` comment above). Without a reachable
            # URL the credential is decorative.
            if has_public_ingress:
                for prov in providers:
                    if _is_active_openai_proto(prov) and _is_netmind(prov.base_url):
                        candidates.append(_to_netmind_user_credential(
                            prov, source_tag=f"user_provider:{prov.source.value}:netmind",
                        ))

            # Tier 3: user other OpenAI-multipart compatible
            for prov in providers:
                if (
                    _is_active_openai_proto(prov)
                    and not _is_official_openai(prov.base_url)
                    and not _is_netmind(prov.base_url)
                    and not _is_openrouter(prov.base_url)
                ):
                    candidates.append(_to_openai_credential(
                        prov, source_tag=f"user_provider:{prov.source.value}",
                    ))
        except Exception as e:
            logger.debug(f"transcription resolver: user_provider lookup failed: {e}")

    # --- Tier 4: legacy settings.openai_api_key ---------------------------
    # Local-mode only — in cloud mode an unset user provider should fall
    # through to the system default (Tier 5), not silently use a baked-in
    # OpenAI key from the operator's .env.
    try:
        from xyz_agent_context.utils.deployment_mode import is_cloud_mode
        local_mode = not is_cloud_mode()
    except Exception:
        local_mode = True

    if local_mode and settings.openai_api_key:
        candidates.append(TranscriptionCredential(
            backend_kind=TranscriptionBackendKind.OPENAI_MULTIPART,
            api_key=settings.openai_api_key,
            base_url="https://api.openai.com/v1",
            model=_WHISPER_OPENAI_MODEL,
            source_tag="settings.openai",
        ))

    # --- Tier 5: system-default NetMind (cloud free tier) -----------------
    # Gate on the user's "Use free quota" toggle (prefer_system_override).
    # This is the SAME switch chat / helper_llm respect via
    # provider_resolver.py — keeping STT aligned means a single Settings
    # toggle controls all four capabilities. Without this gate a user
    # who explicitly opted out of the free tier (e.g. to keep their own
    # NetMind quota for chat) would still see STT silently route through
    # the operator's NetMind key.
    if user_id and await _user_opted_in_to_free_tier(user_id):
        sys_netmind = _system_default_netmind_credential()
        if sys_netmind is not None:
            candidates.append(sys_netmind)

    return candidates


async def _user_opted_in_to_free_tier(user_id: str) -> bool:
    """True iff the user has the "Use free quota" Settings toggle on
    AND the cloud free tier is enabled at the deployment level.

    Defaults to ``False`` on any error path — opt-in by exception is
    the safer default for a feature that costs the operator money.

    The toggle's source of truth is the ``user_quotas.prefer_system_override``
    column, set via ``QuotaService.set_preference``. New cloud users land
    on True (see backend's quota grant flow); they have to actively
    uncheck it in Settings to opt out, at which point STT MUST stop
    routing through system_default just like chat does.
    """
    try:
        from xyz_agent_context.agent_framework.quota_service import QuotaService
        from xyz_agent_context.agent_framework.system_provider_service import (
            SystemProviderService,
        )

        if not SystemProviderService.instance().is_enabled():
            return False

        try:
            qs = QuotaService.default()
        except RuntimeError:
            # Process didn't bootstrap quota (rare — every cloud entry
            # point should). Treat as opt-out so we don't accidentally
            # bill the operator on a misconfigured worker.
            return False

        quota = await qs.get(user_id)
        return quota is not None and bool(quota.prefer_system_override)
    except Exception as e:
        logger.debug(f"transcription resolver: free-tier opt-in check failed: {e}")
        return False


def _system_default_netmind_credential() -> Optional[TranscriptionCredential]:
    """Build the cloud free-tier NetMind credential, or ``None`` if any
    of the required config is missing.

    Required:
      - ``settings.system_default_netmind_api_key`` — the key NetMind
        bills against. Operator-managed; not exposed in user UI.
      - ``settings.public_base_url`` — externally-reachable URL for
        this deployment so NetMind's worker can fetch the audio. If
        unset we'd mint a URL no one can resolve, so we degrade.
    """
    api_key = (settings.system_default_netmind_api_key or "").strip()
    public_base = (settings.public_base_url or "").strip()
    if not api_key or not public_base:
        return None

    base_url = (settings.system_default_netmind_base_url or _NETMIND_NATIVE_BASE_URL).strip()
    return TranscriptionCredential(
        backend_kind=TranscriptionBackendKind.NETMIND,
        api_key=api_key,
        base_url=base_url,
        model=_WHISPER_NETMIND_MODEL,
        source_tag="system_default:netmind",
        is_system_free_tier=True,
    )

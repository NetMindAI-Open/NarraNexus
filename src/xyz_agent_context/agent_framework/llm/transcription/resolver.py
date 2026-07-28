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
  5. (removed 2026-07-28) the operator's own NetMind key — free-tier
     users now reach STT through their wallet card + the deploy-side proxy
     (cloud free tier — only when public_base_url is also configured)

Quota: transcription does NOT call ``cost_tracker.record_cost``
anywhere in the codebase; the system-default NetMind credential is
flagged ``is_system_free_tier=True`` for parity / observability, but
no quota deduction happens for it. See design doc §3 for rationale.
"""
from __future__ import annotations

import os
from typing import List, Optional

from loguru import logger

from xyz_agent_context.agent_framework.llm.transcription.credential import (
    TranscriptionBackendKind,
    TranscriptionCredential,
)
from xyz_agent_context.schema.provider_schema import (
    ProviderConfig,
    ProviderProtocol,
)
from xyz_agent_context.agent_framework.providers.free_tier import (
    FREE_TIER_SOURCE,
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


def _to_gateway_credential(
    prov: ProviderConfig,
) -> Optional[TranscriptionCredential]:
    """Map the free-tier card to the deploy-side STT proxy.

    The card's api_key IS the user's wallet key, and the proxy authenticates
    with exactly that — so transcription rides the same credential as chat, and
    the operator's NetMind key never has to exist in this process. Returns None
    when no proxy is configured (local / free tier off).
    """
    base = (os.environ.get("FREE_TIER_STT_PROXY_URL") or "").strip()
    if not base:
        return None
    return TranscriptionCredential(
        backend_kind=TranscriptionBackendKind.GATEWAY,
        api_key=prov.api_key,
        base_url=base,
        model="",  # the proxy owns the model choice
        source_tag="free_tier:gateway",
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
            from xyz_agent_context.agent_framework.providers.user_service import (
                UserProviderService,
            )
            from xyz_agent_context.utils.db.db_factory import get_db_client

            db = await get_db_client()
            user_cfg = await UserProviderService(db).get_user_config(user_id)
            providers = list((user_cfg.providers or {}).values()) if user_cfg else []

            # Tier 1: user OpenAI official
            for prov in providers:
                if _is_active_openai_proto(prov) and _is_official_openai(prov.base_url):
                    candidates.append(_to_openai_credential(
                        prov, source_tag=f"user_provider:{prov.source.value}:openai_official",
                    ))

            # Tier 2: NetMind capacity — the user's own key direct, or the
            # free-tier wallet through our STT proxy. Both gated on public
            # ingress (see `has_public_ingress` above): NetMind PULLS the
            # audio either way, so without a reachable URL the credential is
            # decorative.
            if has_public_ingress:
                for prov in providers:
                    if not _is_active_openai_proto(prov):
                        continue
                    if prov.source == FREE_TIER_SOURCE:
                        cred = _to_gateway_credential(prov)
                        if cred is not None:
                            candidates.append(cred)
                    elif _is_netmind(prov.base_url):
                        candidates.append(_to_netmind_user_credential(
                            prov, source_tag=f"user_provider:{prov.source.value}:netmind",
                        ))

            # Tier 3: user other OpenAI-multipart compatible.
            # The free-tier card is deliberately excluded: its base_url points
            # at our LLM gateway, which serves no /audio/transcriptions route,
            # so it would look eligible here and fail on every call. It has its
            # own candidate above (the STT proxy).
            for prov in providers:
                if (
                    _is_active_openai_proto(prov)
                    and prov.source != FREE_TIER_SOURCE
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

    return candidates



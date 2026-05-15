"""
@file_name: service.py
@author: Bin Liang
@date: 2026-05-07
@description: TranscriptionService — single facade for the upload route

Responsibilities
----------------
- Resolve a candidate list for the user (via :mod:`.resolver`).
- Walk the list, dispatching each candidate to its registered backend.
- Apply the per-backend overall timeout from
  :data:`.backends.base.BACKEND_TIMEOUTS_S`.
- Fall through to the next candidate when a backend returns ``None``
  (so a temporary OpenAI outage transparently degrades to NetMind for
  users who configured both).
- Never raise. The upload route's contract requires it.

Usage
-----
::

    from xyz_agent_context.agent_framework.transcription import TranscriptionService

    svc = TranscriptionService.instance()
    if await svc.is_available(user_id):
        text = await svc.transcribe(
            file_path=str(on_disk),
            file_id=file_id,
            agent_id=agent_id,
            user_id=user_id,
        )

The service is a process-level singleton. Backends are stateless so
this is purely a hook for tests to swap implementations via
:meth:`override_backends`.
"""
from __future__ import annotations

import asyncio
from typing import Dict, Optional

from loguru import logger

from xyz_agent_context.agent_framework.transcription.backends import (
    BACKEND_TIMEOUTS_S,
    NetMindBackend,
    OpenAIMultipartBackend,
    TranscriptionBackend,
)
from xyz_agent_context.agent_framework.transcription.credential import (
    TranscriptionBackendKind,
    TranscriptionCredential,
)
from xyz_agent_context.agent_framework.transcription.resolver import (
    resolve_candidates,
)


class TranscriptionAvailability:
    """String constants for the ``reason`` field returned to the frontend
    when the availability endpoint reports `available=true`. Strings
    only — kept simple so the frontend can switch on them without
    importing a Python enum.

    Reason codes returned with ``available=False``:
      - ``NONE`` — no provider configured AND no free tier wired up at
        the deployment level (or running in local mode without a key).
        Dialog: "configure OpenAI/NetMind to enable voice input."
      - ``FREE_TIER_OPTED_OUT`` — no provider configured, but the cloud
        free tier IS available and the user opted out via Settings.
        Dialog: "configure your own OR re-enable free quota in Settings."
    """

    HAS_OPENAI = "has_openai"
    HAS_NETMIND = "has_netmind"
    HAS_OTHER = "has_other"           # Yunwu / self-hosted / settings.openai
    SYSTEM_FREE_TIER = "system_free_tier"
    NONE = "none"
    FREE_TIER_OPTED_OUT = "free_tier_opted_out"
    # No transcription provider AND this deployment can't host NetMind
    # (no PUBLIC_BASE_URL — typical for Tauri / `bash run.sh` desktop).
    # Frontend dialog drops the "or NetMind" branch so we don't tell
    # users to configure something that won't work for them anyway.
    NONE_OPENAI_ONLY = "none_openai_only"


def _classify(creds: list[TranscriptionCredential]) -> str:
    """Pick the user-facing 'reason' string from a candidate list.

    First-match wins, mirrors resolver priority. Used for the
    availability endpoint and for log triage.
    """
    if not creds:
        return TranscriptionAvailability.NONE
    head = creds[0]
    if head.backend_kind == TranscriptionBackendKind.OPENAI_MULTIPART:
        if "api.openai.com" in (head.base_url or "").lower():
            return TranscriptionAvailability.HAS_OPENAI
        if head.source_tag.startswith("settings.openai"):
            return TranscriptionAvailability.HAS_OPENAI
        return TranscriptionAvailability.HAS_OTHER
    if head.backend_kind == TranscriptionBackendKind.NETMIND:
        if head.is_system_free_tier:
            return TranscriptionAvailability.SYSTEM_FREE_TIER
        return TranscriptionAvailability.HAS_NETMIND
    return TranscriptionAvailability.NONE


class TranscriptionService:
    """Process-level singleton that coordinates backends + resolver."""

    _instance: Optional["TranscriptionService"] = None

    def __init__(self) -> None:
        self._backends: Dict[str, TranscriptionBackend] = {
            TranscriptionBackendKind.OPENAI_MULTIPART.value: OpenAIMultipartBackend(),
            TranscriptionBackendKind.NETMIND.value: NetMindBackend(),
        }

    # --- singleton plumbing -------------------------------------------

    @classmethod
    def instance(cls) -> "TranscriptionService":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """Test-only — drop the cached instance so the next ``instance()``
        rebuilds with current settings."""
        cls._instance = None

    def override_backends(
        self, backends: Dict[str, TranscriptionBackend]
    ) -> None:
        """Test seam — swap backend implementations without rebuilding
        the instance. Pass the FULL map you want to use."""
        self._backends = dict(backends)

    # --- public API ---------------------------------------------------

    async def is_available(self, user_id: Optional[str]) -> bool:
        """Cheap pre-check used by the availability endpoint and (legacy)
        upload-route hint. Resolves credentials only, no backend call."""
        creds = await resolve_candidates(user_id)
        return len(creds) > 0

    async def availability_reason(
        self, user_id: Optional[str]
    ) -> tuple[bool, str]:
        """Return ``(available, reason)`` for the frontend dialog.

        When ``creds`` is empty we run a second diagnosis to distinguish
        ``FREE_TIER_OPTED_OUT`` from ``NONE`` so the dialog can offer
        two paths instead of one when applicable.
        """
        creds = await resolve_candidates(user_id)
        if creds:
            return True, _classify(creds)

        # No candidates — figure out *why* so the frontend dialog can
        # tailor the call to action.
        if user_id:
            from xyz_agent_context.agent_framework.transcription.resolver import (
                _system_default_netmind_credential,
                _user_opted_in_to_free_tier,
            )
            sys_cred = _system_default_netmind_credential()
            if sys_cred is not None:
                # Free tier is wired at the deploy level; the only
                # reason we got 0 candidates is the user opted out.
                # (If they were opted in, sys_cred would be in the list.)
                opted_in = await _user_opted_in_to_free_tier(user_id)
                if not opted_in:
                    return False, TranscriptionAvailability.FREE_TIER_OPTED_OUT

        # No public ingress → NetMind isn't viable on this machine.
        # Tell the frontend so the dialog drops the NetMind branch
        # rather than telling Tauri users to configure something they
        # can't use. Importing settings here (not at module scope) to
        # keep this read fresh across reload-config flows.
        from xyz_agent_context.settings import settings
        if not (settings.public_base_url or "").strip():
            return False, TranscriptionAvailability.NONE_OPENAI_ONLY

        return False, TranscriptionAvailability.NONE

    async def transcribe(
        self,
        *,
        file_path: str,
        file_id: str,
        agent_id: str,
        user_id: str,
        language: Optional[str] = None,
    ) -> Optional[str]:
        """Walk the candidate list. Return the first non-empty transcript,
        or ``None`` if every candidate failed.

        Never raises — see module docstring. ``file_id``, ``agent_id``,
        ``user_id`` are passed through to backends that need them
        (NetMind mints signed URLs); OpenAI multipart ignores them.
        """
        creds = await resolve_candidates(user_id)
        if not creds:
            logger.debug(f"transcription: no candidate for user={user_id}")
            return None

        for cred in creds:
            backend = self._backends.get(cred.backend_kind.value)
            if backend is None:
                logger.error(
                    f"transcription: no backend registered for "
                    f"kind={cred.backend_kind.value} (source={cred.source_tag})"
                )
                continue

            timeout_s = BACKEND_TIMEOUTS_S.get(cred.backend_kind.value, 60.0)
            try:
                result = await asyncio.wait_for(
                    backend.transcribe(
                        file_path,
                        cred,
                        file_id=file_id,
                        agent_id=agent_id,
                        user_id=user_id,
                        language=language,
                    ),
                    timeout=timeout_s,
                )
            except asyncio.TimeoutError:
                logger.warning(
                    f"transcription: backend {cred.backend_kind.value} "
                    f"timed out after {timeout_s}s via {cred.source_tag}"
                )
                continue
            except Exception as e:
                # Backends are supposed to never raise — but if one does
                # (programmer bug), don't break the upload route. Log
                # and keep walking.
                logger.exception(
                    f"transcription: backend {cred.backend_kind.value} "
                    f"raised via {cred.source_tag}: {e}"
                )
                continue

            if result:
                logger.info(
                    f"transcription: success via {cred.source_tag} "
                    f"({len(result)} chars, file_id={file_id})"
                )
                return result

            logger.debug(
                f"transcription: backend {cred.backend_kind.value} "
                f"returned None via {cred.source_tag}, trying next candidate"
            )

        return None

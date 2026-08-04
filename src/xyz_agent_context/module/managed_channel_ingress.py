"""
@file_name: managed_channel_ingress.py
@author: NexusAgent
@date: 2026-08-03
@description: Managed-mode trigger executor for platform-forwarded IM turns.

Under Manyfold managed mode the platform owns each channel's connection and
cleaning pipeline (dedup / echo filtering / mention gate) and forwards
inbound IM messages as chat turns to ``POST /v1/chat/completions``. That
endpoint maps the turn onto the channel's native semantics
(``build_inbound_run_context``) — but the native channel triggers also carry
per-message BUSINESS hooks that live on the receive path the platform now
holds: WeChat's first-DM owner claim, NarraMessenger's authorize-event gate,
the inbox write, and the error fallback send.

This coordinator is the managed-mode host for those hooks: it constructs one
trigger instance per channel from CHANNEL_TRIGGER_MAP (``start()`` is never
called — no subscribe loops, no connections) and routes
``managed_before_run`` / ``managed_after_run`` around the agent run. It is a
peer of ``run_channel_triggers`` (a coordinator over the trigger registry),
not a Module — modules stay independent (binding rule #3).

Design: reference/self_notebook/specs/2026-08-03-manyfold-managed-im-ingress-design.md §3.2/Q4/Q6.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Optional

from loguru import logger

from xyz_agent_context.channel.channel_trigger_base import ChannelTriggerBase
from xyz_agent_context.module.channel_trigger_map import CHANNEL_TRIGGER_MAP
from xyz_agent_context.schema.hook_schema import WorkingSource
from xyz_agent_context.schema.parsed_message import (
    ChatType,
    MessageContentType,
    ParsedMessage,
)


def synthesize_managed_message(
    trigger_extra_data: dict, user_input: str
) -> ParsedMessage:
    """Rebuild a minimal ParsedMessage from the managed-IM contract fields.

    Just enough for the trigger business hooks: claim / authorize / inbox /
    error fallback address their work via ``sender_id`` / ``chat_id`` /
    ``chat_type`` / ``raw`` (WeChat's reply routing reads
    ``raw["context_token"]`` — carried as ``reply_token`` on the wire).
    """
    tag = trigger_extra_data.get("channel_tag") or {}
    sender_id = str(tag.get("sender_id", "") or "")
    room_id = str(tag.get("room_id", "") or "")
    chat_type = (
        ChatType.GROUP
        if trigger_extra_data.get("chat_type") == "group"
        else ChatType.PRIVATE
    )
    return ParsedMessage(
        message_id=str(
            trigger_extra_data.get("source_message_id", "")
            or trigger_extra_data.get("trigger_id", "")
        ),
        chat_id=room_id,
        sender_id=sender_id,
        sender_name=str(tag.get("sender_name", "") or sender_id or "user"),
        content=user_input,
        content_type=MessageContentType.TEXT,
        chat_type=chat_type,
        thread_id=(str(trigger_extra_data.get("thread_id") or "") or None),
        raw={
            "managed_ingress": True,
            "from_user_id": sender_id,
            "context_token": str(trigger_extra_data.get("reply_token", "") or ""),
        },
    )


class ManagedChannelIngress:
    """Per-process host of start()-less trigger instances.

    Construction failures are isolated per channel (same defensive stance as
    ``run_channel_triggers``): a channel whose optional dependency is missing
    degrades that channel only. Failure semantics differ by hook kind —
    side-effect channels fail OPEN (the run proceeds; downstream surfaces
    no_credential etc.), while narramessenger fails CLOSED because its hook
    is authorization.
    """

    def __init__(self) -> None:
        self._triggers: dict[str, Optional[ChannelTriggerBase]] = {}

    def _trigger(self, channel: str) -> Optional[ChannelTriggerBase]:
        if channel in self._triggers:
            return self._triggers[channel]
        cls = CHANNEL_TRIGGER_MAP.get(channel)
        trigger: Optional[ChannelTriggerBase] = None
        if cls is None:
            logger.warning(
                f"managed ingress: no trigger class for channel {channel!r}"
            )
        else:
            try:
                trigger = cls()
            except Exception as e:  # noqa: BLE001 — per-channel isolation
                logger.warning(
                    f"managed ingress: constructing {cls.__name__} failed "
                    f"({type(e).__name__}: {e})"
                )
        self._triggers[channel] = trigger
        return trigger

    async def before_run(
        self,
        *,
        working_source: WorkingSource,
        agent_id: str,
        user_input: str,
        trigger_extra_data: dict,
        db: Any,
    ) -> tuple[bool, str]:
        """Run the channel's pre-run business gate. Returns (allow, receipt)."""
        channel = working_source.value
        trigger = self._trigger(channel)
        if trigger is None:
            if working_source is WorkingSource.NARRAMESSENGER:
                return False, (
                    "narramessenger authorization unavailable "
                    "(trigger not loadable)"
                )
            return True, ""
        message = synthesize_managed_message(trigger_extra_data, user_input)
        is_mention = bool(trigger_extra_data.get("is_mention", True))
        try:
            return await trigger.managed_before_run(
                agent_id=agent_id, message=message, db=db, is_mention=is_mention
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(
                f"managed ingress before_run failed for {channel} "
                f"({type(e).__name__}: {e})"
            )
            if working_source is WorkingSource.NARRAMESSENGER:
                # The hook that failed IS the authorization gate: fail-closed.
                return False, "narramessenger authorization failed"
            return True, ""

    async def convert_attachments(
        self,
        *,
        working_source: WorkingSource,
        agent_id: str,
        trigger_extra_data: dict,
        db: Any,
    ) -> None:
        """Convert platform-ingested attachment refs into native Attachments.

        The platform already wrote the bytes into the agent workspace
        (``chat-attachments/<session>/<uuid>/<name>``) and passes
        ``{name, mime, size, path}`` refs on the wire. Native markers
        resolve paths through the upload store's per-day index, so each
        file is re-persisted through ``persist_attachment_bytes`` (store +
        index + Whisper STT) — the managed equivalent of a native
        trigger's ``fetch_attachments``, with "download" replaced by a
        local read. Converted dicts land under
        ``trigger_extra_data["attachments"]`` for the existing marker
        pipeline; the raw key is always consumed. Never raises; a broken
        ref degrades that file to text-only, matching the platform's own
        degrade stance.
        """
        refs = trigger_extra_data.pop("manyfold_attachments", None)
        if not refs:
            return
        try:
            from xyz_agent_context.repository.agent_repository import AgentRepository
            from xyz_agent_context.settings import settings as core_settings
            from xyz_agent_context.utils.attachment_storage import (
                persist_attachment_bytes,
            )
            from xyz_agent_context.utils.mime_sniff import sniff_mime_type
            from xyz_agent_context.utils.workspace_paths import (
                resolve_existing_workspace,
            )

            user_id = await AgentRepository(db).resolve_owner(agent_id) or agent_id
            workspace = resolve_existing_workspace(
                agent_id, user_id, str(core_settings.base_working_path)
            ).resolve(strict=False)
        except Exception as e:  # noqa: BLE001
            logger.warning(
                f"managed ingress: attachment workspace resolution failed for "
                f"{agent_id} ({type(e).__name__}: {e}); continuing text-only"
            )
            return

        converted: list[dict] = []
        for ref in refs:
            try:
                rel = str(ref.get("path", "") or "")
                if not rel:
                    continue
                candidate = Path(rel)
                target = (
                    candidate.resolve(strict=False)
                    if candidate.is_absolute()
                    else (workspace / rel).resolve(strict=False)
                )
                target.relative_to(workspace)  # escape guard
                raw = await asyncio.to_thread(target.read_bytes)
                name = str(ref.get("name", "") or target.name)
                mime = sniff_mime_type(
                    raw,
                    filename=name,
                    client_type=str(ref.get("mime", "") or "") or None,
                )
                att = await persist_attachment_bytes(
                    agent_id,
                    user_id,
                    raw_bytes=raw,
                    original_name=name,
                    mime_type=mime,
                    log_prefix=f"managed:{working_source.value}",
                )
                converted.append(att.model_dump(mode="json"))
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    f"managed ingress: attachment convert failed for "
                    f"{ref!r} ({type(e).__name__}: {e})"
                )
        if converted:
            trigger_extra_data["attachments"] = converted

    async def silent_ingest(
        self,
        *,
        working_source: WorkingSource,
        agent_id: str,
        user_input: str,
        trigger_extra_data: dict,
        db: Any,
    ) -> str:
        """Memory-only ingestion for a non-mention group message.

        Mirrors the native silent path (matrix ``group_silent`` →
        ``_build_and_run_agent_silent_batch``): narrative routing + memory
        write run, the agent LLM step is skipped, and NOTHING is sent to
        the room. The platform forwards these with ``is_mention=false``
        (design Q8); running them as a normal turn would make the agent
        barge into group small talk. Returns a transcript receipt; never
        raises.
        """
        channel = working_source.value
        try:
            trigger = self._trigger(channel)
            if trigger is None:
                return "(silent group message dropped - channel unavailable)"
            message = synthesize_managed_message(trigger_extra_data, user_input)
            # Contract knowledge (dict shape) is the coordinator's; the
            # batch-call shape stays the trigger's (managed_silent_ingest).
            attachments = None
            converted = trigger_extra_data.get("attachments")
            if isinstance(converted, list) and converted:
                from xyz_agent_context.schema.attachment_schema import Attachment

                attachments = [
                    Attachment(**d) for d in converted if isinstance(d, dict)
                ]
            return await trigger.managed_silent_ingest(
                agent_id=agent_id,
                message=message,
                db=db,
                attachments=attachments,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(
                f"managed ingress silent_ingest failed for {channel} "
                f"({type(e).__name__}: {e})"
            )
            return "(silent group message dropped - ingestion failed)"

    async def after_run(
        self,
        *,
        working_source: WorkingSource,
        agent_id: str,
        user_input: str,
        trigger_extra_data: dict,
        db: Any,
        reply_text: str,
        error_text: str = "",
    ) -> None:
        """Run the channel's post-run bookkeeping (inbox / audit / error
        fallback). Best-effort; never raises."""
        trigger = self._trigger(working_source.value)
        if trigger is None:
            return
        message = synthesize_managed_message(trigger_extra_data, user_input)
        try:
            await trigger.managed_after_run(
                agent_id=agent_id,
                message=message,
                db=db,
                reply_text=reply_text,
                error_text=error_text,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(
                f"managed ingress after_run failed for {working_source.value} "
                f"({type(e).__name__}: {e})"
            )


_INGRESS: Optional[ManagedChannelIngress] = None


def get_managed_channel_ingress() -> ManagedChannelIngress:
    """Process-wide singleton (trigger instances are cached per channel)."""
    global _INGRESS
    if _INGRESS is None:
        _INGRESS = ManagedChannelIngress()
    return _INGRESS

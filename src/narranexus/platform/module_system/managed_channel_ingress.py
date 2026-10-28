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
import time
from pathlib import Path
from typing import Any, Optional

from loguru import logger

from narranexus.platform.channel.channel_audit_events import (
    EVENT_MANAGED_ATTACHMENTS,
    EVENT_MANAGED_INGRESS_DENIED,
    EVENT_MANAGED_INGRESS_SILENT,
)
from narranexus.platform.channel.channel_trigger_base import ChannelTriggerBase
from narranexus.platform.channel.ingress_guard import IngressGuard, content_fingerprint
from narranexus.platform.repository.channel_ingress_breaker_repository import (
    ChannelIngressBreakerRepository,
)
from narranexus.platform.repository.channel_trigger_audit_repository import (
    ChannelTriggerAuditRepository,
)
from narranexus.platform.module_system.channel_trigger_map import CHANNEL_TRIGGER_MAP
from narranexus.platform.schema.hook_schema import WorkingSource
from narranexus.platform.schema.parsed_message import (
    ChatType,
    MessageContentType,
    ParsedMessage,
)


def _wire_message_id(trigger_extra_data: dict) -> str:
    """The message identity every managed artifact shares — ParsedMessage
    and audit rows derive through here so the two can never drift."""
    return str(
        trigger_extra_data.get("source_message_id", "")
        or trigger_extra_data.get("trigger_id", "")
        or ""
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
        message_id=_wire_message_id(trigger_extra_data),
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
        # One ingress breaker per channel. Managed mode bypasses the whole
        # native receive path — no _subscribe_loop, no dedup store, no
        # worker queue, no _process_message — so the base class's guard
        # (built in start(), which managed mode never calls) can never fire
        # here. Without this the entire Manyfold surface would be the one
        # unprotected way in.
        self._guards: dict[str, IngressGuard] = {}
        # Next monotonic deadline for each channel's retention sweep. The
        # coordinator has no periodic loop, so the sweep rides the ingress
        # path throttled to once per process-day.
        self._breaker_sweep_next: dict[str, float] = {}

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

    def _guard(
        self, channel: str, trigger: Optional[ChannelTriggerBase], db: Any
    ) -> Optional[IngressGuard]:
        """Lazily build this channel's breaker from ITS OWN tunables.

        Built through ``trigger.build_ingress_guard`` rather than by
        hand-copying the seven thresholds, so a channel that tightens its
        numbers tightens them on both paths. No trigger (class missing /
        construction failed) → no guard: the deny paths that fire when the
        trigger is unavailable are authorization concerns, not rate ones.
        """
        if channel in self._guards:
            return self._guards[channel]
        if trigger is None or not getattr(trigger, "INGRESS_GUARD_ENABLED", False):
            return None
        try:
            guard = trigger.build_ingress_guard(db)
        except Exception as e:  # noqa: BLE001 — a broken trigger fails open
            logger.warning(
                f"managed ingress: building the guard for {channel} failed "
                f"({type(e).__name__}: {e}) — channel runs unguarded"
            )
            return None
        self._guards[channel] = guard
        return guard

    async def _sweep_breaker_rows(
        self, db: Any, channel: str, trigger: ChannelTriggerBase
    ) -> None:
        """Age out this channel's closed breaker rows, once per process-day.

        The coordinator has no periodic loop of its own, so the sweep rides
        the ingress path — the same shape ``manyfold/files.py`` uses for its
        audit retention, and for the same reason.

        It cannot be left to ``ChannelTriggerBase._run_cleanup``: that sweep
        is scoped to its own ``channel_name``, so managed rows would only be
        reclaimed when a NATIVE trigger for the same channel happens to run
        in the same process. A managed-only deployment would keep them
        forever, which is the growth the retention window exists to stop.

        The window comes from the trigger, the same way ``_guard`` takes
        its seven thresholds from ``trigger.build_ingress_guard`` — one
        source for both receive faces.
        """
        # After the guard check, not before: a channel with the breaker
        # switched off has no rows to sweep, and scanning a table it never
        # writes to once per process-day is pure waste.
        if self._guard(channel, trigger, db) is None:
            return
        now = time.monotonic()
        if now < self._breaker_sweep_next.get(channel, 0.0):
            return
        # Armed BEFORE the attempt, deliberately: a failing DB must not be
        # retried on every message for the rest of the day.
        self._breaker_sweep_next[channel] = now + 24 * 3600
        try:
            deleted = await ChannelIngressBreakerRepository(
                db
            ).cleanup_older_than_days(
                # THIS trigger's window, not the base class's. The constant
                # is declared per-trigger exactly like the seven threshold
                # knobs beside it (and like DEDUP_/AUDIT_RETENTION_DAYS,
                # which Lark and Matrix already override), so reading the
                # base value here would reinstate the same defect the
                # channel scoping just removed — only across receive faces
                # instead of across channels: native ticks keep 90 days,
                # managed ticks delete at 30, same table, same channel.
                trigger.INGRESS_BREAKER_RETENTION_DAYS,
                channel=channel,
            )
            if deleted:
                logger.info(
                    f"managed ingress: cleaned {deleted} closed breaker rows "
                    f"for {channel}"
                )
        except Exception as e:  # noqa: BLE001 — retention is a side-channel
            logger.warning(
                f"managed ingress: breaker cleanup failed for {channel} "
                f"({type(e).__name__}: {e})"
            )

    async def _ingress_admitted(
        self,
        *,
        db: Any,
        channel: str,
        trigger: Optional[ChannelTriggerBase],
        agent_id: str,
        message: ParsedMessage,
        trigger_extra_data: dict,
        is_agent_peer: bool,
    ) -> tuple[bool, str]:
        """Managed-mode twin of ``ChannelTriggerBase._ingress_admitted``.

        Returns ``(allow, receipt)`` to match the surrounding gate's shape;
        the receipt is what the caller answers the platform with when no
        run is created.

        Fails OPEN, explicitly (the mirror doc requires each managed gate
        to pick a side): this is a rate guard, not an authorization gate,
        so a broken guard must not black out a channel.

        ``is_agent_peer`` is passed in rather than asked again. The caller
        already answered it to stamp the tag, and asking twice gave the two
        answers different failure modes: a raising override made the tag
        read "human" (one lost signal) but took the WHOLE breaker down for
        that message here, via the except below. One evaluation, one
        degradation.
        """
        guard = self._guard(channel, trigger, db)
        if guard is None:
            return True, ""
        try:
            verdict = await guard.admit(
                agent_id=agent_id,
                channel=channel,
                chat_id=message.chat_id,
                sender_id=message.sender_id,
                fingerprint=content_fingerprint(
                    message.chat_id, message.sender_id, message.content
                ),
                is_agent_peer=is_agent_peer,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(
                f"managed ingress: guard raised for {channel} "
                f"({type(e).__name__}: {e}) — failing open"
            )
            return True, ""

        # Same mapping as the native gate, from the one place that owns it.
        event = verdict.audit_event()
        if event is not None:
            await self._audit(
                db,
                channel,
                event,
                agent_id=agent_id,
                message_id=_wire_message_id(trigger_extra_data),
                chat_id=message.chat_id,
                sender_id=message.sender_id,
                details=verdict.audit_details(),
            )
        if verdict.admit:
            return True, ""
        return False, "conversation temporarily rate-limited (repeat storm)"

    async def before_run(
        self,
        *,
        working_source: WorkingSource,
        agent_id: str,
        user_input: str,
        trigger_extra_data: dict,
        db: Any,
    ) -> tuple[bool, str]:
        """Run the channel's pre-run business gate. Returns (allow, receipt).

        Also stamps the #254 turn envelope (``channel_room_type`` +
        ``channel_reply_kwargs``) onto ``trigger_extra_data``: native turns
        get it from the context builder inside
        ``ChannelTriggerBase.build_trigger_extra_data``, but managed turns
        never run a context builder — without the stamp, step_3 reads every
        managed 1:1 DM as a group room and the no-reply fallback is dead
        code on the whole managed surface.
        """
        channel = working_source.value
        trigger = self._trigger(channel)
        self._stamp_turn_envelope(trigger, trigger_extra_data)
        # A denied inbound produces NO run and NO processed row — without
        # its own event, "the bot ignored me" on a managed channel is
        # unanswerable from the DB (lesson #5). EVERY deny path below
        # audits, including the two infrastructure ones (trigger not
        # loadable, gate crashed): those fail whole channels at once and
        # would otherwise read as "the platform never called us" —
        # bisection pointed exactly backwards.
        if trigger is None:
            if working_source is WorkingSource.NARRAMESSENGER:
                receipt = (
                    "narramessenger authorization unavailable "
                    "(trigger not loadable)"
                )
                await self._audit_deny(
                    db, channel, agent_id, trigger_extra_data, receipt
                )
                return False, receipt
            return True, ""
        message = synthesize_managed_message(trigger_extra_data, user_input)

        # Stamp "is the far side a machine?" onto the wire channel_tag.
        # Native turns get it inside build_trigger_extra_data; managed turns
        # never run a context builder, so without this every managed A2A DM
        # reads as a human conversation to everything downstream.
        #
        # Degrades to False rather than raising, same stance as
        # _stamp_turn_envelope's managed_reply_kwargs call: a trigger too
        # broken to answer this must cost the turn one signal, not take the
        # whole channel down. False is also the pre-existing behaviour.
        # ``trigger is None`` already returned above, so there is no None
        # branch here — an extra one only invites a "cleanup" that takes
        # the stamping with it.
        # Asked ONCE, here, and handed to both consumers below. Asking
        # twice gave the two answers different failure modes: this one
        # degrades to "human" (one lost signal), while the breaker's copy
        # sat inside a broader except that failed the whole gate open for
        # that message.
        try:
            is_agent_peer = trigger.is_agent_peer(message)
        except Exception as e:  # noqa: BLE001
            logger.warning(
                f"managed ingress: is_agent_peer failed for {channel} "
                f"({type(e).__name__}: {e}); treating as human"
            )
            is_agent_peer = False

        tag = trigger_extra_data.get("channel_tag")
        if isinstance(tag, dict) and is_agent_peer:
            # Written only when TRUE, matching ``ChannelTag.to_dict``'s
            # "falsy fields do not appear" rule — otherwise managed turns
            # would carry ``"is_agent_peer": false`` in their persisted tag
            # while native turns carry no key at all, a difference with no
            # meaning that a future snapshot diff would trip over.
            #
            # This stamps the DICT. What the model reads is rebuilt from it
            # by ``retag_managed_input`` after these hooks — remove either
            # half and the signal never reaches the model.
            tag["is_agent_peer"] = True

        # Ingress circuit breaker, BEFORE the channel's business hook and
        # before any run is constructed. Ordered ahead of the business gate
        # deliberately: a conversation we have already isolated should not
        # cost an authorization round-trip per message.
        await self._sweep_breaker_rows(db, channel, trigger)
        admitted, breaker_receipt = await self._ingress_admitted(
            db=db,
            channel=channel,
            trigger=trigger,
            agent_id=agent_id,
            message=message,
            trigger_extra_data=trigger_extra_data,
            is_agent_peer=is_agent_peer,
        )
        if not admitted:
            return False, breaker_receipt

        is_mention = bool(trigger_extra_data.get("is_mention", True))
        try:
            allow, receipt = await trigger.managed_before_run(
                agent_id=agent_id, message=message, db=db, is_mention=is_mention
            )
            if not allow:
                await self._audit_deny(
                    db, channel, agent_id, trigger_extra_data, receipt
                )
            return allow, receipt
        except Exception as e:  # noqa: BLE001
            logger.warning(
                f"managed ingress before_run failed for {channel} "
                f"({type(e).__name__}: {e})"
            )
            if working_source is WorkingSource.NARRAMESSENGER:
                # The hook that failed IS the authorization gate: fail-closed.
                receipt = "narramessenger authorization failed"
                await self._audit_deny(
                    db,
                    channel,
                    agent_id,
                    trigger_extra_data,
                    f"{receipt} ({type(e).__name__}: {e})",
                )
                return False, receipt
            return True, ""

    @staticmethod
    async def _audit(
        db: Any, channel: str, event_type: str, **kwargs: Any
    ) -> None:
        """Direct audit write, no trigger required. Never raises.

        The coordinator writes through the repository itself rather than
        a trigger seam: two of the deny paths fire precisely BECAUSE the
        trigger is unavailable, and an audit mechanism that needs the
        broken component can't record the breakage.
        """
        try:
            await ChannelTriggerAuditRepository(channel, db).append(
                event_type, **kwargs
            )
        except Exception as e:  # noqa: BLE001 — audit is a side-channel
            logger.warning(
                f"managed ingress: audit write failed for {channel} "
                f"({type(e).__name__}: {e})"
            )

    @classmethod
    async def _audit_deny(
        cls,
        db: Any,
        channel: str,
        agent_id: str,
        trigger_extra_data: dict,
        reason: str,
    ) -> None:
        tag = trigger_extra_data.get("channel_tag") or {}
        await cls._audit(
            db,
            channel,
            EVENT_MANAGED_INGRESS_DENIED,
            agent_id=agent_id,
            message_id=_wire_message_id(trigger_extra_data),
            chat_id=str(tag.get("room_id", "") or ""),
            sender_id=str(tag.get("sender_id", "") or ""),
            details={"reason": (reason or "")[:200]},
        )

    @staticmethod
    def _stamp_turn_envelope(
        trigger: Optional[ChannelTriggerBase], trigger_extra_data: dict
    ) -> None:
        """Synthesize the turn envelope for a managed turn. Never raises.

        Room type comes from the wire ``chat_type`` (only ``"group"`` reads
        as a group room — DMs arrive as ``"private"`` or with the field
        absent). Reply kwargs are channel-specific and come from the
        trigger's ``managed_reply_kwargs`` seam; no trigger → no kwargs,
        which step_3 treats as "deliver with target_id only".
        """
        from narranexus.platform.channel.channel_prompts import (
            ROOM_TYPE_DIRECT,
            ROOM_TYPE_GROUP,
        )

        is_group = trigger_extra_data.get("chat_type") == "group"
        trigger_extra_data["channel_room_type"] = (
            ROOM_TYPE_GROUP if is_group else ROOM_TYPE_DIRECT
        )
        if trigger is not None:
            try:
                trigger_extra_data["channel_reply_kwargs"] = (
                    trigger.managed_reply_kwargs(trigger_extra_data)
                )
            except Exception as e:  # noqa: BLE001 — envelope is best-effort
                logger.warning(
                    f"managed ingress: managed_reply_kwargs failed "
                    f"({type(e).__name__}: {e}); turn degrades to no-fallback"
                )

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
            from narranexus.platform.repository.agent_repository import AgentRepository
            from narranexus.platform.settings import settings as core_settings
            from narranexus.platform.utils.attachment_storage import (
                persist_attachment_bytes,
            )
            from narranexus.platform.utils.mime_sniff import sniff_mime_type
            from narranexus.platform.utils.workspace_paths import (
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
            # This is the "ALL declared attachments lost" case — the one
            # the declared-vs-converted row most needs to cover (process
            # logs rotate; the DB row doesn't).
            await self._audit_attachments(
                db,
                working_source.value,
                agent_id,
                trigger_extra_data,
                declared=len(refs),
                converted=0,
                error=f"workspace_resolution: {type(e).__name__}",
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
        # One row per attachment-bearing inbound, whatever the outcome:
        # declared vs converted is the whole diagnosis for "the agent
        # never saw my file" (the converter itself never raises, so a
        # silent shortfall is otherwise invisible outside process logs).
        await self._audit_attachments(
            db,
            working_source.value,
            agent_id,
            trigger_extra_data,
            declared=len(refs),
            converted=len(converted),
        )

    @classmethod
    async def _audit_attachments(
        cls,
        db: Any,
        channel: str,
        agent_id: str,
        trigger_extra_data: dict,
        *,
        declared: int,
        converted: int,
        error: str = "",
    ) -> None:
        tag = trigger_extra_data.get("channel_tag") or {}
        details: dict[str, Any] = {"declared": declared, "converted": converted}
        if error:
            details["error"] = error[:200]
        await cls._audit(
            db,
            channel,
            EVENT_MANAGED_ATTACHMENTS,
            agent_id=agent_id,
            message_id=_wire_message_id(trigger_extra_data),
            chat_id=str(tag.get("room_id", "") or ""),
            sender_id=str(tag.get("sender_id", "") or ""),
            details=details,
        )

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
                from narranexus.platform.schema.attachment_schema import Attachment

                attachments = [
                    Attachment(**d) for d in converted if isinstance(d, dict)
                ]
            receipt = await trigger.managed_silent_ingest(
                agent_id=agent_id,
                message=message,
                db=db,
                attachments=attachments,
            )
            # _audit never raises, so a failed write cannot convert a
            # successful ingest into a "dropped" receipt.
            await self._audit(
                db,
                channel,
                EVENT_MANAGED_INGRESS_SILENT,
                agent_id=agent_id,
                message_id=message.message_id,
                chat_id=message.chat_id,
                sender_id=message.sender_id,
                details={
                    "attachments": len(attachments) if attachments else 0,
                    "receipt": (receipt or "")[:120],
                },
            )
            return receipt
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
        audit_details: Optional[dict] = None,
    ) -> None:
        """Run the channel's post-run bookkeeping (inbox / audit / error
        fallback). Best-effort; never raises. ``audit_details`` is the
        completions endpoint's turn facts (route / duration) for the
        ``managed_ingress_processed`` row."""
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
                audit_details=audit_details,
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

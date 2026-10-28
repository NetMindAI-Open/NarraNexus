"""
@file_name: sync.py
@author: NexusAgent
@date: 2026-07-16
@description: Manyfold managed-trigger surface — config read endpoints,
config-change webhook middleware, and the run-job execution entry.

When NarraNexus runs on a Manyfold cloud sandbox, the sandbox suspends
while idle, so in-process pollers (job_trigger) and long-lived IM
connections (run_channel_triggers) cannot own scheduling or inbound IM.
run.sh skips both when NEXUS_EXTERNAL_TRIGGERS=1 and Manyfold takes over:

  - GET /manyfold/jobs / GET /manyfold/channels — Manyfold pulls the
    authoritative job + channel-binding state and mirrors it into its
    own scheduler (automations) and channel connections.
  - config_change_webhook_middleware — fire-and-forget POST to
    MANYFOLD_SYNC_WEBHOOK_URL after any successful config write, so
    Manyfold pulls immediately after a dashboard change.
  - execute_job_once / parse_run_job_control — Manyfold's mirrored alarm
    fires a chat turn whose prompt is `[[nx:run_job <job_id> v1]]`;
    openai_compat dispatches it here instead of a normal agent run.

Everything is inert without the MANYFOLD_* env (webhook no-ops) and the
routers are only registered when ENABLE_MANYFOLD_API=1 (backend/main.py),
so local / EC2 / DMG deployments behave exactly as before.
"""

from __future__ import annotations

import asyncio
import os
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Awaitable, Callable, Optional

import httpx
from fastapi import APIRouter, Request
from loguru import logger

from narranexus.contracts.job import JobRunOutcome
from narranexus.platform.schema.channel_tag import ChannelTag
from narranexus.platform.schema.hook_schema import WorkingSource
from narranexus.platform.utils.db.db_factory import get_db_client
from narranexus.platform.utils.host_hooks import call_host_hook
from narranexus.platform.utils.plugin_services import try_job_run_once
from narranexus.platform.integrations.manyfold_outbound import (
    managed_reply_declared,
    manyfold_runtime_env,
)
from backend.auth_errors import GATEWAY_TOKEN_INVALID, AuthError


router = APIRouter()


# ---------------------------------------------------------------------------
# Managed-IM inbound run context (model B) — reply via the LOCAL channel tool
# ---------------------------------------------------------------------------

# IM provider → WorkingSource. Naming an origin makes the forwarded turn
# behave like the in-process channel trigger's inbound: the matching channel
# module renders its "reply via <tool>" mode and the agent sends through its
# LOCAL credentials, so the platform only forwards inbound and never touches
# the outbound reply. Providers the platform cannot hand over (slack) or
# deliberately keeps (unknown names) fall back to a plain MANYFOLD turn.
# Design: specs/2026-08-03-manyfold-managed-im-ingress-design.md §3.
def _provider_working_source(provider: str) -> Optional[WorkingSource]:
    """The inbound WorkingSource of an IM provider — any channel with a descriptor in ``ingress.channels``."""
    # The channel registry is populated by the host boot; an unbooted process
    # answers with no channels (the plain MANYFOLD turn), never by importing one.
    from narranexus.platform.module_system.data_access.channel_store import CHANNELS

    key = (provider or "").lower().strip()
    if key not in CHANNELS:
        return None
    try:
        return WorkingSource(key)
    except ValueError:
        return None  # credentials-only channel (no inbound turns)


def _ctx_str(ctx: dict, key: str) -> str:
    """Coerce a channel_context value to a single-line stripped string
    ('' when absent).

    The platform side is TypeScript — ints/None slip through easily and must
    never break dispatch.

    Internal whitespace is COLLAPSED, not just trimmed: the sender tag is a
    single-line protocol (``[Channel · Name · Id · Room]``) and these values
    go straight into it. A display name containing a newline — the platform
    forwards Matrix display names verbatim — would split the tag across two
    lines, in chat history and in whatever reads it back.
    """
    value = ctx.get(key)
    if value is None:
        return ""
    return " ".join(str(value).split())


_FALSY_STRINGS = frozenset({"false", "0", "no", "off", ""})


def _ctx_flag(value: Any) -> bool:
    """Boolean coercion that survives TypeScript stringification: a literal
    "false"/"0" must not become truthy (bool("false") is True — that exact
    slip would make a non-mention group message look mentioned and put the
    agent back into barging on group small talk)."""
    if isinstance(value, str):
        return value.strip().lower() not in _FALSY_STRINGS
    return bool(value)


def build_inbound_run_context(
    *,
    channel_provider: Optional[str],
    channel_context: Optional[dict],
    user_input: str,
    session_id: str,
) -> tuple[WorkingSource, str, dict]:
    """Translate a Manyfold-forwarded turn into an agent-run context.

    Without ``channel_provider`` (or an unknown one) this is a plain
    MANYFOLD turn, unchanged: the reply streams back for the platform to
    deliver.

    With a known IM ``channel_provider`` it mirrors what
    channel_trigger_base does for a native inbound: prefix the input with
    the ChannelTag (so the room_id reaches the agent for its reply tool)
    and carry ``channel_tag`` in ``trigger_extra_data`` (so the channel
    module fills current_sender_id / owner trust). Optional contract
    fields (chat_type / thread_id / reply_token / is_mention /
    attachments) are passed through only when present — raw platform
    attachment dicts travel under ``manyfold_attachments`` and are
    converted to native Attachment objects by the ingress executor, never
    fed to the marker pipeline unconverted.

    Returns ``(working_source, input_content, trigger_extra_data)``.
    """
    ws = _provider_working_source(channel_provider or "")
    if ws is None:
        return (
            WorkingSource.MANYFOLD,
            user_input,
            {"trigger_id": session_id, "retrieval_anchor": user_input},
        )

    ctx = channel_context if isinstance(channel_context, dict) else {}
    sender_id = _ctx_str(ctx, "sender_id")
    sender_name = _ctx_str(ctx, "sender_name") or sender_id or "user"
    room_id = _ctx_str(ctx, "room_id")
    source_message_id = _ctx_str(ctx, "source_message_id")
    # ``is_agent_peer`` cannot be answered here — only the channel's own
    # trigger knows the platform's identity convention, and it does not run
    # until the ingress hooks. Built False, then the tag LINE is re-rendered
    # by ``retag_managed_input`` once the hooks have stamped the dict.
    tag = ChannelTag(
        channel=ws.value,
        sender_name=sender_name,
        sender_id=sender_id,
        room_id=room_id,
        is_agent_peer=False,
    )
    trigger_extra_data: dict[str, Any] = {
        "channel_tag": tag.to_dict(),
        # Native channel triggers anchor retrieval on "[From <name>] <body>".
        "retrieval_anchor": f"[From {sender_name}] {user_input}",
        # Native convention f"{channel}_{message_id}"; the platform dedups
        # upstream, so this is trace identity, not a dedup key.
        "trigger_id": (
            f"{ws.value}_{source_message_id}" if source_message_id else session_id
        ),
        "source_message_id": source_message_id,
        # Lets modules distinguish a platform-forwarded turn from a native
        # trigger turn — narramessenger switches its reply instruction to
        # narra_send (narra_reply's delivery relies on the in-process
        # trigger, which is not running under managed mode).
        "managed_ingress": True,
    }
    chat_type = _ctx_str(ctx, "chat_type").lower()
    if chat_type:
        trigger_extra_data["chat_type"] = chat_type
    for key in ("thread_id", "reply_token"):
        value = _ctx_str(ctx, key)
        if value:
            trigger_extra_data[key] = value
    if "is_mention" in ctx:
        trigger_extra_data["is_mention"] = _ctx_flag(ctx.get("is_mention"))
    attachments = ctx.get("attachments")
    if isinstance(attachments, list):
        cleaned = [a for a in attachments if isinstance(a, dict)]
        if cleaned:
            trigger_extra_data["manyfold_attachments"] = cleaned
    return ws, f"{tag.format()}\n{user_input}", trigger_extra_data


def retag_managed_input(trigger_extra_data: dict, user_input: str) -> str:
    """Re-render the tag line after the ingress hooks have stamped it.

    ``build_inbound_run_context`` has to render the tag to build
    ``run_input``, but some of what belongs ON that tag is only known once
    the channel's trigger has looked at the turn — ``is_agent_peer`` is the
    first such field. The stamp lands in
    ``trigger_extra_data["channel_tag"]`` (a dict), so without this the
    dict and the string the model actually reads disagree: the model gets a
    tag identical to a human conversation's while the DM protocol's
    loop-breaker clause names the very marker that is missing.

    Rendering goes through ``ChannelTag.format()``, the single definition.
    Hand-writing the marker at a second site is exactly the "N hand-rolled
    copies" shape this signal was built to avoid.

    Rebuilds from ``user_input`` with the SAME expression
    ``build_inbound_run_context`` uses, rather than operating on the string
    it produced. Doing string surgery on the rendered form (splitting the
    first line, checking it starts with "[" and ends with "]") looked
    tidier and had a hole: ``sender_name`` is a platform-supplied display
    name and ``_ctx_str`` only strips the ends, so a name containing a
    newline makes ``tag.format()`` itself span two lines — the "first line"
    then does not end in "]", the re-render is skipped, and the marker
    disappears SILENTLY. The party who benefits from that marker
    disappearing is the agent on the other side, and it controls its own
    display name.

    Returns ``user_input`` unchanged when there is nothing to render: a
    plain Manyfold turn carries no ``channel_tag``.
    """
    tag_dict = trigger_extra_data.get("channel_tag")
    if not isinstance(tag_dict, dict) or not tag_dict:
        return user_input
    return f"{ChannelTag.from_dict(tag_dict).format()}\n{user_input}"


def _require_manyfold_auth(request: Request) -> None:
    if not getattr(request.state, "manyfold_authed", False):
        raise AuthError(
            GATEWAY_TOKEN_INVALID,
            "missing or invalid MANYFOLD_GATEWAY_TOKEN",
        )


# ---------------------------------------------------------------------------
# GET /manyfold/jobs — non-terminal jobs, cross-user (single-user container)
# ---------------------------------------------------------------------------

_TERMINAL_JOB_STATUSES = {"completed", "cancelled", "failed"}
_JOBS_LIMIT = 500


def _iso(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


@router.get("/manyfold/jobs")
async def list_jobs_for_manyfold(request: Request):
    """Every non-terminal job in the container. Manyfold mirrors each row
    as a one-shot alarm at next_run_time and re-pulls after every run, so
    this endpoint is read-only and carries no trigger semantics itself."""
    _require_manyfold_auth(request)
    db = await get_db_client()
    rows = await db.get(
        "instance_jobs", {}, order_by="created_at DESC", limit=_JOBS_LIMIT
    ) or []
    if len(rows) >= _JOBS_LIMIT:
        logger.warning(
            f"/manyfold/jobs hit the {_JOBS_LIMIT}-row cap — older jobs are not mirrored"
        )
    data = [
        {
            "job_id": row.get("job_id"),
            "agent_id": row.get("agent_id"),
            "title": row.get("title"),
            "status": row.get("status"),
            "job_type": row.get("job_type"),
            "next_run_time": _iso(row.get("next_run_time")),
            "updated_at": _iso(row.get("updated_at")),
        }
        for row in rows
        if (row.get("status") or "").lower() not in _TERMINAL_JOB_STATUSES
    ]
    return {"data": data, "object": "list"}


# ---------------------------------------------------------------------------
# GET /manyfold/channels — every enabled IM binding with decoded credentials
# ---------------------------------------------------------------------------


def _provider_rank(provider: str) -> tuple[int, str]:
    """Stable payload order: the channel descriptors' ``ui.order`` (the same order
    the settings panel lists them), unknown providers last, ties by name."""
    from narranexus.platform.channel.credential_store import all_descriptors

    orders = {d.name: (d.ui.order if d.ui else 1_000) for d in all_descriptors()}
    return (orders.get(provider, 10_000), provider)


@router.get("/manyfold/channels")
async def list_channels_for_manyfold(request: Request):
    """Uniform view over the six per-provider credential tables. Secrets
    are decoded here on purpose: this endpoint only exists behind the
    gateway token, and Manyfold needs the raw bot credentials to open the
    replacement IM connections (it encrypts them at rest on its side)."""
    _require_manyfold_auth(request)
    db = await get_db_client()
    # Each channel builtin answers onWillExportManagedChannels with its
    # own rows (module/<channel>_module/plugin_hooks.py); a disabled channel
    # simply contributes nothing. Provider order is pinned for a stable payload.
    outcome = await call_host_hook("onWillExportManagedChannels", db=db)
    for owner, exc in outcome.errors:
        logger.warning(f"[manyfold] credential export by {owner} failed: {exc!r}")
    data: list[dict[str, Any]] = [row for rows in outcome.results for row in rows]
    data.sort(key=lambda row: _provider_rank(row.get("provider", "")))

    # Every row declares agent_managed_reply EXPLICITLY. Manyfold's mapper
    # defaults mirrored channels to managed-ON when the key is absent, so an
    # explicit false is what keeps the rollout under this deployment's
    # declaration (NEXUS_MANAGED_REPLY_PROVIDERS) instead of flipping at
    # whichever pull first applies the platform default. One post-pass so a
    # future seventh provider cannot miss the key.
    for row in data:
        row["config"]["agent_managed_reply"] = managed_reply_declared(
            row["provider"]
        )

    return {"data": data, "object": "list"}


# ---------------------------------------------------------------------------
# Config-change webhook — fire-and-forget notify, Manyfold pulls after it
# ---------------------------------------------------------------------------

_WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def _channel_path_prefixes() -> tuple[str, ...]:
    """Routes whose writes change an IM binding: the generic channel router plus
    every registered channel's own router (Lark OAuth, WeChat QR, …) — read
    from the registry so a plugin channel's routes count too."""
    from narranexus.platform.channel.credential_store import all_descriptors

    return ("/api/channels",) + tuple(f"/api/{d.name}" for d in all_descriptors())
# Provider mutations resume PAUSED_NO_QUOTA jobs edge-triggered
# (job_recovery), so they are job-state changes too.
_JOB_PATH_PREFIXES = ("/api/jobs", "/api/providers")

_pending_kinds: set[str] = set()
_flush_task: Optional[asyncio.Task] = None


def _webhook_env() -> Optional[tuple[str, str, str]]:
    """Delegates to the single manyfold env parse (integrations layer) so
    the notify leg and the channel-send leg cannot skew on env semantics.
    The webhook URL requirement is this leg's own: channel-send can run
    on an explicit URL without it, notify cannot."""
    env = manyfold_runtime_env()
    if env is None or not env.webhook_url:
        return None
    return env.webhook_url, env.token, env.runtime_id


def _classify_config_path(path: str) -> Optional[str]:
    for prefix in _JOB_PATH_PREFIXES:
        if path == prefix or path.startswith(prefix + "/"):
            return "jobs"
    for prefix in _channel_path_prefixes():
        if path == prefix or path.startswith(prefix + "/"):
            return "channels"
    return None


def notify_manyfold_config_changed(kinds: set[str]) -> None:
    """Best-effort, never raises. Bursts within the 500ms window collapse
    into one POST; Manyfold treats any notify as 'pull everything', so a
    lost webhook only delays the sync until the next turn/boot pull."""
    env = _webhook_env()
    if not env:
        return
    _pending_kinds.update(kinds)
    global _flush_task
    if _flush_task is None or _flush_task.done():
        _flush_task = asyncio.create_task(_flush_pending(env))
        _flush_task.add_done_callback(_log_flush_outcome)


# Backoff between notify attempts. Tuned to the failure this retries: the
# platform API being briefly unreachable (deploy blip / cold path). Bind-time
# is when a lost notify hurts most — the user walks away to the IM app and
# nothing else triggers a pull (the platform's 5-min reconcile only sweeps
# running sprites) — so three spaced retries are cheap insurance.
_NOTIFY_RETRY_BACKOFF_S: tuple[float, ...] = (1.0, 5.0, 25.0)


async def _flush_pending(env: tuple[str, str, str]) -> None:
    await asyncio.sleep(0.5)
    kinds = set(_pending_kinds)
    _pending_kinds.clear()
    if not kinds:
        return
    url, token, runtime_id = env
    timeout = float(os.environ.get("MANYFOLD_SYNC_WEBHOOK_TIMEOUT_S", "5"))
    last_error: Exception | None = None
    for attempt, delay in enumerate((0.0, *_NOTIFY_RETRY_BACKOFF_S)):
        if delay:
            await asyncio.sleep(delay)
            # Kinds queued while we were failing ride this retry instead of
            # waiting out their own flush cycle (the flush task is single-
            # flight, so nobody else will pick them up until we finish).
            kinds |= _pending_kinds
            _pending_kinds.clear()
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(
                    url,
                    json={"runtimeId": runtime_id, "kinds": sorted(kinds)},
                    headers={"Authorization": f"Bearer {token}"},
                )
                resp.raise_for_status()
            return
        except Exception as e:  # noqa: BLE001 — retried, then surfaced once below
            last_error = e
            logger.warning(
                f"Manyfold config-change webhook attempt {attempt + 1} failed "
                f"({type(e).__name__}: {e})"
            )
    # Every attempt failed: put the batch back so the NEXT config write's
    # flush resends it. Without this, kinds absorbed during the retry
    # window die with the doomed batch — worse than the pre-retry code,
    # where they survived in _pending_kinds for the next task. Notify is
    # "pull everything", so a later resend of stale kinds is harmless.
    _pending_kinds.update(kinds)
    if last_error is not None:
        raise last_error


def _log_flush_outcome(task: asyncio.Task) -> None:
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.warning(f"Manyfold config-change webhook failed: {exc}")


async def config_change_webhook_middleware(
    request: Request, call_next: Callable[[Request], Awaitable[Any]]
):
    """Response-side observer: after a successful write to a job/channel/
    provider route, webhook Manyfold. Transparent for everything else
    (including OPTIONS preflights and non-2xx responses)."""
    response = await call_next(request)
    if (
        request.method in _WRITE_METHODS
        and 200 <= response.status_code < 300
    ):
        kind = _classify_config_path(request.url.path)
        if kind:
            notify_manyfold_config_changed({kind})
    return response


# ---------------------------------------------------------------------------
# Run-job execution entry (dispatched from openai_compat)
# ---------------------------------------------------------------------------

_RUN_JOB_RE = re.compile(r"\A\[\[nx:run_job ([A-Za-z0-9_\-]+) v1\]\]\Z")

# Bounds on how many ADDITIONAL due jobs one dispatch picks up — never on a
# job's own runtime (铁律 #14). The drain keeps module_poller's dependency
# chain alive: a completed job's dependents get next_run_time=NOW and would
# otherwise wait for the next mirrored alarm.
@dataclass
class RunJobOutcome(JobRunOutcome):
    """contracts.job.JobRunOutcome plus the completion text this route streams."""

    def as_text(self) -> str:
        if not self.ok:
            return f"nx:run_job {self.job_id} skipped:{self.reason}"
        return (
            f"nx:run_job {self.job_id} ok"
            f" status={self.status} drained={self.drained}"
        )


def parse_run_job_control(user_input: str) -> Optional[str]:
    """Return the job_id when the input is exactly a run-job control
    message; anything else (including surrounding text) is a normal chat
    turn and must not be intercepted."""
    match = _RUN_JOB_RE.match(user_input.strip())
    return match.group(1) if match else None


async def execute_job_once(agent_id: str, job_id: str) -> RunJobOutcome:
    """Execute one stored job through JobTrigger's own execution body
    (try_acquire_job CAS, prompt build, run, finalize — identical side
    effects to a poller pickup), then drain other due jobs briefly.

    With NEXUS_EXTERNAL_TRIGGERS=1 the poller that used to run the
    maintenance passes is off, so they run here instead: COOLING re-arm
    is purely clock-based and the mirrored alarm fires exactly at
    cooldown_until; the PAUSED_NO_QUOTA backstop keeps quota recovery
    working when the edge-triggered provider-route path was missed.

    Never raises — the caller streams the outcome as a completion.
    """
    try:
        return await _execute_job_once_inner(agent_id, job_id)
    except Exception as e:
        logger.exception(f"run_job {job_id} failed: {e}")
        return RunJobOutcome(job_id=job_id, ok=False, reason="internal_error")


async def _execute_job_once_inner(agent_id: str, job_id: str) -> RunJobOutcome:
    # The execution body (JobTrigger CAS pickup, maintenance passes, drain) is
    # builtin.job's ``jobs.run_once`` service — see job_module/run_once.py.
    runner = try_job_run_once()
    if runner is None:
        return RunJobOutcome(job_id=job_id, ok=False, reason="jobs_unavailable")
    return RunJobOutcome(**asdict(await runner(agent_id, job_id)))

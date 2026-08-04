"""
@file_name: openai_compat.py
@author: NexusAgent
@date: 2026-05-25
@description: OpenAI-compatible chat completions endpoint for Manyfold integration

Exposes ``POST /v1/chat/completions`` so Manyfold platform's
``ApiChatAdapter`` (modelled on openclaw.adapter.ts:90-328) can drive
NarraNexus agents using the standard OpenAI protocol.

Wiring rules (Owner decisions, 2026-05-25):
  * ``model`` field on the request = agent_id (NOT a model name).
  * Streaming: emit OpenAI-shape SSE chunks; finish_reason="stop" + final
    ``data: [DONE]`` sentinel.
  * Each chunk echoes ``model`` = agent_id (the platform-provided value),
    including error responses.
  * Bearer ``MANYFOLD_GATEWAY_TOKEN`` is required (auth middleware filters
    before this handler runs).
  * Endpoint is registered only when ``ENABLE_MANYFOLD_API=1`` — see
    backend/main.py conditional include.

Event-to-chunk translation (see _classify_event):
  * agent_thinking / agent_response token deltas → delta.reasoning_content
    (the agent's inner stream; never the user-visible reply)
  * reply-tool calls → delta.content — which tools count as the reply, and
    how the text is extracted, comes from the per-WorkingSource declaration
    (MessageSourceRegistry), not a hardcoded name list
  * other tool calls → delta.tool_calls; tool output → delta.tool_results
    (non-standard extension, paired FIFO by tool_call_id)
  * terminal (complete / run_ended / done / completed / failed / cancelled)
    → finish_reason chunk + [DONE]; error → OpenAI error envelope shape

Managed-IM dispatch (model B, 2026-08-03): a known ``channel_provider``
maps the turn onto the origin channel's native WorkingSource
(build_inbound_run_context) and runs the channel's business hooks around
it (managed_channel_ingress: owner claim / authorize gate / attachment
conversion / inbox + audit / error fallback). Non-mention group traffic
short-circuits to memory-only silent ingestion. Design:
reference/self_notebook/specs/2026-08-03-manyfold-managed-im-ingress-design.md.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from loguru import logger
from pydantic import BaseModel, Field

from backend.routes.manyfold.sync import (
    build_inbound_run_context,
    execute_job_once,
    parse_run_job_control,
)
from xyz_agent_context.agent_runtime.background_run import BackgroundRun
from xyz_agent_context.agent_runtime.cancellation import CancellationToken
from xyz_agent_context.channel.message_source_handler import (
    MessageSourceHandler,
    MessageSourceRegistry,
)
from xyz_agent_context.schema import WorkingSource
from xyz_agent_context.utils.db.db_factory import get_db_client


router = APIRouter()


# ---------------------------------------------------------------------------
# Request / Response models (OpenAI shape — only fields we read or echo)
# ---------------------------------------------------------------------------


class ChatMessage(BaseModel):
    role: str
    content: Any  # str or list[content_block]


class ChatCompletionsRequest(BaseModel):
    """Subset of the OpenAI chat.completions request body we honor.

    Platform code only relies on ``model``, ``messages``, ``stream`` per
    openclaw.adapter.ts:118+. Unknown fields are ignored (Pydantic default).

    ``channel_provider`` / ``channel_context`` are the managed-IM extension
    (model B): when the platform forwards an inbound IM message (rather
    than a native UI turn), it names the origin channel and carries the
    room/sender identifiers, so the turn behaves like a native channel
    trigger inbound and the agent replies through its LOCAL channel tool.
    Absent these fields the endpoint behaves exactly as before
    (``WorkingSource.MANYFOLD``, platform delivers the reply).
    Contract: specs/2026-08-03-manyfold-managed-im-ingress-design.md §4.1.
    """

    model: str  # = agent_id per Manyfold contract
    messages: list[ChatMessage] = Field(default_factory=list)
    stream: bool = False
    # Managed-IM origin (optional). provider ∈ {lark, slack, telegram,
    # wechat, discord, narramessenger}; context carries room_id / sender_id
    # / sender_name / source_message_id plus the optional v1 additions
    # (chat_type / thread_id / reply_token / is_mention / attachments).
    channel_provider: Optional[str] = None
    channel_context: Optional[dict] = None


# ---------------------------------------------------------------------------
# Auth helper (middleware sets request.state.manyfold_authed already; this
# helper raises a uniform 401 if for some reason it didn't fire)
# ---------------------------------------------------------------------------


def _require_manyfold_auth(request: Request, model_echo: str = "") -> None:
    if not getattr(request.state, "manyfold_authed", False):
        raise HTTPException(
            status_code=401,
            detail=_openai_error(
                "missing or invalid MANYFOLD_GATEWAY_TOKEN",
                etype="invalid_request_error",
                model_echo=model_echo,
            ),
        )


# ---------------------------------------------------------------------------
# OpenAI envelope helpers
# ---------------------------------------------------------------------------


def _openai_error(
    message: str,
    *,
    etype: str = "invalid_request_error",
    code: Optional[str] = None,
    model_echo: str = "",
) -> dict:
    """Build an OpenAI-shape error body. The ``model`` echo is required
    per Owner decision (2026-05-25, Part 4.4): error responses must echo
    the requested agent_id so platform-side logs aggregate correctly."""
    err: dict[str, Any] = {"message": message, "type": etype}
    if code:
        err["code"] = code
    return {"error": err, "model": model_echo}


def _chunk(
    *,
    id_: str,
    created: int,
    model: str,
    delta: dict,
    finish_reason: Optional[str] = None,
) -> str:
    """Format one OpenAI SSE chunk as a ``data: {...}\\n\\n`` line."""
    payload = {
        "id": id_,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [
            {
                "index": 0,
                "delta": delta,
                "finish_reason": finish_reason,
            }
        ],
    }
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _done_sentinel() -> str:
    return "data: [DONE]\n\n"


# ---------------------------------------------------------------------------
# Agent + creator resolution
# ---------------------------------------------------------------------------


async def _resolve_agent_creator(agent_id: str) -> Optional[str]:
    """Look up agents.created_by for the agent. Returns None if not found.
    Delegates to the shared AgentRepository.resolve_owner seam (one home
    for ownership semantics); this wrapper only keeps the Optional
    contract its callers branch on."""
    from xyz_agent_context.repository.agent_repository import AgentRepository

    owner = await AgentRepository(await get_db_client()).resolve_owner(agent_id)
    return owner or None


def _extract_user_input(messages: list[ChatMessage]) -> str:
    """Last ``user`` role message's content becomes the agent input. If the
    content is a list (OpenAI multimodal), join text blocks; ignore others.
    Empty string is allowed (some platforms send empty pings)."""
    last_user = next(
        (m for m in reversed(messages) if m.role == "user"),
        None,
    )
    if not last_user:
        return ""
    c = last_user.content
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        parts: list[str] = []
        for blk in c:
            if isinstance(blk, dict) and blk.get("type") == "text":
                txt = blk.get("text")
                if isinstance(txt, str):
                    parts.append(txt)
        return "\n".join(parts)
    return ""


# ---------------------------------------------------------------------------
# Event → OpenAI delta translation
# ---------------------------------------------------------------------------

# "complete" is the terminal frame BackgroundRun._finalize broadcasts to
# live subscribers right before closing the broadcaster.
_TERMINAL_TYPES = ("complete", "run_ended", "completed", "done", "failed", "cancelled")


def _ensure_source_handlers_registered() -> None:
    """Channel modules register their MessageSourceHandler at import time
    (lark_module, wechat_module, ...). Importing MODULE_MAP forces those
    imports so a managed-IM turn classifies with the channel's declared
    reply tools even when this request is the process's first
    agent-related code path. Cached by sys.modules after the first call."""
    try:
        from xyz_agent_context.module import MODULE_MAP  # noqa: F401
    except Exception as e:  # pragma: no cover - broken module tree
        logger.warning(f"MessageSource registration import failed: {e}")


def _no_reply_fallback(
    working_source: WorkingSource, last_error_msg: Optional[str]
) -> str:
    """Terminal text for a run that produced no delta.content.

    A plain MANYFOLD (owner-chat) turn with no reply usually means an
    upstream LLM error - keep the diagnostic wording. A managed channel
    turn delivers through the agent's LOCAL channel tools, so no
    owner-visible text is a NORMAL outcome (silent group etiquette,
    out-of-band delivery) and gets a neutral receipt instead."""
    if working_source is not WorkingSource.MANYFOLD:
        return (
            f"(channel turn completed - any reply was delivered through "
            f"the agent's {working_source.value} channel tools)"
        )
    return (
        f"(agent ran but produced no user-visible reply; "
        f"this usually means an upstream LLM error - "
        f"check container logs. Last error: {last_error_msg or 'none captured'})"
    )


def _classify_event(
    event: dict, source_handler: MessageSourceHandler
) -> Optional[tuple[str, Any]]:
    """Map a BackgroundRun broadcaster event onto one of four OpenAI
    streaming channels:

    - ``("reasoning", str)``  — agent's inner stream + post-reply self
      monologue. Routed to ``delta.reasoning_content`` (OpenAI o1 /
      DeepSeek convention).
    - ``("content",   str)``  — the user-visible reply text the agent
      explicitly emits via ``send_message_to_user_directly``. Routed
      to ``delta.content``.
    - ``("tool_call", {name, arguments})`` — any OTHER internal tool the
      agent invokes (lark_cli, skill_module, etc.). Routed to
      ``delta.tool_calls[...]`` in standard OpenAI shape.
    - ``("tool_result", str)`` — output produced by the agent's just-
      invoked tool. NarraNexus executes tools INTERNALLY (it doesn't
      ask the client to do it), so the tool result is available right
      after the tool_call event. We surface it as a non-standard
      ``delta.tool_results[...]`` extension so Manyfold's UI can pair
      it with the matching tool_call and stop showing "running"
      forever. See `openclaw.adapter.ts` chunk parser on the Manyfold
      side for the receiving end of this extension.

    Returns ``None`` for events that don't map to any channel (heartbeat,
    progress without tool / output, internal lifecycle, etc.).
    """
    t = event.get("type", "")

    # 1a. Explicit "thinking" events — Claude's chain-of-thought blocks
    # that the SDK surfaces separately from regular response tokens.
    # NarraNexus emits these with type=agent_thinking + thinking_content
    # (NOT delta). Map onto OpenAI delta.reasoning_content.
    if t == "agent_thinking":
        tc = event.get("thinking_content")
        if isinstance(tc, str) and tc:
            return ("reasoning", tc)
        return None

    # 1b. The other inner stream: actual response tokens being generated
    # (before any tool call wrap them up). Same destination —
    # delta.reasoning_content — because for narranexus the ONLY
    # user-facing text is the explicit send_message_to_user_directly
    # tool call below; everything else is chain-of-thought.
    if t == "agent_response":
        d = event.get("delta")
        if isinstance(d, str) and d:
            return ("reasoning", d)
        return None

    # Tool output: explicit (agent_tool_output) or progress-with-output.
    # Surfaced BEFORE the tool_call branch so a `progress` event that
    # carries `output` is routed to tool_result rather than fall through
    # to tool_call (which it isn't — it's the post-call result).
    if t == "agent_tool_output" or (
        t == "progress"
        and isinstance((event.get("details") or {}).get("output"), str)
    ):
        details = event.get("details") or {}
        output = details.get("output")
        if isinstance(output, str) and output:
            return ("tool_result", output)
        return None

    if t in ("progress", "agent_tool_call"):
        details = event.get("details") or {}
        tool_name = details.get("tool_name", "") or ""
        if not tool_name:
            # Pure progress markers (step started / finished) — no
            # OpenAI channel for these. Drop.
            return None
        args = details.get("arguments") or {}
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except Exception:  # noqa: BLE001
                args = {}
        if not isinstance(args, dict):
            args = {}

        # 2. user-visible reply → delta.content. Which tools count as the
        # reply - and how the text is pulled out of their arguments - is
        # the per-WorkingSource declaration (MessageSourceRegistry), the
        # same chain chat_module uses to split user-visible replies. For
        # owner chat the default handler resolves to
        # send_message_to_user_directly + arguments["content"], exactly
        # the retired hardcoded list.
        reply_text = source_handler.extract_reply_text(tool_name, args)
        if reply_text:
            return ("content", reply_text)
        if reply_text is not None:
            # "" = a reply attempt whose text stripped to blank (same
            # line as the other four guards). Not a real tool call
            # either — falling through would leak the internal reply
            # tool's MCP name to external clients as a fake tool_call.
            # Drop the event. (None means "not a reply tool at all" —
            # e.g. a lark_cli non-send command — which IS a real tool
            # call and falls through below.)
            return None

        # 3. all other tools → delta.tool_calls (proper OpenAI schema)
        return ("tool_call", {"name": tool_name, "arguments": args})

    return None


def _is_terminal(event: dict) -> bool:
    t = event.get("type", "")
    return t in _TERMINAL_TYPES


def _is_error(event: dict) -> bool:
    return event.get("type") == "error"


# ---------------------------------------------------------------------------
# Manyfold run-job dispatch (control message short-circuit)
# ---------------------------------------------------------------------------

_RUN_JOB_HEARTBEAT_S = 15.0


async def _run_job_completion(
    *, agent_id: str, job_id: str, stream: bool
):
    """Answer a `[[nx:run_job ...]]` turn with the job's execution outcome
    in both OpenAI shapes. No BackgroundRun is started — execute_job_once
    drives JobTrigger's execution body directly (identical side effects to
    a poller pickup: Inbox writes, next_run_time advance, status flips)."""
    completion_id = f"chatcmpl-{uuid.uuid4().hex[:24]}"
    created_ts = int(time.time())

    task = asyncio.create_task(execute_job_once(agent_id, job_id))
    # A disconnected client must never cancel the job run (铁律 #14) — the
    # task keeps going; the callback just retrieves a potential exception.
    task.add_done_callback(_log_orphaned_run_job)

    if not stream:
        outcome = await asyncio.shield(task)
        return {
            "id": completion_id,
            "object": "chat.completion",
            "created": created_ts,
            "model": agent_id,
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": outcome.as_text(),
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
            },
        }

    async def gen():
        yield _chunk(
            id_=completion_id,
            created=created_ts,
            model=agent_id,
            delta={"role": "assistant", "content": ""},
        )
        while True:
            try:
                outcome = await asyncio.wait_for(
                    asyncio.shield(task), timeout=_RUN_JOB_HEARTBEAT_S
                )
                break
            except asyncio.TimeoutError:
                # Empty-content heartbeat keeps proxies and the platform's
                # idle watchdog from cutting a long job run's stream.
                yield _chunk(
                    id_=completion_id,
                    created=created_ts,
                    model=agent_id,
                    delta={"content": ""},
                )
        yield _chunk(
            id_=completion_id,
            created=created_ts,
            model=agent_id,
            delta={"content": outcome.as_text()},
        )
        yield _chunk(
            id_=completion_id,
            created=created_ts,
            model=agent_id,
            delta={},
            finish_reason="stop",
        )
        yield _done_sentinel()

    return StreamingResponse(gen(), media_type="text/event-stream")


def _log_orphaned_run_job(task: asyncio.Task) -> None:
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.exception(f"run_job task failed: {exc}")


# ---------------------------------------------------------------------------
# Managed-channel run wrappers (gate receipt + post-run bookkeeping)
# ---------------------------------------------------------------------------


def _receipt_completion(
    receipt: str,
    *,
    agent_id: str,
    completion_id: str,
    created_ts: int,
    stream: bool,
):
    """Answer a gate-denied managed turn with a receipt in both OpenAI
    shapes. No agent run happened; the receipt is transcript-facing."""
    if not stream:
        return {
            "id": completion_id,
            "object": "chat.completion",
            "created": created_ts,
            "model": agent_id,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": receipt},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
            },
        }

    async def gen():
        yield _chunk(
            id_=completion_id,
            created=created_ts,
            model=agent_id,
            delta={"role": "assistant", "content": ""},
        )
        yield _chunk(
            id_=completion_id,
            created=created_ts,
            model=agent_id,
            delta={"content": receipt},
        )
        yield _chunk(
            id_=completion_id,
            created=created_ts,
            model=agent_id,
            delta={},
            finish_reason="stop",
        )
        yield _done_sentinel()

    return StreamingResponse(gen(), media_type="text/event-stream")


def _schedule_managed_after_run(managed_ingress, **kwargs) -> None:
    """Fire-and-forget the post-run bookkeeping with a done-callback
    (engineering lesson #2: no unobserved task exceptions)."""
    task = asyncio.create_task(managed_ingress.after_run(**kwargs))
    task.add_done_callback(_log_managed_after_run)


def _log_managed_after_run(task: asyncio.Task) -> None:
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.exception(f"managed ingress after_run task failed: {exc}")


# ---------------------------------------------------------------------------
# Main endpoint
# ---------------------------------------------------------------------------


@router.post("/v1/chat/completions")
async def chat_completions(request: Request, body: ChatCompletionsRequest):
    agent_id = body.model
    _require_manyfold_auth(request, model_echo=agent_id)

    if not agent_id:
        return JSONResponse(
            status_code=400,
            content=_openai_error(
                "missing 'model' field (must be agent_id)",
                model_echo="",
            ),
        )

    creator = await _resolve_agent_creator(agent_id)
    if not creator:
        return JSONResponse(
            status_code=404,
            content=_openai_error(
                f"agent {agent_id!r} not found",
                etype="invalid_request_error",
                code="agent_not_found",
                model_echo=agent_id,
            ),
        )

    user_input = _extract_user_input(body.messages)

    # Manyfold managed-trigger dispatch: a mirrored alarm fires a chat turn
    # whose entire input is a run-job control message. Execute the stored
    # job through JobTrigger's own body instead of starting an agent run
    # around the literal control text. Not env-gated on purpose — the
    # endpoint is already gateway-token-authed and try_acquire_job prevents
    # double execution, so a rollback that leaves stale alarms behind stays
    # harmless.
    run_job_id = parse_run_job_control(user_input)
    if run_job_id is not None:
        return await _run_job_completion(
            agent_id=agent_id,
            job_id=run_job_id,
            stream=body.stream,
        )

    db = await get_db_client()

    session_id = f"manyfold_{uuid.uuid4().hex[:8]}"
    completion_id = f"chatcmpl-{uuid.uuid4().hex[:24]}"
    created_ts = int(time.time())

    # Managed-IM dispatch (model B): a known channel_provider flips this
    # turn from a plain MANYFOLD chat into the matching channel's native
    # inbound semantics. Without the fields this is a byte-for-byte no-op.
    working_source, run_input, trigger_extra_data = build_inbound_run_context(
        channel_provider=body.channel_provider,
        channel_context=body.channel_context,
        user_input=user_input,
        session_id=session_id,
    )

    # The turn's reply surface is declared per WorkingSource - resolve the
    # handler once and classify every streamed event against it.
    _ensure_source_handlers_registered()
    source_handler = MessageSourceRegistry.get(working_source.value)

    # Managed-channel business gate: the pre-run side effects that natively
    # live on the trigger's receive path (WeChat owner claim, NarraMessenger
    # authorize-event). Runs BEFORE any run resource is created; a deny
    # answers with a receipt completion and never starts the agent.
    managed_ingress = None
    if working_source is not WorkingSource.MANYFOLD:
        from xyz_agent_context.module.managed_channel_ingress import (
            get_managed_channel_ingress,
        )

        managed_ingress = get_managed_channel_ingress()
        allowed, receipt = await managed_ingress.before_run(
            working_source=working_source,
            agent_id=agent_id,
            user_input=user_input,
            trigger_extra_data=trigger_extra_data,
            db=db,
        )
        if not allowed:
            return _receipt_completion(
                receipt,
                agent_id=agent_id,
                completion_id=completion_id,
                created_ts=created_ts,
                stream=body.stream,
            )
        # Platform-ingested attachments → native Attachment protocol
        # (store + index + STT) so the marker pipeline works unchanged.
        # Never raises; a broken ref degrades that file to text-only.
        await managed_ingress.convert_attachments(
            working_source=working_source,
            agent_id=agent_id,
            trigger_extra_data=trigger_extra_data,
            db=db,
        )

        # Non-mention group traffic is memory-only (native group_silent
        # semantics): ingest and answer a receipt - never run a reply
        # turn that would barge into the conversation.
        if (
            trigger_extra_data.get("is_mention") is False
            and trigger_extra_data.get("chat_type") == "group"
        ):
            receipt = await managed_ingress.silent_ingest(
                working_source=working_source,
                agent_id=agent_id,
                user_input=user_input,
                trigger_extra_data=trigger_extra_data,
                db=db,
            )
            return _receipt_completion(
                receipt,
                agent_id=agent_id,
                completion_id=completion_id,
                created_ts=created_ts,
                stream=body.stream,
            )

    active_runs = request.app.state.active_runs
    cancellation = CancellationToken()
    bg = BackgroundRun(
        agent_id=agent_id,
        user_id=creator,
        input_preview=user_input or "",
        db=db,
        active_runs=active_runs,
        cancellation=cancellation,
    )

    # Kick off the background agent run.
    bg.task = asyncio.create_task(
        bg.drive(
            agent_id=agent_id,
            user_id=creator,
            input_content=run_input,
            working_source=working_source,
            pass_mcp_servers={},
            trigger_extra_data=trigger_extra_data,
        )
    )

    # Wait until BackgroundRun publishes its run_id (Step 0 emitted),
    # otherwise the broadcaster subscribe race might miss early events.
    try:
        await asyncio.wait_for(bg.ready_event.wait(), timeout=30.0)
    except asyncio.TimeoutError:
        logger.warning(f"[manyfold:{agent_id}] BackgroundRun never went ready in 30s")

    subscriber = bg.broadcaster.subscribe(session_id)

    # -------- Streaming mode --------
    if body.stream:
        async def gen():
            # Initial chunk with role (OpenAI spec — first chunk carries
            # the assistant role, subsequent chunks carry only content
            # deltas).
            yield _chunk(
                id_=completion_id,
                created=created_ts,
                model=agent_id,
                delta={"role": "assistant", "content": ""},
            )
            content_emitted = False
            saw_tool_call = False
            reply_parts: list[str] = []
            last_error_msg: Optional[str] = None
            tool_index = 0  # OpenAI tool_calls each carry a distinct index
            # Pending tool_call_ids that haven't been paired with an
            # output yet. NarraNexus's broadcaster emits tool_call
            # followed by agent_tool_output in order, so we pair FIFO.
            # When a tool_result event fires, we pop the oldest id and
            # send it back as the result's tool_call_id so Manyfold's
            # pairToolBlocks can match them.
            pending_tool_call_ids: list[str] = []
            try:
                async for event in subscriber:
                    logger.info(
                        f"[manyfold:{agent_id}] stream event: type={event.get('type','?')} "
                        f"keys={list(event.keys())[:8]}"
                    )
                    if _is_error(event):
                        # Best-effort: surface the error message as a content
                        # token, then terminate. The OpenAI streaming protocol
                        # does not have a great error-mid-stream story; this
                        # is consistent with how openclaw handles it.
                        msg = (
                            event.get("message")
                            or event.get("error_message")
                            or event.get("error")
                            or "agent error"
                        )
                        last_error_msg = msg
                        yield _chunk(
                            id_=completion_id,
                            created=created_ts,
                            model=agent_id,
                            delta={"content": f"\n[error] {msg}"},
                        )
                        content_emitted = True
                        yield _chunk(
                            id_=completion_id,
                            created=created_ts,
                            model=agent_id,
                            delta={},
                            finish_reason="stop",
                        )
                        yield _done_sentinel()
                        return
                    if _is_terminal(event):
                        if not content_emitted:
                            fallback = _no_reply_fallback(working_source, last_error_msg)
                            yield _chunk(
                                id_=completion_id,
                                created=created_ts,
                                model=agent_id,
                                delta={"content": fallback},
                            )
                        # finish_reason policy: if the agent invoked any
                        # non-reply tools, OpenAI's contract is to report
                        # finish_reason=tool_calls — except we ALSO emitted
                        # final assistant text in the same response. Real
                        # OpenAI lets the assistant either send text OR
                        # call tools, not both. Since user-visible text
                        # is what matters for chat, we report "stop"
                        # when we have content, regardless of tool calls.
                        finish = "stop" if content_emitted else (
                            "tool_calls" if saw_tool_call else "stop"
                        )
                        yield _chunk(
                            id_=completion_id,
                            created=created_ts,
                            model=agent_id,
                            delta={},
                            finish_reason=finish,
                        )
                        yield _done_sentinel()
                        return

                    classified = _classify_event(event, source_handler)
                    if not classified:
                        continue
                    kind, payload = classified
                    if kind == "reasoning":
                        # agent's inner stream (claude SDK token deltas).
                        # OpenAI o1 / DeepSeek convention: delta.reasoning_content.
                        yield _chunk(
                            id_=completion_id,
                            created=created_ts,
                            model=agent_id,
                            delta={"reasoning_content": payload},
                        )
                    elif kind == "content":
                        content_emitted = True
                        reply_parts.append(payload)
                        yield _chunk(
                            id_=completion_id,
                            created=created_ts,
                            model=agent_id,
                            delta={"content": payload},
                        )
                    elif kind == "tool_call":
                        # NarraNexus's progress event fires once per tool
                        # invocation with the full arguments — OpenAI's
                        # streaming usually splits arguments into many
                        # chunks, but emitting it as one chunk is still
                        # valid (parsers concatenate `function.arguments`
                        # across chunks of the same index).
                        saw_tool_call = True
                        tool_call_id = f"call_{uuid.uuid4().hex[:24]}"
                        pending_tool_call_ids.append(tool_call_id)
                        yield _chunk(
                            id_=completion_id,
                            created=created_ts,
                            model=agent_id,
                            delta={
                                "tool_calls": [{
                                    "index": tool_index,
                                    "id": tool_call_id,
                                    "type": "function",
                                    "function": {
                                        "name": payload["name"],
                                        "arguments": json.dumps(
                                            payload["arguments"],
                                            ensure_ascii=False,
                                        ),
                                    },
                                }]
                            },
                        )
                        tool_index += 1
                    elif kind == "tool_result":
                        # NON-STANDARD OpenAI extension. The plain OpenAI
                        # streaming spec doesn't carry tool results in
                        # the assistant's response (they're supposed to
                        # come back in a subsequent message with
                        # role:"tool"). But NarraNexus executes tools
                        # internally and knows the result right away,
                        # and Manyfold's UI needs the result to mark the
                        # tool block as "completed" instead of stuck on
                        # "running". So we emit a parallel
                        # `delta.tool_results[...]` array that Manyfold's
                        # openclaw chunk parser knows to translate into
                        # ``type:'tool_result'`` events. Other OpenAI
                        # clients silently ignore the unknown field.
                        if pending_tool_call_ids:
                            paired_id = pending_tool_call_ids.pop(0)
                        else:
                            # No matching call — emit anyway with a synthetic
                            # id so the result content is still surfaced.
                            # Logged so we can investigate the ordering anomaly.
                            paired_id = f"call_orphan_{uuid.uuid4().hex[:16]}"
                            logger.warning(
                                f"[manyfold:{agent_id}] tool_result with no "
                                f"pending tool_call_id; emitting orphan {paired_id}"
                            )
                        yield _chunk(
                            id_=completion_id,
                            created=created_ts,
                            model=agent_id,
                            delta={
                                "tool_results": [{
                                    "tool_call_id": paired_id,
                                    "content": payload,
                                }]
                            },
                        )
                # Subscriber iterator exhausted (broadcaster closed cleanly)
                # without an explicit terminal event — still send sentinel.
                if not content_emitted:
                    fallback = _no_reply_fallback(working_source, last_error_msg)
                    yield _chunk(
                        id_=completion_id,
                        created=created_ts,
                        model=agent_id,
                        delta={"content": fallback},
                    )
                yield _chunk(
                    id_=completion_id,
                    created=created_ts,
                    model=agent_id,
                    delta={},
                    finish_reason="stop" if content_emitted else (
                        "tool_calls" if saw_tool_call else "stop"
                    ),
                )
                yield _done_sentinel()
            finally:
                bg.broadcaster.unsubscribe(session_id)
                if managed_ingress is not None:
                    _schedule_managed_after_run(
                        managed_ingress,
                        working_source=working_source,
                        agent_id=agent_id,
                        user_input=user_input,
                        trigger_extra_data=trigger_extra_data,
                        db=db,
                        reply_text="\n".join(reply_parts),
                        error_text=last_error_msg or "",
                    )

        return StreamingResponse(
            gen(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    # -------- Non-streaming mode (aggregate then return one response) --------
    parts: list[str] = []
    reasoning_parts: list[str] = []
    tool_call_aggregate: list[dict] = []
    tool_result_aggregate: list[dict] = []  # paired by FIFO with tool_call_aggregate
    nonstream_pending_ids: list[str] = []
    error_msg: Optional[str] = None
    try:
        async for event in subscriber:
            logger.info(
                f"[manyfold:{agent_id}] nonstream event: type={event.get('type','?')} "
                f"keys={list(event.keys())[:8]}"
            )
            if _is_error(event):
                error_msg = event.get("message") or event.get("error") or "agent error"
                break
            if _is_terminal(event):
                break
            classified = _classify_event(event, source_handler)
            if not classified:
                continue
            kind, payload = classified
            if kind == "reasoning":
                reasoning_parts.append(payload)
            elif kind == "content":
                parts.append(payload)
            elif kind == "tool_call":
                tool_call_id = f"call_{uuid.uuid4().hex[:24]}"
                nonstream_pending_ids.append(tool_call_id)
                tool_call_aggregate.append({
                    "id": tool_call_id,
                    "type": "function",
                    "function": {
                        "name": payload["name"],
                        "arguments": json.dumps(
                            payload["arguments"],
                            ensure_ascii=False,
                        ),
                    },
                })
            elif kind == "tool_result":
                paired_id = (
                    nonstream_pending_ids.pop(0)
                    if nonstream_pending_ids
                    else f"call_orphan_{uuid.uuid4().hex[:16]}"
                )
                tool_result_aggregate.append({
                    "tool_call_id": paired_id,
                    "content": payload,
                })
    finally:
        bg.broadcaster.unsubscribe(session_id)
        if managed_ingress is not None:
            _schedule_managed_after_run(
                managed_ingress,
                working_source=working_source,
                agent_id=agent_id,
                user_input=user_input,
                trigger_extra_data=trigger_extra_data,
                db=db,
                reply_text="\n".join(parts),
                error_text=error_msg or "",
            )

    if error_msg:
        return JSONResponse(
            status_code=500,
            content=_openai_error(
                error_msg,
                etype="api_error",
                model_echo=agent_id,
            ),
        )

    full_text = "".join(parts)
    full_reasoning = "".join(reasoning_parts)
    message: dict[str, Any] = {
        "role": "assistant",
        "content": full_text,
    }
    if full_reasoning:
        message["reasoning_content"] = full_reasoning
    if tool_call_aggregate:
        message["tool_calls"] = tool_call_aggregate
    if tool_result_aggregate:
        # Non-standard mirror of the streaming-mode extension. Manyfold's
        # non-streaming branch is rarely used in practice (chat is always
        # streaming), but emitting `tool_results` here keeps the two
        # paths symmetrical so a future caller that flips `stream:false`
        # gets the same pairing behaviour.
        message["tool_results"] = tool_result_aggregate
    return {
        "id": completion_id,
        "object": "chat.completion",
        "created": created_ts,
        "model": agent_id,
        "choices": [
            {
                "index": 0,
                "message": message,
                "finish_reason": "stop" if full_text else (
                    "tool_calls" if tool_call_aggregate else "stop"
                ),
            }
        ],
        "usage": {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        },
    }

"""
@file_name: websocket.py
@author: NetMind.AI
@date: 2025-11-28
@description: WebSocket endpoint for agent runtime streaming

Provides real-time streaming of agent execution via WebSocket.
Messages are streamed as JSON objects following the RuntimeMessage schema.

Protocol:
1. Client connects to /ws/agent/run
2. Client sends JSON: {"agent_id": "...", "user_id": "...", "input_content": "..."}
3. Server streams RuntimeMessage objects as JSON
4. Client may send {"action": "stop"} at any time to cancel the run
5. Connection closes when execution completes or is cancelled

Message Types:
- progress: Step-by-step execution progress
- agent_response: Text output from the agent
- agent_thinking: Agent's thinking process
- tool_call: Tool/function calls
- cancelled: Sent when user cancels the run
- error: Error messages
"""

import asyncio
import json
import traceback
from contextlib import suppress
from typing import Any, Optional
import jwt
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, ValidationError
from loguru import logger

from backend.config import settings
from backend.auth import _is_cloud_mode, decode_token

from xyz_agent_context.agent_runtime import AgentRuntime  # noqa: F401 — kept for legacy fallback
from xyz_agent_context.agent_runtime.background_run import BackgroundRun, run_is_live
from xyz_agent_context.agent_runtime.cancellation import CancellationToken, CancelledByUser
from xyz_agent_context.schema import WorkingSource
from xyz_agent_context.repository import MCPRepository
from xyz_agent_context.utils.db_factory import get_db_client


router = APIRouter()

# WebSocket close codes (RFC 6455 + application-specific)
WS_CLOSE_POLICY_VIOLATION = 1008  # auth failure / policy violation


class AgentRunRequest(BaseModel):
    """WebSocket request payload for running an agent.

    Two operation modes determined by ``run_id``:

    * ``run_id`` is None / omitted → **fresh run**. ``agent_id`` +
      ``user_id`` + ``input_content`` are required; a new BackgroundRun
      is created and the WS subscribes to its Broadcaster.
    * ``run_id`` is set → **reconnect**. The other fields may be
      omitted (they're inferred from the existing events row). The WS
      replays event_stream history from the DB and, if the run is
      still alive, subscribes to the Broadcaster for live continuation.
    """
    agent_id: Optional[str] = None
    user_id: Optional[str] = None
    input_content: Optional[str] = None
    working_source: Optional[str] = "chat"
    # Optional list of attachments uploaded for this turn. Each entry is the
    # JSON form of `xyz_agent_context.schema.Attachment` and is forwarded to
    # the runtime via trigger_extra_data → ctx_data.extra_data["attachments"]
    # so ChatModule's hooks can persist + reference them.
    attachments: Optional[list[dict]] = None
    # JWT token — required in cloud mode, ignored in local mode.
    # Sent in the first WS message because browser WebSocket API cannot
    # set arbitrary Authorization headers.
    token: Optional[str] = None
    # Phase C reconnect mode. When set, the WS handler skips the
    # fresh-run path and instead replays history + subscribes to an
    # existing BackgroundRun.
    run_id: Optional[str] = None


async def _handle_reconnect(
    websocket: WebSocket,
    *,
    run_id: str,
    requesting_user_id: Optional[str],
) -> None:
    """Phase C reconnect path.

    Replays event_stream history from the DB then, if the run is still
    running and a BackgroundRun is alive in active_runs, subscribes the
    WS to its broadcaster for live continuation.

    No CancellationToken, no AgentRuntime instantiation, no MCP load.
    This handler is pure read-side: it never starts new agent work.
    """
    import uuid as _uuid
    ws_session_id = str(_uuid.uuid4())

    db = await get_db_client()
    try:
        events_row = await db.get_one("events", {"event_id": run_id})
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[reconnect] db lookup failed for run_id={run_id!r}: {e}")
        with suppress(Exception):
            await websocket.send_json({
                "type": "error",
                "error_message": "Failed to look up run",
                "error_type": "DBError",
            })
        with suppress(Exception):
            await websocket.close()
        return

    if not events_row:
        with suppress(Exception):
            await websocket.send_json({
                "type": "error",
                "error_message": f"Run {run_id!r} not found",
                "error_type": "NotFound",
            })
        with suppress(Exception):
            await websocket.close()
        return

    # Visibility check — user must own this run (cloud mode). Local mode
    # may have requesting_user_id missing; we still enforce match when
    # the row has a user_id.
    row_user_id = events_row.get("user_id")
    if requesting_user_id and row_user_id and requesting_user_id != row_user_id:
        with suppress(Exception):
            await websocket.send_json({
                "type": "error",
                "error_message": "Run does not belong to this user",
                "error_type": "Forbidden",
            })
        with suppress(Exception):
            await websocket.close()
        return

    # Extract the user's original input + the canonical timestamp that
    # ChatModule will later use when persisting this turn into
    # agent_messages.user_ts (= event.created_at). Frontend uses these
    # to inject the user bubble that triggered this run; the timestamp
    # match guarantees ChatPanel's role:content + 60s dedup collapses
    # the reconnect-injected bubble with the eventual history row,
    # avoiding a duplicate user message after the run completes and
    # history is reloaded.
    #
    # env_context is a JSON-encoded dict {"input": <str>, "timestamp": <iso>}
    # populated by EventService.create_event() at step 0. Parsing failures
    # are non-fatal — we just don't inject the bubble (reconnect still
    # works, user just doesn't see their own question on first paint).
    input_content_str: Optional[str] = None
    try:
        env_raw = events_row.get("env_context")
        if env_raw:
            env_decoded = json.loads(env_raw) if isinstance(env_raw, str) else env_raw
            if isinstance(env_decoded, dict):
                v = env_decoded.get("input")
                if isinstance(v, str) and v:
                    input_content_str = v
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[reconnect] env_context parse failed for run_id={run_id!r}: {e}")

    # Announce session start with the run_id echoed back so the client
    # can confirm + display.
    with suppress(Exception):
        await websocket.send_json({
            "type": "run_reconnect",
            "run_id": run_id,
            "state": events_row.get("state") or "unknown",
            "started_at": _format_dt(events_row.get("started_at")),
            "tool_call_count": events_row.get("tool_call_count") or 0,
            "current_stage": events_row.get("current_stage") or "",
            # Phase C dedup: input_content + input_timestamp let the
            # client paint the user-side bubble while replaying.
            # input_timestamp is events.created_at — the same value
            # ChatModule.hook_after_event_execution will write as
            # agent_messages.user_ts after the run finishes, so the
            # frontend's existing role:content + 60s dedup matches them
            # by exact millisecond rather than by approximation.
            "input_content": input_content_str,
            "input_timestamp": _format_dt(events_row.get("created_at")),
        })

    # Replay all event_stream rows in seq ASC. Errors are non-fatal
    # — the WS still proceeds to the live phase.
    try:
        stream_rows = await db.get("event_stream", {"event_id": run_id})
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[reconnect] event_stream lookup failed for run_id={run_id!r}: {e}")
        stream_rows = []

    stream_rows = sorted(stream_rows or [], key=lambda r: r.get("seq") or 0)
    for row in stream_rows:
        with suppress(Exception):
            await websocket.send_json({
                "type": "replay",
                "kind": row.get("kind"),
                "seq": row.get("seq"),
                "payload": row.get("payload"),
            })

    # Surface final_output if the run is already terminal.
    state = events_row.get("state") or "unknown"
    final_output = events_row.get("final_output") or ""
    if state != "running":
        with suppress(Exception):
            await websocket.send_json({
                "type": "run_ended",
                "state": state,
                "final_output": final_output,
                "error_message": events_row.get("error_message"),
            })
        with suppress(Exception):
            await websocket.close()
        return

    # Run is still running — try to subscribe to the live broadcaster.
    # On a different backend process from the one that started the run
    # the active_runs registry won't contain run_id. In that case all
    # the user gets is the replay above. They can keep polling /api/
    # agents/list to see when state transitions to terminal.
    bg = websocket.app.state.active_runs.get(run_id)
    if bg is None:
        # Distinguish "alive on another process" from "dead without
        # _finalize" using the shared heartbeat-freshness rule (same one
        # the agents listing applies). A stale heartbeat means no process
        # anywhere is driving this run — its task died before writing the
        # terminal row (killed mid-run / failed terminal write). Without
        # this branch the client gets reconnect_warning + close, treats
        # it as a passive disconnect, and reconnect-loops forever with a
        # spinner that only a page refresh clears.
        if not run_is_live(events_row):
            logger.warning(
                f"[reconnect] run_id={run_id} state=running but heartbeat "
                f"stale and no in-memory BackgroundRun — presumed dead, "
                f"reporting run_ended(failed)"
            )
            with suppress(Exception):
                await websocket.send_json({
                    "type": "run_ended",
                    "state": "failed",
                    "final_output": final_output,
                    "error_message": "Run lost (backend restarted or "
                                     "process died mid-run)",
                })
            with suppress(Exception):
                await websocket.close()
            return

        logger.warning(
            f"[reconnect] run_id={run_id} state=running but no in-memory "
            f"BackgroundRun on this process — replay-only, no live subscription"
        )
        with suppress(Exception):
            await websocket.send_json({
                "type": "reconnect_warning",
                "message": "Run is alive on a different backend instance; "
                           "live streaming not available from this connection.",
            })
        with suppress(Exception):
            await websocket.close()
        return

    subscriber = bg.broadcaster.subscribe(ws_session_id)
    try:
        async for event in subscriber:
            try:
                await websocket.send_json(event)
            except RuntimeError:
                logger.info(
                    "[reconnect] WS closed mid-stream. Agent continues; "
                    "user can reconnect again with same run_id."
                )
                break
    except WebSocketDisconnect:
        logger.info("[reconnect] WS disconnected.")
    finally:
        bg.broadcaster.unsubscribe(ws_session_id)


def _format_dt(value: Any) -> Optional[str]:
    """ISO format for any datetime / string value, None passes through."""
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


async def _listen_for_stop(websocket: WebSocket, cancellation: CancellationToken) -> None:
    """
    Background listener: watches for a stop signal from the client.

    Runs concurrently with the agent loop. When the client sends
    {"action": "stop"}, triggers the cancellation token which
    propagates through the entire execution pipeline.

    Any WS close — whether truly client-initiated (tab close, navigate),
    a transport-level drop (network blip), or uvicorn's own ping-timeout
    hard-close — surfaces here as a ``WebSocketDisconnect`` raised from
    ``receive_json``. The distinguishing signal is ``exc.code`` + ``exc.reason``:

    - 1000 / "": normal close (user navigated away etc.)
    - 1001 "going away": tab/page unload
    - 1006 / "": abnormal (TCP reset / proxy kill / uvicorn ping_timeout)
    - 1011 / "keepalive ping timeout": uvicorn's own enforcement — the
      server decided the peer was dead and tore the socket down. Before
      Bug 32 we hit this on every long LLM turn with uvicorn defaults.
    """
    try:
        while not cancellation.is_cancelled:
            data = await websocket.receive_json()
            if not isinstance(data, dict):
                continue
            action = data.get("action")
            if action == "stop":
                # Stop ACK — stage 1 of 3 ("received").
                #
                # Without this ACK the user faces a dumb UI from the
                # moment they click Stop until cleanup completes — which
                # in the worst case (stuck tool call, slow Claude CLI
                # shutdown) is many seconds. Stages 2 ("cleanup") and 3
                # ("complete") fire later from agent_loop and the
                # outer websocket_agent_run finally block.
                with suppress(Exception):
                    await websocket.send_json({
                        "type": "stopping",
                        "stage": "received",
                    })
                cancellation.cancel("User clicked stop")
                return
            if action == "force_stop":
                # Phase D: user-initiated escalation when the graceful
                # stop above has been pending ≥10 s and the agent still
                # has not torn down. We surface a louder cancellation
                # reason so logs distinguish the path; the actual SIGKILL
                # is performed by xyz_claude_agent_sdk's bounded
                # disconnect (Phase A C2) — see the 5-second wait_for
                # + process.kill() fallback. Note: even force_stop is
                # cooperative at the asyncio layer — we don't bypass
                # finally / events-row persistence so the run is left
                # in a clean terminal state.
                with suppress(Exception):
                    await websocket.send_json({
                        "type": "stopping",
                        "stage": "received",
                        "force": True,
                    })
                cancellation.cancel("User force-stopped (escalation)")
                return
    except WebSocketDisconnect as e:
        # Phase C (2026-05-13) — WS disconnect NO LONGER cancels the
        # agent. Iron rule #14: agent runs are first-class and live
        # independently of the WebSocket. The user closed their tab /
        # the network blipped / uvicorn ping-timed-out — agent must
        # keep going. Re-opening the page with run_id reconnects.
        reason = (e.reason or "").strip() or "<no reason>"
        code = getattr(e, "code", None)
        logger.info(
            f"WS closed during run — code={code} reason={reason!r}; "
            f"agent continues in background. Reconnect with run_id "
            f"to resume the live stream."
        )
    except Exception as e:
        # Receive errors other than disconnect — log but do NOT cancel.
        # Same rationale: the agent is not the WebSocket's prisoner.
        logger.warning(
            f"WS receive failed during run — {type(e).__name__}: {e}. "
            f"Agent continues; user can reconnect."
        )


@router.websocket("/ws/agent/run")
async def websocket_agent_run(websocket: WebSocket):
    """
    WebSocket endpoint for streaming agent execution.

    Uses a dual-task pattern:
    - Task A: runs the agent pipeline and streams messages to the client
    - Task B: listens for stop signals from the client

    Both tasks share a CancellationToken. When the client sends stop,
    Task B triggers the token, which causes Task A to exit gracefully.
    """
    await websocket.accept()
    logger.info("WebSocket connection accepted")

    try:
        # Receive and parse request
        request_data = await websocket.receive_json()
        # NEVER log the raw payload — it contains the JWT token in cloud
        # mode and the user's input_content (potentially PII). Only log
        # safe scalar fields here; full DEBUG dump is gated behind redact().
        if isinstance(request_data, dict):
            logger.info(
                "ws.request_received agent_id={a} user_id={u} "
                "working_source={ws} input_len={n}",
                a=request_data.get("agent_id"),
                u=request_data.get("user_id"),
                ws=request_data.get("working_source", "chat"),
                n=len(str(request_data.get("input_content", ""))),
            )
        else:
            logger.info("ws.request_received (non-dict payload)")

        try:
            request = AgentRunRequest(**request_data)
        except ValidationError as e:
            logger.exception(f"Invalid request: {e}")
            await websocket.send_json({
                "type": "error",
                "error_message": f"Invalid request format: {str(e)}",
                "error_type": "ValidationError",
            })
            await websocket.close()
            return

        # ---- Identity check (both modes) ----
        #
        # Browser WebSocket API cannot set Authorization or X-User-Id
        # headers, so identity travels on the first message payload
        # (cloud: JWT token; local: ``user_id``). The auth middleware
        # skips /ws/* exactly because of that — we do the check here.
        #
        # Cloud mode: JWT-derived user_id is authoritative; payload
        # user_id must match the token claim, else reject.
        #
        # Local mode: the WS URL query string ``?x_user_id=<id>`` is the
        # identity anchor (frontend sets it from configStore.userId).
        # The payload ``user_id`` must match. Mismatched / missing →
        # reject. This is the WS analog of the X-User-Id header rule
        # used by the HTTP middleware: we never silently accept a
        # client-supplied identity without a server-anchored value to
        # cross-check it against. (OS-user-is-boundary is still the
        # trust root; this only catches FE state bugs that would
        # otherwise silently scope the run to the wrong user_id and
        # corrupt narratives / cost attribution.)
        if not _is_cloud_mode():
            anchor_uid = websocket.query_params.get("x_user_id", "").strip()
            if not anchor_uid:
                logger.warning(
                    "WS auth failed (local): missing ?x_user_id= on URL"
                )
                await websocket.send_json({
                    "type": "error",
                    "error_message": (
                        "Missing x_user_id query param. The frontend "
                        "must include ?x_user_id=<user_id> on the WS URL."
                    ),
                    "error_type": "AuthError",
                })
                await websocket.close(code=WS_CLOSE_POLICY_VIOLATION)
                return
            if anchor_uid != request.user_id:
                logger.warning(
                    f"WS auth failed (local): URL ?x_user_id={anchor_uid!r} "
                    f"!= payload user_id={request.user_id!r}"
                )
                await websocket.send_json({
                    "type": "error",
                    "error_message": "user_id mismatch between URL and payload",
                    "error_type": "AuthError",
                })
                await websocket.close(code=WS_CLOSE_POLICY_VIOLATION)
                return

        if _is_cloud_mode():
            if not request.token:
                logger.warning("WS auth failed: missing token in cloud mode")
                await websocket.send_json({
                    "type": "error",
                    "error_message": "Authentication required",
                    "error_type": "AuthError",
                })
                await websocket.close(code=WS_CLOSE_POLICY_VIOLATION)
                return
            try:
                payload = decode_token(request.token)
            except jwt.ExpiredSignatureError:
                logger.warning("WS auth failed: token expired")
                await websocket.send_json({
                    "type": "error",
                    "error_message": "Token expired",
                    "error_type": "AuthError",
                })
                await websocket.close(code=WS_CLOSE_POLICY_VIOLATION)
                return
            except jwt.InvalidTokenError as e:
                logger.warning(f"WS auth failed: invalid token ({e})")
                await websocket.send_json({
                    "type": "error",
                    "error_message": "Invalid token",
                    "error_type": "AuthError",
                })
                await websocket.close(code=WS_CLOSE_POLICY_VIOLATION)
                return

            token_user_id = payload.get("user_id")
            if not token_user_id:
                logger.warning("WS auth failed: token missing user_id claim")
                await websocket.send_json({
                    "type": "error",
                    "error_message": "Invalid token claims",
                    "error_type": "AuthError",
                })
                await websocket.close(code=WS_CLOSE_POLICY_VIOLATION)
                return

            if token_user_id != request.user_id:
                logger.warning(
                    f"WS auth failed: user_id mismatch — token={token_user_id}, "
                    f"payload={request.user_id}"
                )
                await websocket.send_json({
                    "type": "error",
                    "error_message": "User ID does not match token",
                    "error_type": "AuthError",
                })
                await websocket.close(code=WS_CLOSE_POLICY_VIOLATION)
                return

            logger.info(f"WS auth OK: user_id={token_user_id}, role={payload.get('role')}")

        # ---- Phase C reconnect branch ----
        # If the client supplied ``run_id``, this WS is reconnecting to
        # an existing BackgroundRun (or, if the run has already ended,
        # to read the persisted history). We replay event_stream rows
        # in order, surface state + final_output, and — only if the run
        # is still running — subscribe to the live broadcaster.
        if request.run_id:
            await _handle_reconnect(
                websocket,
                run_id=request.run_id,
                requesting_user_id=request.user_id,
            )
            return

        # Fresh run validation: agent_id + input_content are required.
        if not request.agent_id or request.input_content is None:
            await websocket.send_json({
                "type": "error",
                "error_message": "agent_id and input_content are required for fresh runs",
                "error_type": "ValidationError",
            })
            await websocket.close()
            return

        # Convert working_source string to enum
        working_source = WorkingSource(request.working_source)

        logger.info(f"Starting agent runtime: agent_id={request.agent_id}, user_id={request.user_id}")

        # ---- Dashboard v2 (TDR-2): register active session AFTER auth passes, ----
        # ---- BEFORE any MCP/runtime setup that can throw. The enclosing try/finally
        # ---- below guarantees removal on every exit path.
        # ---- NOTE: logging discipline — never print SessionInfo fields user_id /
        # ---- user_display / channel (PII). Only session_id + agent_id are log-safe.
        import uuid as _uuid
        from datetime import datetime as _datetime, timezone as _timezone
        from backend.state.active_sessions import get_session_registry as _get_registry, SessionInfo as _SessionInfo

        _session_id = str(_uuid.uuid4())
        _channel = request.working_source or "web"
        _registry = _get_registry()
        await _registry.add(
            request.agent_id,
            _SessionInfo(
                session_id=_session_id,
                user_id=request.user_id,
                user_display=request.user_id,  # refine via channel_tag.sender_name when available
                channel=_channel,
                started_at=_datetime.now(_timezone.utc).isoformat(),
            ),
        )

        # Load MCP URLs from database for this agent+user
        mcp_urls = {}
        try:
            db_client = await get_db_client()
            mcp_repo = MCPRepository(db_client)
            mcps = await mcp_repo.get_mcps_by_agent_user(
                agent_id=request.agent_id,
                user_id=request.user_id,
                is_enabled=True
            )
            for mcp in mcps:
                mcp_urls[mcp.name] = mcp.url
            if mcp_urls:
                logger.info(f"Loaded {len(mcp_urls)} MCP servers: {list(mcp_urls.keys())}")
        except Exception as e:
            logger.warning(f"Failed to load MCP URLs: {e}")

        # ---- Shared cancellation token ----
        # Bound to the BackgroundRun, NOT to this WS task. WS disconnect
        # never triggers cancel (iron rule #14). The only cancel paths are
        # explicit user stop (via _listen_for_stop) and run shutdown on
        # backend exit.
        cancellation = CancellationToken()

        # ---- Heartbeat task ----
        # WS-level heartbeat for browser ping/pong. Independent of the
        # backend's per-run last_event_at heartbeat (which BackgroundRun
        # manages internally).
        heartbeat_stop = asyncio.Event()

        async def heartbeat_loop():
            while not heartbeat_stop.is_set():
                try:
                    await asyncio.wait_for(heartbeat_stop.wait(), timeout=settings.ws_heartbeat_interval)
                    break
                except asyncio.TimeoutError:
                    try:
                        await websocket.send_json({"type": "heartbeat"})
                    except Exception:
                        break

        heartbeat_task = asyncio.create_task(heartbeat_loop())

        # ---- Start stop listener (Task B) ----
        stop_listener = asyncio.create_task(_listen_for_stop(websocket, cancellation))

        import time as _time
        _ws_start = _time.monotonic()

        # ---- Phase C: detach agent execution into BackgroundRun ----
        #
        # The previous implementation drove AgentRuntime.run() directly
        # inside this WS task. WS disconnect → cancellation token fired
        # → agent died on the spot. Iron rule #14 says no: agent runs
        # are first-class and outlive their WebSocket.
        #
        # Now:
        #  1. Create BackgroundRun (broadcaster + heartbeat + DB hooks)
        #  2. asyncio.create_task(bg.drive(...))  — this task is OWNED
        #     by app.state.active_runs[run_id] once Step 0 yields the
        #     event_id; it is NOT awaited from here
        #  3. Subscribe this WS to bg.broadcaster
        #  4. Forward broadcaster events → ws.send_json
        #
        # The BackgroundRun cleans itself up via its own finally block:
        # writes terminal events row, closes broadcaster, removes from
        # active_runs registry. None of that is this WS's responsibility.
        try:
            db_client_for_bg = await get_db_client()
            bg = BackgroundRun(
                agent_id=request.agent_id,
                user_id=request.user_id,
                input_preview=request.input_content or "",
                db=db_client_for_bg,
                active_runs=websocket.app.state.active_runs,
                cancellation=cancellation,
            )

            # Kick off the agent run task. It self-registers in
            # active_runs once Step 0 yields the event_id.
            bg.task = asyncio.create_task(bg.drive(
                agent_id=request.agent_id,
                user_id=request.user_id,
                input_content=request.input_content or "",
                working_source=working_source,
                pass_mcp_urls=mcp_urls,
                trigger_extra_data={
                    "trigger_id": f"ws_{_session_id[:8]}",
                    # Front-end chat input is already a clean user message —
                    # use it directly as the narrative retrieval anchor.
                    "retrieval_anchor": request.input_content or "",
                    **(
                        {"attachments": request.attachments}
                        if request.attachments
                        else {}
                    ),
                },
            ))

            # Wait for run_id assignment (Step 0 completion). After this,
            # bg.run_id is set, the run is in active_runs registry, and
            # the broadcaster is ready to receive subscribers. The wait
            # is bounded by the run's own lifetime — if the run fails
            # before Step 0 yields, _finalize sets ready_event anyway.
            await bg.ready_event.wait()

            # Surface the run_id to the client so it can use it for
            # reconnect / API queries. Always emit, even if run_id
            # never got set (rare — only if Step 0 crashed before
            # yielding) — in that case bg.run_id is None.
            if bg.run_id:
                with suppress(Exception):
                    await websocket.send_json({
                        "type": "run_started",
                        "run_id": bg.run_id,
                    })

            # Subscribe this WS to the broadcaster.
            subscriber = bg.broadcaster.subscribe(_session_id)

            try:
                async for event in subscriber:
                    try:
                        await websocket.send_json(event)
                    except RuntimeError:
                        logger.info(
                            "WebSocket closed during streaming. Agent continues "
                            "in background; reconnect with run_id to resume."
                        )
                        break
            finally:
                bg.broadcaster.unsubscribe(_session_id)

            # If we reached here it's either because the broadcaster
            # closed (run terminal) or the WS dropped. EITHER WAY we
            # do NOT touch bg.task — it owns its own lifecycle.

        except WebSocketDisconnect:
            # Iron rule #14: WS disconnect does NOT cancel the run.
            logger.info(
                "WS disconnected during fresh-run setup or stream. "
                "Agent continues; reconnect with run_id to resume."
            )
        finally:
            heartbeat_stop.set()
            stop_listener.cancel()
            with suppress(asyncio.CancelledError):
                await stop_listener
            with suppress(asyncio.CancelledError):
                await heartbeat_task

        _ws_end = _time.monotonic()
        _total = _ws_end - _ws_start
        logger.info(f"WS session for fresh-run completed — ws-task-total={_total:.1f}s")

    except WebSocketDisconnect:
        logger.info("WebSocket client disconnected")

    except Exception as e:
        logger.exception(f"WebSocket error: {e}")
        logger.exception(traceback.format_exc())
        try:
            await websocket.send_json({
                "type": "error",
                "error_message": str(e),
                "error_type": type(e).__name__,
                "traceback": traceback.format_exc(),
            })
        except Exception:
            pass

    finally:
        # Dashboard v2 (TDR-2): remove session on every exit path. `_session_id`
        # may be unset if we exited before the registry add (auth failure, bad
        # payload) — guard against NameError.
        try:
            if "_session_id" in locals():
                await _registry.remove(request.agent_id, _session_id)
        except Exception as _cleanup_err:
            logger.warning(f"session registry cleanup failed: {_cleanup_err}")
        try:
            await websocket.close()
        except Exception:
            pass
        logger.info("WebSocket connection closed")


@router.websocket("/ws/ping")
async def websocket_ping(websocket: WebSocket):
    """Simple ping/pong WebSocket for connection testing"""
    await websocket.accept()
    logger.info("Ping WebSocket connected")

    try:
        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text("pong")
            else:
                await websocket.send_text(f"echo: {data}")
    except WebSocketDisconnect:
        logger.info("Ping WebSocket disconnected")

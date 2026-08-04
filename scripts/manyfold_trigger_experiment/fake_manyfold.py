#!/usr/bin/env python3
"""
@file_name: fake_manyfold.py
@author: rujing.yan
@date: 2026-07-20
@description: Local stand-in for the Manyfold host, to validate that
NarraNexus's trigger surface can be moved out to an external platform.

This is the experiment harness for platform-managed triggers (model B). It plays
the two roles Manyfold takes over from a suspended sandbox — the "ears" (IM
inbound) and the "clock" (job scheduling) — by speaking the exact HTTP
contracts NarraNexus exposes (PR #118 + the model-B inbound extension). It
does NOT reproduce Firecracker/sprites/suspend-wake; the abstract event flow is
what we validate — a 1:1 local reproduction is impossible and not the goal;
matching the abstract event flow is.

Contracts exercised (all gateway-token authed):
  - GET  /manyfold/jobs      — pull the authoritative job inventory
  - GET  /manyfold/channels  — pull IM bindings + decoded credentials
  - POST /v1/chat/completions with `[[nx:run_job <id> v1]]`  — fire a job
  - POST /v1/chat/completions with channel_provider/channel_context — forward
    an IM inbound so the agent replies via its LOCAL channel tool (model B)
  - a tiny HTTP server receiving the config-change notify webhook

Config via env: NEXUS_BASE_URL (default http://localhost:8000),
MANYFOLD_GATEWAY_TOKEN (must match the running Nexus).

Usage:
  python fake_manyfold.py pull-jobs
  python fake_manyfold.py pull-channels
  python fake_manyfold.py fire-job   --agent <agent_id> --job <job_id>
  python fake_manyfold.py send-im    --agent <agent_id> --provider lark \\
                                     --room oc_test --sender ou_alice \\
                                     --sender-name Alice --text "weather tomorrow?"
  python fake_manyfold.py listen-matrix --agent <agent_id>   # live NM bridge
  python fake_manyfold.py listen-wechat --agent <agent_id>   # live iLink bridge
  python fake_manyfold.py serve-notify --port 9099

The listen-* commands are the platform's replacement connections played
locally (2026-08-03 extension): they pull credentials via
GET /manyfold/channels, long-poll the IM, land media through
POST /manyfold/agents/<id>/files/write (= ingestWorkspace), and forward
each inbound as a v1 channel_context turn — full E2E for the managed-IM
ingress without a Manyfold deployment. Test plan:
reference/self_notebook/plans/2026-08-03-manyfold-im-ingress-local-e2e.md.
Requires NEXUS_EXTERNAL_TRIGGERS=1 on the Nexus (double-consuming the bot
with the native triggers corrupts every result).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Optional

import httpx

BASE_URL = os.environ.get("NEXUS_BASE_URL", "http://localhost:8000").rstrip("/")
TOKEN = os.environ.get("MANYFOLD_GATEWAY_TOKEN", "")

# ANSI helpers for a readable PASS/FAIL surface.
_GREEN, _RED, _DIM, _RESET = "\033[32m", "\033[31m", "\033[2m", "\033[0m"


def _client() -> httpx.Client:
    if not TOKEN:
        _die("MANYFOLD_GATEWAY_TOKEN is not set (must match the running Nexus).")
    return httpx.Client(
        base_url=BASE_URL,
        headers={"Authorization": f"Bearer {TOKEN}"},
        timeout=120.0,
    )


def _die(msg: str) -> None:
    print(f"{_RED}✗ {msg}{_RESET}", file=sys.stderr)
    sys.exit(1)


def _ok(msg: str) -> None:
    print(f"{_GREEN}✓ {msg}{_RESET}")


# ---------------------------------------------------------------------------
# The "clock" — pull inventory, fire jobs
# ---------------------------------------------------------------------------


def pull_jobs() -> list[dict]:
    with _client() as c:
        r = c.get("/manyfold/jobs")
    r.raise_for_status()
    data = r.json().get("data", [])
    print(f"{_DIM}GET /manyfold/jobs → {len(data)} non-terminal job(s){_RESET}")
    for j in data:
        print(
            f"  · {j.get('job_id')}  agent={j.get('agent_id')}  "
            f"status={j.get('status')}  next_run={j.get('next_run_time')}"
        )
    return data


def pull_channels() -> list[dict]:
    with _client() as c:
        r = c.get("/manyfold/channels")
    r.raise_for_status()
    data = r.json().get("data", [])
    print(f"{_DIM}GET /manyfold/channels → {len(data)} binding(s){_RESET}")
    for ch in data:
        print(
            f"  · {ch.get('provider')}  agent={ch.get('agent_id')}  "
            f"enabled={ch.get('enabled')}  external_id={ch.get('external_id')}"
        )
    return data


def fire_job(agent_id: str, job_id: str) -> None:
    """Simulate a mirrored alarm firing: send the run-job control message and
    read back the JobTrigger execution outcome.

    Uses stream=True — the real Manyfold alarm call streams, and the run-job
    dispatch emits a 15s empty-content heartbeat so a long run (target job +
    bounded drain of other due jobs) never trips a proxy / read timeout."""
    control = f"[[nx:run_job {job_id} v1]]"
    print(f"{_DIM}POST /v1/chat/completions  model={agent_id}  '{control}'  (stream){_RESET}")
    body = {
        "model": agent_id,
        "messages": [{"role": "user", "content": control}],
        "stream": True,
    }
    content = ""
    with _client() as c:
        with c.stream("POST", "/v1/chat/completions", json=body) as r:
            if r.status_code != 200:
                _die(f"HTTP {r.status_code}: {r.read().decode()[:300]}")
            for chunk in _iter_sse_content(r):
                content += chunk
    content = content.strip()
    if content.startswith("nx:run_job") and " ok " in f" {content} ":
        _ok(f"job executed → {content}")
    else:
        print(f"{_RED}✗ unexpected run-job outcome → {content!r}{_RESET}")


def _iter_sse_content(response: httpx.Response):
    """Yield delta.content strings from an OpenAI-shaped SSE stream."""
    for line in response.iter_lines():
        if not line or not line.startswith("data: "):
            continue
        data = line[len("data: "):]
        if data.strip() == "[DONE]":
            break
        try:
            delta = json.loads(data)["choices"][0]["delta"]
        except (json.JSONDecodeError, KeyError, IndexError):
            continue
        piece = delta.get("content")
        if piece:
            yield piece


# ---------------------------------------------------------------------------
# The "ears" — forward an IM inbound (model B)
# ---------------------------------------------------------------------------


def send_im(
    agent_id: str,
    provider: str,
    room: str,
    sender: str,
    sender_name: str,
    text: str,
) -> None:
    """Forward an inbound IM message with channel context and assert the agent
    replies via its LOCAL channel tool (model B) targeting the right room —
    NOT via send_message_to_user_directly (which would be model A)."""
    body = {
        "model": agent_id,
        "messages": [{"role": "user", "content": text}],
        "stream": False,
        "channel_provider": provider,
        "channel_context": {
            "room_id": room,
            "sender_id": sender,
            "sender_name": sender_name,
            "source_message_id": "om_experiment_1",
        },
    }
    print(
        f"{_DIM}POST /v1/chat/completions  model={agent_id}  "
        f"provider={provider} room={room} sender={sender}{_RESET}"
    )
    with _client() as c:
        r = c.post("/v1/chat/completions", json=body)
    if r.status_code != 200:
        _die(f"HTTP {r.status_code}: {r.text[:400]}")

    msg = _first_message(r.json())
    tool_calls = msg.get("tool_calls") or []
    content = msg.get("content") or ""

    # The local channel reply tool for this provider (model B).
    local_tool = {
        "lark": "lark_cli",
        "slack": "slack_cli",
        "telegram": "tg_cli",
        "wechat": "wechat_send",
        "discord": "discord_send",
    }.get(provider, "")

    print(f"{_DIM}  tool_calls: {[_tc_name(t) for t in tool_calls]}{_RESET}")
    if content:
        print(f"{_DIM}  delta.content (model-A path): {content[:120]!r}{_RESET}")

    matched = _find_local_reply(tool_calls, local_tool, room)
    if matched:
        _ok(
            f"model B confirmed: agent replied via LOCAL {local_tool} to the "
            f"right room ({room}).\n    call: {matched[:160]}"
        )
    else:
        print(
            f"{_RED}✗ no local {local_tool!r} reply targeting room {room!r} found. "
            f"Agent may have used send_message_to_user_directly (model A) or lacks "
            f"channel context / a bound credential.{_RESET}"
        )


def _find_local_reply(
    tool_calls: list[dict], local_tool: str, room: str
) -> Optional[str]:
    """Return the matching reply-tool call string, or None. Matches any tool
    whose name contains the provider's local reply tool and whose arguments
    carry a send command aimed at the room."""
    for tc in tool_calls:
        name = _tc_name(tc)
        if local_tool and local_tool not in name:
            continue
        args = _tc_args(tc)
        blob = json.dumps(args, ensure_ascii=False)
        if room in blob and ("messages-send" in blob or "messages-reply" in blob or "send" in name):
            return f"{name}({blob})"
    return None


def _tc_name(tc: dict) -> str:
    return (tc.get("function") or {}).get("name", "") or tc.get("name", "")


def _tc_args(tc: dict) -> Any:
    raw = (tc.get("function") or {}).get("arguments")
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {"_raw": raw}
    return raw or {}


def _first_message(resp: dict) -> dict:
    choices = resp.get("choices") or [{}]
    return choices[0].get("message") or {}


# ---------------------------------------------------------------------------
# Config-change notify receiver (the webhook Nexus fires after a config write)
# ---------------------------------------------------------------------------


class _NotifyHandler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802 (stdlib naming)
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b""
        try:
            payload = json.loads(raw or b"{}")
        except json.JSONDecodeError:
            payload = {"_raw": raw.decode("utf-8", "replace")}
        auth = self.headers.get("Authorization", "")
        print(f"{_GREEN}▶ notify{_RESET} kinds={payload.get('kinds')} "
              f"runtimeId={payload.get('runtimeId')} auth={'yes' if auth else 'no'}")
        # A real Manyfold would now re-pull; show that it can.
        if TOKEN:
            try:
                pull_jobs()
            except Exception as e:  # noqa: BLE001 — best-effort demo pull
                print(f"{_DIM}  (re-pull failed: {e}){_RESET}")
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'{"ok":true}')

    def log_message(self, *_args) -> None:  # silence default access logging
        pass


def serve_notify(port: int) -> None:
    print(f"fake Manyfold notify receiver on http://localhost:{port}/notify")
    print(f"{_DIM}point MANYFOLD_SYNC_WEBHOOK_URL at it; Ctrl-C to stop{_RESET}")
    HTTPServer(("0.0.0.0", port), _NotifyHandler).serve_forever()


# ---------------------------------------------------------------------------
# Live listeners — the platform's replacement connections, played locally
# ---------------------------------------------------------------------------
#
# Prereq on the Nexus side: NEXUS_EXTERNAL_TRIGGERS=1 (native channel
# triggers OFF — otherwise this listener and the in-process trigger would
# double-consume the same bot, the exact conflict managed mode exists to
# avoid) + ENABLE_MANYFOLD_API=1 + MANYFOLD_GATEWAY_TOKEN.


def _fetch_channels() -> list[dict]:
    with _client() as c:
        r = c.get("/manyfold/channels")
    r.raise_for_status()
    return r.json().get("data", [])


def _find_binding(provider: str, agent_id: str) -> dict:
    rows = [
        ch
        for ch in _fetch_channels()
        if ch.get("provider") == provider
        and ch.get("agent_id") == agent_id
        and ch.get("enabled")
    ]
    if not rows:
        _die(f"no enabled {provider} binding for agent {agent_id} "
             f"(bind it in the Nexus UI first, then re-run)")
    return rows[0]


def upload_workspace_file(agent_id: str, rel_path: str, data: bytes) -> dict:
    """POST bytes to the gateway write endpoint — exactly what the platform's
    ingestWorkspace does once narraNexusCtx.write is wired here."""
    with _client() as c:
        r = c.post(
            f"/manyfold/agents/{agent_id}/files/write",
            # overwrite=true keeps a re-forwarded event's re-upload (same
            # event-id path) idempotent instead of 409ing the retry.
            params={"path": rel_path, "overwrite": "true"},
            content=data,
        )
    if r.status_code != 200:
        _die(f"files/write HTTP {r.status_code}: {r.text[:300]}")
    return r.json()


def _stream_and_print(body: dict) -> tuple[str, list[str]]:
    """POST a streaming completion and render the transcript channels the
    way manyfold.ai would: reasoning dimmed, tool calls labelled, content
    highlighted. Returns (content, tool_call_names)."""
    content_parts: list[str] = []
    tool_names: list[str] = []
    with _client() as c:
        with c.stream("POST", "/v1/chat/completions", json=body) as r:
            if r.status_code != 200:
                _die(f"HTTP {r.status_code}: {r.read().decode()[:400]}")
            for line in r.iter_lines():
                if not line or not line.startswith("data: "):
                    continue
                data = line[len("data: "):]
                if data.strip() == "[DONE]":
                    break
                try:
                    delta = json.loads(data)["choices"][0]["delta"]
                except (json.JSONDecodeError, KeyError, IndexError):
                    continue
                if delta.get("reasoning_content"):
                    print(f"{_DIM}{delta['reasoning_content']}{_RESET}", end="", flush=True)
                for tc in delta.get("tool_calls") or []:
                    name = _tc_name(tc)
                    tool_names.append(name)
                    print(f"\n{_DIM}⚙ {name} {json.dumps(_tc_args(tc), ensure_ascii=False)[:160]}{_RESET}")
                for tr in delta.get("tool_results") or []:
                    print(f"{_DIM}  ↳ {str(tr.get('content'))[:160]}{_RESET}")
                if delta.get("content"):
                    content_parts.append(delta["content"])
                    print(f"{_GREEN}{delta['content']}{_RESET}", end="", flush=True)
    print()
    return "".join(content_parts), tool_names


def _forward_channel_turn(
    agent_id: str, provider: str, text: str, context: dict
) -> None:
    print(
        f"\n{_GREEN}▶ inbound{_RESET} provider={provider} room={context.get('room_id')} "
        f"sender={context.get('sender_name') or context.get('sender_id')} "
        f"mention={context.get('is_mention', True)} "
        f"attachments={len(context.get('attachments') or [])}"
    )
    body = {
        "model": agent_id,
        "messages": [{"role": "user", "content": text}],
        "stream": True,
        "channel_provider": provider,
        "channel_context": context,
    }
    content, tools = _stream_and_print(body)
    print(f"{_DIM}  turn done: content={len(content)}ch tools={tools}{_RESET}")


# ── Matrix (narramessenger) ─────────────────────────────────────────────


def listen_matrix(agent_id: str, max_messages: int = 0, forward_silent: bool = True) -> None:
    """Long-poll the agent's Matrix account (creds pulled the same way the
    platform does) and forward every inbound event as a managed channel turn.

    Plays the platform bridge faithfully enough for E2E: echo filter by our
    own mxid, dm/group via joined-member count, mention via m.mentions +
    mxid/localpart substring, media downloaded and landed through the
    gateway files/write endpoint (= ingestWorkspace), then the completions
    POST with the v1 channel_context."""
    binding = _find_binding("narramessenger", agent_id)
    hs = (binding.get("config") or {}).get("matrix_homeserver_url") or ""
    our_id = (binding.get("config") or {}).get("matrix_user_id") or ""
    token = (binding.get("credentials") or {}).get("matrix_access_token") or ""
    if not hs or not token:
        _die("binding lacks matrix homeserver/token (gateway transport binding? "
             "only connection_mode=matrix is platform-manageable)")
    localpart = our_id.split(":", 1)[0].lstrip("@") if our_id else ""
    print(f"listening on {hs} as {our_id or '(unknown mxid)'} — Ctrl-C to stop")

    mx = httpx.Client(
        base_url=hs.rstrip("/"),
        headers={"Authorization": f"Bearer {token}"},
        timeout=httpx.Timeout(45.0, read=45.0),
    )
    member_count: dict[str, int] = {}

    def room_members(room_id: str) -> int:
        if room_id not in member_count:
            try:
                r = mx.get(f"/_matrix/client/v3/rooms/{room_id}/joined_members")
                member_count[room_id] = len(r.json().get("joined", {})) if r.status_code == 200 else 0
            except httpx.HTTPError:
                member_count[room_id] = 0
        return member_count[room_id]

    def download_mxc(mxc: str) -> Optional[bytes]:
        if not mxc.startswith("mxc://"):
            return None
        server, _, media_id = mxc[len("mxc://"):].partition("/")
        for path in (
            f"/_matrix/client/v1/media/download/{server}/{media_id}",
            f"/_matrix/media/v3/download/{server}/{media_id}",
        ):
            try:
                r = mx.get(path)
                if r.status_code == 200:
                    return r.content
            except httpx.HTTPError:
                continue
        return None

    def join_invites(payload: dict) -> None:
        """Platform autoJoin equivalent — without it the agent account never
        enters a freshly-created group and @-mentions go nowhere."""
        for room_id in ((payload.get("rooms") or {}).get("invite") or {}):
            try:
                jr = mx.post(f"/_matrix/client/v3/join/{room_id}")
                if jr.status_code == 200:
                    print(f"{_GREEN}auto-joined invited room {room_id}{_RESET}")
                    member_count.pop(room_id, None)
                else:
                    print(f"{_RED}join {room_id} → HTTP {jr.status_code}{_RESET}")
            except httpx.HTTPError as e:
                print(f"{_RED}join {room_id} failed: {e}{_RESET}")

    since = ""
    r = mx.get("/_matrix/client/v3/sync", params={"timeout": 0})
    r.raise_for_status()
    baseline = r.json()
    since = baseline.get("next_batch", "")  # baseline: skip backlog...
    join_invites(baseline)  # ...but do accept invites already pending

    seen = 0
    while True:
        try:
            r = mx.get(
                "/_matrix/client/v3/sync",
                params={"timeout": 30000, "since": since},
            )
            r.raise_for_status()
        except httpx.HTTPError as e:
            print(f"{_RED}sync error: {e}; retrying in 3s{_RESET}")
            import time as _t
            _t.sleep(3)
            continue
        payload = r.json()
        since = payload.get("next_batch", since)
        join_invites(payload)
        rooms = (payload.get("rooms") or {}).get("join") or {}
        for room_id, room in rooms.items():
            for ev in ((room.get("timeline") or {}).get("events") or []):
                if ev.get("type") not in ("m.room.message", "ai.netmind.compound"):
                    continue
                sender = ev.get("sender", "")
                if not sender or sender == our_id:
                    continue  # echo filter
                content = ev.get("content") or {}
                body_text = str(content.get("body") or "")
                msgtype = content.get("msgtype", "m.text")

                is_dm = room_members(room_id) <= 2
                mentions = ((content.get("m.mentions") or {}).get("user_ids") or [])
                mentioned = (
                    is_dm
                    or (our_id and our_id in mentions)
                    or (our_id and our_id in body_text)
                    or (localpart and f"@{localpart}" in body_text)
                )

                # Media specs: NarraMessenger documents/images arrive as a
                # custom ai.netmind.compound msgtype (self-describing block
                # with the REAL user text + media_url); plain Matrix media
                # uses the standard msgtypes with content.url.
                media_specs: list[tuple[str, str, str]] = []
                compound = content.get("ai.netmind.compound")
                if msgtype == "ai.netmind.compound" and isinstance(compound, dict):
                    body_text = str(compound.get("text") or "")
                    if compound.get("media_url"):
                        media_specs.append((
                            str(compound["media_url"]),
                            str(compound.get("file_name") or "attachment.bin"),
                            str(compound.get("mime_type") or ""),
                        ))
                elif msgtype in ("m.image", "m.file", "m.audio", "m.video"):
                    media_specs.append((
                        str(content.get("url") or ""),
                        str(content.get("filename") or body_text or "media.bin"),
                        str((content.get("info") or {}).get("mimetype") or ""),
                    ))
                    if str(content.get("filename") or "") == body_text:
                        body_text = ""  # filename-as-body carries no caption

                attachments: list[dict] = []
                for mxc, name, mime in media_specs:
                    raw = download_mxc(mxc)
                    if raw is None:
                        print(f"{_RED}media download failed: {mxc}{_RESET}")
                        continue
                    rel = f"chat-attachments/local-e2e/{ev.get('event_id','evt').lstrip('$')[:12]}/{name}"
                    up = upload_workspace_file(agent_id, rel, raw)
                    attachments.append(
                        {"name": name, "mime": mime, "size": up.get("size", len(raw)),
                         "path": up.get("path", rel)}
                    )

                if not mentioned and not forward_silent:
                    print(f"{_DIM}(skipped non-mention group msg in {room_id}){_RESET}")
                    continue

                context: dict[str, Any] = {
                    "room_id": room_id,
                    "sender_id": sender,
                    "sender_name": sender.split(":", 1)[0].lstrip("@"),
                    "source_message_id": ev.get("event_id", ""),
                    "chat_type": "private" if is_dm else "group",
                    "is_mention": bool(mentioned),
                }
                if attachments:
                    context["attachments"] = attachments
                _forward_channel_turn(agent_id, "narramessenger", body_text, context)
                seen += 1
                if max_messages and seen >= max_messages:
                    print(f"{_DIM}reached --max-messages={max_messages}, exiting{_RESET}")
                    return


# ── WeChat (iLink) ──────────────────────────────────────────────────────


def listen_wechat(agent_id: str, max_messages: int = 0) -> None:
    """Long-poll the agent's WeChat bot via the iLink getupdates cursor and
    forward each text DM as a managed channel turn (reply_token carries the
    context_token wechat_send needs). Reuses the repo's WeChatSDKClient so
    the wire details (headers, cursor semantics, errcode handling) stay in
    one home — run via `uv run python`."""
    import asyncio as _asyncio

    from xyz_agent_context.module.wechat_module.wechat_sdk_client import (
        WeChatSDKClient,
        extract_text,
    )

    binding = _find_binding("wechat", agent_id)
    bot_token = (binding.get("credentials") or {}).get("bot_token") or ""
    base_url = (binding.get("credentials") or {}).get("base_url") or ""
    bot_wx_id = (binding.get("config") or {}).get("bot_wx_id") or ""
    if not bot_token:
        _die("wechat binding lacks bot_token")

    async def _loop() -> None:
        client = WeChatSDKClient(bot_token, base_url)
        cursor = ""
        seen = 0
        print(f"polling iLink getupdates (bot={bot_wx_id or '?'}) — Ctrl-C to stop")
        while True:
            try:
                data = await client.get_updates(cursor)
            except Exception as e:  # noqa: BLE001 — surface and retry
                print(f"{_RED}getupdates error: {type(e).__name__}: {e}; retry in 3s{_RESET}")
                await _asyncio.sleep(3)
                continue
            cursor = data.get("get_updates_buf", cursor)
            for msg in data.get("msgs") or []:
                text = extract_text(msg)
                from_user = msg.get("from_user_id") or ""
                if not text or not from_user or from_user == bot_wx_id:
                    continue
                context_token = msg.get("context_token", "") or ""
                context = {
                    "room_id": from_user,
                    "sender_id": from_user,
                    "sender_name": None,
                    "source_message_id": context_token or f"wx_{from_user}",
                    "chat_type": "private",
                    "is_mention": True,
                    "reply_token": context_token,
                }
                _forward_channel_turn(agent_id, "wechat", text, context)
                seen += 1
                if max_messages and seen >= max_messages:
                    print(f"{_DIM}reached --max-messages={max_messages}, exiting{_RESET}")
                    return
            await _asyncio.sleep(0.5)

    _asyncio.run(_loop())


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    p = argparse.ArgumentParser(description="Local Manyfold stand-in for trigger validation")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("pull-jobs")
    sub.add_parser("pull-channels")

    fj = sub.add_parser("fire-job")
    fj.add_argument("--agent", required=True)
    fj.add_argument("--job", required=True)

    si = sub.add_parser("send-im")
    si.add_argument("--agent", required=True)
    si.add_argument("--provider", default="lark")
    si.add_argument("--room", required=True)
    si.add_argument("--sender", required=True)
    si.add_argument("--sender-name", default="")
    si.add_argument("--text", required=True)

    lm = sub.add_parser("listen-matrix", help="live narramessenger bridge")
    lm.add_argument("--agent", required=True)
    lm.add_argument("--max-messages", type=int, default=0)
    lm.add_argument(
        "--drop-silent",
        action="store_true",
        help="drop non-mention group msgs instead of forwarding is_mention=false",
    )

    lw = sub.add_parser("listen-wechat", help="live iLink bridge")
    lw.add_argument("--agent", required=True)
    lw.add_argument("--max-messages", type=int, default=0)

    sn = sub.add_parser("serve-notify")
    sn.add_argument("--port", type=int, default=9099)

    args = p.parse_args()
    if args.cmd == "pull-jobs":
        pull_jobs()
    elif args.cmd == "pull-channels":
        pull_channels()
    elif args.cmd == "fire-job":
        fire_job(args.agent, args.job)
    elif args.cmd == "send-im":
        send_im(
            args.agent, args.provider, args.room, args.sender,
            args.sender_name or args.sender, args.text,
        )
    elif args.cmd == "listen-matrix":
        listen_matrix(
            args.agent,
            max_messages=args.max_messages,
            forward_silent=not args.drop_silent,
        )
    elif args.cmd == "listen-wechat":
        listen_wechat(args.agent, max_messages=args.max_messages)
    elif args.cmd == "serve-notify":
        serve_notify(args.port)


if __name__ == "__main__":
    main()

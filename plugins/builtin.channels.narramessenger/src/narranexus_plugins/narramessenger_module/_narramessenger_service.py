"""
@file_name: _narramessenger_service.py
@date: 2026-07-02
@description: Bind-flow driver for NarraMessenger — shared by the ``narra_bind``
MCP tool and the ``/api/narramessenger/bind`` backend route.

Drives the platform's **Direct Matrix** bind deterministically from a single
bind token (the public token inside a link like
``https://api.netmind.chat/<token>/setup-guide.md``):

  1. ``report-profile`` (the agent's name + bio) to advance the bind session.
  2. Re-fetch the setup-guide (now at ``waiting_connection`` / ``connected``)
     and regex out the runtime **bearer** token, the **Matrix access token**,
     the Matrix user id, and the homeserver URL.
  3. ``POST /bind-agent/runtime-ready?token=<bind_token>`` with
     ``{"platform": "nexus"}`` — completes the Direct Matrix bind, tags the
     agent's origin platform on the NarraMessenger side, and returns the
     initial ``bind_room_id`` where the owner will send the first test
     message.
  4. Upsert the credential row with ``connection_mode='matrix'`` and both
     tokens → :class:`MatrixTrigger`'s credential watcher auto-starts syncing.

Direct Matrix is the DEFAULT bind path per the NarraMessenger guide's
Runtime Selection Rule ("Do not ask the creator to choose Matrix vs
Gateway"). Gateway (Polling) is a fallback for environments that cannot
host a Matrix client — supported by NarraMessenger via
``POST /api/agent-gateway/connect`` on the same bearer, but not exercised
by this bind flow anymore. If the owner needs Gateway later, they can hit
that endpoint directly with the stored bearer.

Egress gate (cloud): the bind link is pasted by the user and the homeserver URL
is scraped from whatever that link's host serves, so BOTH are attacker-
influenced server-side fetch targets. Before any request the link's base URL
passes the shared ``assert_public_http_url_in_cloud`` gate, and the parsed homeserver
passes it before ``runtime-ready`` completes the remote bind or anything is
persisted. Redirects are never followed (a public host could 302 into the
cluster). Local/desktop skips the gate (single trusted user).

Caveat: extracting the bearer relies on regexing the rendered markdown, and the
bind state machine may require a human confirmation step on the NarraMessenger
side (``creator_confirmed``) before the bearer is revealed — in which case
``do_bind`` returns a clear "not connected yet" error rather than guessing.
"""

from __future__ import annotations

import re
from typing import Any, Optional, Tuple

import aiohttp
from loguru import logger

from narranexus.platform.utils.url_safety import assert_public_http_url_in_cloud

from ._narramessenger_credential_manager import (
    NarramessengerCredential,
    NarramessengerCredentialManager,
)

_DEFAULT_BASE = "https://api.netmind.chat"

# Upstream error codes we act on or may show the user verbatim. Anything else
# the host returns in ``error`` / ``message`` stays in the log: the bind host is
# user-chosen, so its free text must never be reflected back.
_KNOWN_ERROR_CODES = frozenset({"WRONG_STATE"})

# Origin tag sent to NarraMessenger on ``runtime-ready`` so their side can
# label which platform an agent was bound from. Their field defaults to
# ``"default"`` when the key is absent — which is exactly what every agent we
# bound looked like before this was added.
PLATFORM_TAG = "nexus"

# `Authorization: Bearer <token>` — the runtime bearer in the connected guide's
# Authentication section. First match wins (the Authentication block renders
# before generic example blocks).
_BEARER_RE = re.compile(r"Authorization:\s*Bearer\s+([A-Za-z0-9._\-]{8,})")
# A Matrix user id like @agent-e7726996:matrix.netmind.chat
_MATRIX_USER_RE = re.compile(r"(@[A-Za-z0-9_\-./=]+:[A-Za-z0-9_.\-]+)")
# `| Homeserver URL | `https://matrix...` |` — same-line, non-greedy to the URL
_HOMESERVER_RE = re.compile(r"Homeserver URL[^\n]*?(https?://[A-Za-z0-9_.\-:]+)")
# Matrix access token — Synapse-style tokens prefix ``syt_`` and are the only
# token flavour Matrix's HTTP API accepts (Bearer <token>). Constrained to
# a conservative alphabet + minimum length so a stray URL fragment or an
# unrelated syt-lookalike inside the guide's OpenClaw JSON5 example isn't
# picked up ahead of the canonical Matrix Connection Details table row.
_MATRIX_ACCESS_TOKEN_RE = re.compile(r"(syt_[A-Za-z0-9_\-]{20,})")
# `.../<token>/setup-guide...` inside a pasted link
_URL_TOKEN_RE = re.compile(
    r"https?://([A-Za-z0-9_.\-]+)/([A-Za-z0-9_\-]+)/setup-guide", re.IGNORECASE
)


def _parse_bind_command(bind_command: str) -> Tuple[str, str]:
    """Extract ``(token, backend_base_url)`` from a pasted command/link.

    Accepts a full URL (``https://host/<token>/setup-guide.md``) or a looser
    ``narra bind <token>`` / bare-token paste. Defaults the base URL to
    ``api.netmind.chat`` when only a bare token is given.
    """
    s = (bind_command or "").strip()
    m = _URL_TOKEN_RE.search(s)
    if m:
        host, token = m.group(1), m.group(2)
        return token, f"https://{host}"
    # Looser paste: take the last token-looking word that isn't noise.
    words = re.findall(r"[A-Za-z0-9_\-]{4,64}", s)
    skip = {"narra", "bind", "agent", "agents", "setup", "guide", "https",
            "http", "com", "net", "chat", "md", "the", "token", "link"}
    cand = [w for w in words if w.lower() not in skip]
    return (cand[-1] if cand else ""), _DEFAULT_BASE


async def _agent_profile(db, agent_id: str) -> Tuple[str, str]:
    """Look up the agent's display name + a short bio for ``report-profile``."""
    agent = None
    try:
        agent = await db.get_one("agents", {"agent_id": agent_id})
    except Exception:  # noqa: BLE001
        agent = None
    name = ""
    bio = ""
    if agent:
        name = str(agent.get("name") or agent.get("agent_name") or "")
        bio = str(agent.get("description") or agent.get("bio") or "")
    name = (name.strip() or "NarraNexus Agent")[:30]
    bio = (bio.strip() or "A NarraNexus agent reachable on NarraMessenger.")[:200]
    return name, bio


def _parse_setup_guide(md: str) -> dict[str, str]:
    """Pull runtime credentials out of the rendered setup guide.

    Extracted fields (all optional — absence means "not yet revealed by
    the bind state machine", not "failed"):

    - ``bearer``: Narra agent secret token, used for
      ``/bind-agent/runtime-ready`` and every subsequent
      ``/api/agent-runtime/*`` call (including the authorize-event gate).
    - ``matrix_access_token``: Matrix homeserver access token (``syt_...``),
      used ONLY for Matrix HTTP API calls. Never mix with the Narra
      bearer — the guide is explicit that Matrix rejects the Narra bearer
      with ``M_UNKNOWN_TOKEN``.
    - ``homeserver``: Matrix homeserver URL from the Matrix Connection
      Details table.
    - ``matrix_user_id``: The agent's own MXID.
    """
    out: dict[str, str] = {}
    if not md:
        return out
    bm = _BEARER_RE.search(md)
    if bm:
        out["bearer"] = bm.group(1)
    hm = _HOMESERVER_RE.search(md)
    if hm:
        out["homeserver"] = hm.group(1)
    um = _MATRIX_USER_RE.search(md)
    if um:
        out["matrix_user_id"] = um.group(1)
    am = _MATRIX_ACCESS_TOKEN_RE.search(md)
    if am:
        out["matrix_access_token"] = am.group(1)
    return out


async def _is_allowed_target(url: str) -> bool:
    """Cloud-only SSRF egress gate for a bind-time fetch target.

    False on ANY failure to decide (unsafe, unresolvable, malformed) — fail
    closed. The reason goes to the log only; callers return a generic message
    so the gate never becomes an oracle for the internal network.
    """
    try:
        await assert_public_http_url_in_cloud(url)
    except Exception as e:  # noqa: BLE001 — any decision failure fails closed
        logger.warning(f"[narramessenger] bind target refused by egress gate ({type(e).__name__}): {e}")
        return False
    return True


async def _get_text(session: aiohttp.ClientSession, url: str) -> str:
    async with session.get(url, allow_redirects=False) as resp:
        if 200 <= resp.status < 300:
            return await resp.text()
        if 300 <= resp.status < 400:
            # Redirects are refused, and the caller maps "" to "token not found";
            # log the status so an upstream redirect is not misread as a bad
            # token. Never log or surface Location (upstream-controlled).
            logger.warning(f"[narramessenger] bind GET refused a {resp.status} redirect")
        return ""


async def _post_json(
    session: aiohttp.ClientSession, url: str, body: Optional[dict], bearer: str = ""
) -> dict:
    headers = {"Content-Type": "application/json"}
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"
    kwargs: dict[str, Any] = {"headers": headers, "allow_redirects": False}
    if body is not None:
        kwargs["json"] = body
    async with session.post(url, **kwargs) as resp:
        try:
            data = await resp.json()
        except Exception:  # noqa: BLE001
            data = {}
        if 200 <= resp.status < 300:
            return {"ok": True, "data": data if isinstance(data, dict) else {}}
        # Raw upstream code/text, for control flow only (WRONG_STATE check).
        # Never shown as-is — `_step_failure` builds the user-facing text.
        err = ""
        if isinstance(data, dict):
            err = str(data.get("error") or data.get("message") or "")
        return {"ok": False, "status": resp.status, "error": err}


def _step_failure(step: str, result: dict) -> str:
    """User-facing failure for a bind step: status code + whitelisted code only.

    The single place a step result becomes user text; the raw upstream string
    goes to the log unless it is a known code.
    """
    raw = str(result.get("error") or "")
    code = raw if raw in _KNOWN_ERROR_CODES else ""
    if raw and not code:
        logger.info(f"[narramessenger] {step} upstream error: {raw[:200]}")
    status = result.get("status")
    detail = f"HTTP {status}" if status else "no response"
    return f"{step} failed ({detail}{', ' + code if code else ''})"


async def do_bind(db, agent_id: str, bind_command: str) -> dict[str, Any]:
    """Drive the Gateway bind from a pasted bind command/link.

    Returns ``{"success": True, "data": {...}}`` or
    ``{"success": False, "error": <message>}``. See module docstring.
    """
    token, base_url = _parse_bind_command(bind_command)
    if not token:
        return {
            "success": False,
            "error": "Could not find a bind token in the pasted text. Paste the "
            "full bind link shown under My Agents → Bind Agents.",
        }
    name, bio = await _agent_profile(db, agent_id)
    base = base_url.rstrip("/")
    if not await _is_allowed_target(base):
        return {
            "success": False,
            "error": "The bind link does not point at a public NarraMessenger "
            "host. Re-copy the bind link from My Agents → Bind Agents.",
        }

    try:
        async with aiohttp.ClientSession(
            trust_env=True, timeout=aiohttp.ClientTimeout(total=25)
        ) as session:
            guide = await _get_text(session, f"{base}/{token}/setup-guide.md")
            if not guide:
                return {
                    "success": False,
                    "error": "Bind token not found or expired (could not fetch the "
                    "setup guide). Re-copy the bind link from My Agents → Bind Agents.",
                }
            parsed = _parse_setup_guide(guide)

            # Advance the session if the bearer hasn't been revealed yet.
            if not parsed.get("bearer"):
                rp = await _post_json(
                    session,
                    f"{base}/bind-agent/report-profile?token={token}",
                    {"name": name, "bio": bio},
                )
                # WRONG_STATE just means we're already past the profile step.
                if not rp.get("ok") and rp.get("error") not in ("WRONG_STATE", ""):
                    return {
                        "success": False,
                        "error": _step_failure("report-profile", rp),
                    }
                guide = await _get_text(session, f"{base}/{token}/setup-guide.md")
                parsed = _parse_setup_guide(guide)

            bearer = parsed.get("bearer", "")
            if not bearer:
                return {
                    "success": False,
                    "error": "Profile reported, but the runtime credential isn't "
                    "revealed yet — the bind session may still need confirmation on "
                    "the NarraMessenger side. Confirm it there, then paste the link "
                    "again.",
                }

            # The homeserver is scraped from content the link's host served;
            # gate it BEFORE runtime-ready so a refused bind is never completed
            # on the NarraMessenger side, and nothing is persisted. A missing
            # homeserver is left to the existing downstream handling.
            homeserver_url = parsed.get("homeserver", "")
            if homeserver_url and not await _is_allowed_target(homeserver_url):
                return {
                    "success": False,
                    "error": "The setup guide names a Matrix homeserver that is "
                    "not a public host; the bind was not completed.",
                }

            # Direct Matrix bind. This is a bind-token URL (query param
            # ``?token=<bind_token>``), NOT a bearer-auth endpoint —
            # confusingly the guide has both ``/bind-agent/runtime-ready``
            # (this call, initial bind) and ``/api/agent-runtime/direct-ready``
            # (bearer-authenticated, used only when upgrading FROM Gateway
            # BACK to Direct after an initial bind). Do not mix them.
            # ``platform`` tags the agent's origin on the NarraMessenger side.
            # It rides on runtime-ready (not report-profile) because this is
            # the one call EVERY bind makes — report-profile is skipped
            # whenever the guide already revealed the bearer — and because
            # runtime-ready is the step that means "this agent is usable",
            # which is what their label describes.
            ready = await _post_json(
                session,
                f"{base}/bind-agent/runtime-ready?token={token}",
                {"platform": PLATFORM_TAG},
            )
            if not ready.get("ok"):
                return {
                    "success": False,
                    "error": _step_failure("runtime-ready", ready),
                }
            rdata = ready.get("data") or {}
    except aiohttp.ClientError as e:
        return {
            "success": False,
            "error": f"network error reaching NarraMessenger: {type(e).__name__}",
        }

    matrix_user_id = rdata.get("matrixUserId") or parsed.get("matrix_user_id", "")
    principal_id = rdata.get("principalId", "") or ""
    bind_room_id = rdata.get("roomId", "") or ""
    homeserver = parsed.get("homeserver", "")
    matrix_access_token = parsed.get("matrix_access_token", "")
    if not matrix_access_token:
        # The bearer / user id / homeserver arrived but the Matrix
        # access token did not — the guide should have revealed it
        # together with the rest. Treat this as a hard bind error
        # rather than silently persisting a Matrix-mode row that the
        # trigger cannot use (MatrixTrigger.connect raises ValueError
        # on missing access_token, which the base then treats as a
        # permanent failure and disables the credential — worse UX
        # than telling the owner to retry the bind).
        return {
            "success": False,
            "error": "Matrix access token missing from the setup guide. Re-copy "
            "the bind link and try again; if the problem persists, contact the "
            "NarraMessenger team.",
        }

    mgr = NarramessengerCredentialManager(db)
    await mgr.upsert(
        NarramessengerCredential(
            agent_id=agent_id,
            bearer_token=bearer,
            backend_base_url=base,
            matrix_homeserver_url=homeserver,
            matrix_user_id=matrix_user_id,
            matrix_access_token=matrix_access_token,
            nexus_principal_id=principal_id,
            nexus_profile_id=rdata.get("profileId", "") or "",
            bind_room_id=bind_room_id,
            connection_mode="matrix",
            enabled=True,
        )
    )
    logger.info(
        f"[narramessenger:{agent_id}] bound via Direct Matrix "
        f"(matrix_user_id={matrix_user_id}, principal={principal_id}, "
        f"bind_room={bind_room_id})"
    )
    return {
        "success": True,
        "data": {
            "matrix_user_id": matrix_user_id,
            "principal_id": principal_id,
            "room_id": bind_room_id,
            "connection_mode": "gateway",
        },
    }


async def do_unbind(db, agent_id: str) -> dict[str, Any]:
    """Remove the agent's NarraMessenger credential (stops the trigger)."""
    mgr = NarramessengerCredentialManager(db)
    ok = await mgr.unbind(agent_id)
    return {"success": True, "unbound": ok}

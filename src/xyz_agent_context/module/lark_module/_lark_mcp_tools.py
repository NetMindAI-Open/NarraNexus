"""
@file_name: _lark_mcp_tools.py
@date: 2026-04-22
@description: Lark MCP tools — seven-tool set driving the three-click
authorization flow.

Tools exposed (7 total):
  - lark_cli(agent_id, command)                    — Run any lark-cli command
  - lark_setup(agent_id, brand, email)             — Click 1: create NEW app
  - lark_bind(agent_id, app_id, secret, brand, ...)— Bind EXISTING app
  - lark_permission_advance(agent_id, event="")    — Drive Click 2 & Click 3
  - lark_enable_receive(agent_id, app_secret)      — Enable real-time auto-reply
  - lark_status(agent_id)                          — Health + Matrix self-heal
  - lark_skill(agent_id, name, path="SKILL.md")    — Read any skill file

See spec: reference/self_notebook/specs/2026-04-22-lark-three-click-auth-design.md

Four tools were removed in the C-mini redesign; all four funnel into
`lark_permission_advance`:
  - lark_configure_permissions  → event=""
  - lark_auth                   → event="" (incremental scope补 via lark_cli)
  - lark_auth_complete          → event="user_authorized"
  - lark_mark_console_done      → event="availability_ok"
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from loguru import logger

from xyz_agent_context.module.base import XYZBaseModule
from ._lark_credential_manager import (
    LarkCredentialManager,
    AUTH_STATUS_BOT_READY,
    AUTH_STATUS_USER_LOGGED_IN,
)
from .lark_cli_client import LarkCLIClient
from ._lark_command_security import validate_command, sanitize_command
from ._lark_workspace import build_profile_name, ensure_workspace, get_home_env

# Shared CLI client instance (stateless)
_cli = LarkCLIClient()

# Max time we'll wait for the user to finish the browser-side setup flow
# before we kill the subprocess and delete the pending DB row.
_SETUP_TIMEOUT_SECONDS = 15 * 60


async def _get_credential(agent_id: str):
    """Load credential from DB via MCP-level database client."""
    db = await XYZBaseModule.get_mcp_db_client()
    mgr = LarkCredentialManager(db)
    return await mgr.get_credential(agent_id)


async def _get_agent_name(agent_id: str) -> str:
    """Look up the agent's human-readable name; fall back to agent_id."""
    db = await XYZBaseModule.get_mcp_db_client()
    row = await db.get_one("agents", {"agent_id": agent_id})
    return (row or {}).get("agent_name", "") or agent_id


def _command_uses_user_identity(args: list[str]) -> bool:
    """True iff the lark-cli args explicitly request `--as user`.

    Only EXPLICIT user identity counts as an observable success signal.
    `--as auto` / no flag / `--as bot` don't count — those may route through
    the bot token even for commands that happen to support user id.
    """
    for i, tok in enumerate(args):
        if tok == "--as" and i + 1 < len(args) and args[i + 1] == "user":
            return True
    return False


def _dev_console_url(brand: str, app_id: str) -> str:
    """Build the direct URL to the app's page in the Lark/Feishu dev console.

    Users get the plain App Secret from "Credentials & Basic Info" on this page.
    """
    if not app_id or app_id == "pending_setup":
        return ""
    if brand == "feishu":
        return f"https://open.feishu.cn/app/{app_id}"
    return f"https://open.larksuite.com/app/{app_id}"


# ───────────────────────────────────────────────────────────────────────────
# User-facing messages returned by lark_permission_advance
# Kept as constants so wording stays identical across agents / turns.
# Agent sends `data.user_facing_message` verbatim.
# ───────────────────────────────────────────────────────────────────────────

_MSG_CLICK2_GENERATED = (
    "👉 这是 **Click 2 / 共 3 次**：点击此链接会向你们企业管理员提交权限申请。"
    "**点击 ≠ 授权完成**，管理员批准后请回来告诉我。"
    "如果你本人就是管理员，请去 Lark Admin Console → 应用审批 批准。"
)
_MSG_CLICK3_GENERATED = (
    "👉 这是 **Click 3 / 共 3 次**：管理员已批准，现在需要你本人授权。"
    "点击此链接后告诉我「我授权了」。"
)
_MSG_AUTHORIZED_OK = (
    "✅ 授权完成。接下来我需要你的 App Secret 来开启实时消息接收 —— "
    "请去 dev console 的 'Credentials & Basic Info' 复制后粘给我。"
)
_MSG_AUTHORIZED_PENDING = (
    "Lark 还没收到你的 Click 3 点击。请确认刚才的授权链接是否点了。"
    "点了告诉我，我再试一次。"
)
_MSG_AUTHORIZED_EXPIRED = (
    "刚才那个授权链接过期了，新的给你：{fresh_url}。点完告诉我「我授权了」。"
)
_MSG_AVAILABILITY_OK = "✅ 可见度已记录。"
_MSG_ALREADY_COMPLETED = (
    "已完成三次点击授权，Matrix 第 2 行应显示 ✅ completed。无需重复操作。"
)


# Recommended bot scopes the `--recommend` OAuth flow will grant. Kept here
# for diagnostics / documentation; the actual grant list is decided by Lark
# based on what the app registers.
_RECOMMENDED_BOT_SCOPES = [
    "im:message", "im:message:send_as_bot", "im:resource",
    "im:chat", "im:chat:readonly",
    "contact:user.base:readonly", "contact:user.email:readonly",
    "contact:contact:readonly",
    "docs:document", "docs:document:readonly",
    "calendar:calendar", "calendar:calendar:readonly",
    "drive:drive", "drive:drive:readonly",
    "sheets:spreadsheet", "sheets:spreadsheet:readonly",
    "wiki:wiki:readonly",
    "task:task",
]


async def _finalize_setup(
    agent_id: str,
    proc,
    workspace,
    profile_name: str,
) -> None:
    """Wait for `config init --new` to finish, then write the real app_id +
    keychain reference back to the DB. On timeout or error, clean up.

    Launched as a background task from lark_setup so the Agent's tool call
    can return the auth URL immediately while the user completes the
    browser flow.
    """
    import json as _json
    import pathlib

    try:
        # Step 1: wait for CLI to exit (user finishes browser-side setup)
        try:
            await asyncio.wait_for(proc.wait(), timeout=_SETUP_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            logger.warning(
                f"lark_setup: {agent_id} timed out waiting for browser "
                f"authorization ({_SETUP_TIMEOUT_SECONDS}s). Cleaning up."
            )
            try:
                proc.kill()
                await proc.wait()
            except (ProcessLookupError, OSError):
                pass
            db = await XYZBaseModule.get_mcp_db_client()
            await LarkCredentialManager(db).delete_credential(agent_id)
            return

        if proc.returncode != 0:
            logger.error(
                f"lark_setup: {agent_id} CLI exited with code {proc.returncode}. "
                f"Cleaning up pending row."
            )
            db = await XYZBaseModule.get_mcp_db_client()
            await LarkCredentialManager(db).delete_credential(agent_id)
            return

        # Step 2: read the CLI-written config.json from the isolated workspace
        config_path = pathlib.Path(workspace) / ".lark-cli" / "config.json"
        if not config_path.is_file():
            logger.error(f"lark_setup: {agent_id} CLI config not found at {config_path}")
            db = await XYZBaseModule.get_mcp_db_client()
            await LarkCredentialManager(db).delete_credential(agent_id)
            return

        try:
            config = _json.loads(config_path.read_text())
        except _json.JSONDecodeError as e:
            logger.exception(f"lark_setup: {agent_id} malformed config.json: {e}")
            db = await XYZBaseModule.get_mcp_db_client()
            await LarkCredentialManager(db).delete_credential(agent_id)
            return

        # Step 3: find our profile (matched by name) in the apps list
        apps = config.get("apps", [])
        our_app = next((a for a in apps if a.get("name") == profile_name), None)
        if not our_app and len(apps) == 1:
            our_app = apps[0]
        if not our_app:
            logger.error(
                f"lark_setup: {agent_id} profile '{profile_name}' not in "
                f"config.json (found {[a.get('name') for a in apps]})"
            )
            db = await XYZBaseModule.get_mcp_db_client()
            await LarkCredentialManager(db).delete_credential(agent_id)
            return

        app_id = our_app.get("appId", "")
        app_secret_ref = (our_app.get("appSecret") or {}).get("id", "")
        if not app_id or not app_secret_ref:
            logger.error(
                f"lark_setup: {agent_id} config.json missing appId or "
                f"appSecret.id (app_id={app_id!r}, ref={app_secret_ref!r})"
            )
            db = await XYZBaseModule.get_mcp_db_client()
            await LarkCredentialManager(db).delete_credential(agent_id)
            return

        # Step 4: flip the DB row to ready
        db = await XYZBaseModule.get_mcp_db_client()
        mgr = LarkCredentialManager(db)
        await mgr.update_app_credentials(
            agent_id=agent_id,
            app_id=app_id,
            app_secret_ref=app_secret_ref,
            is_active=True,
            auth_status=AUTH_STATUS_BOT_READY,
        )
        logger.info(
            f"lark_setup: {agent_id} finalized — app_id={app_id}, "
            f"profile={profile_name}"
        )

        # Step 5: best-effort bot_name + owner resolution (non-blocking failure)
        try:
            bot_info = await _cli._run_with_agent_id(
                ["contact", "+get-user", "--as", "bot"], agent_id
            )
            if bot_info.get("success"):
                data = bot_info.get("data", {})
                name = data.get("name") or data.get("en_name") or ""
                if name:
                    await mgr.update_bot_name(agent_id, name)
        except Exception as e:
            logger.warning(f"lark_setup: {agent_id} bot_name lookup failed: {e}")

    except Exception as e:
        logger.exception(f"lark_setup: {agent_id} _finalize_setup unexpected error: {e}")
        try:
            db = await XYZBaseModule.get_mcp_db_client()
            await LarkCredentialManager(db).delete_credential(agent_id)
        except Exception:
            pass


# ───────────────────────────────────────────────────────────────────────────
# lark_permission_advance — per-event handlers
# ───────────────────────────────────────────────────────────────────────────

async def _advance_start(agent_id: str, cred) -> dict:
    """event="" — Generate Click 2 URL (submit scope request to admin)."""
    ps = cred.permission_state or {}

    # Idempotent: if a Click 2 URL already exists, return it rather than
    # minting a new device_code (avoids invalidating a URL the user may
    # already have open).
    existing_url = ps.get("admin_request_url")
    if existing_url:
        return {
            "success": True,
            "data": {
                "url": existing_url,
                "click_label": "Click 2",
                "user_facing_message": _MSG_CLICK2_GENERATED,
                "stage_after": cred.current_click_stage(),
                "fresh_url": None,
            },
        }

    result = await _cli._run_with_agent_id(
        ["auth", "login", "--domain", "all", "--recommend", "--json", "--no-wait"],
        agent_id,
        timeout=60.0,
    )
    if not result.get("success"):
        return {
            "success": False,
            "error": f"Failed to generate Click 2 URL: {result.get('error', 'unknown')}",
        }

    data = result.get("data", {})
    url = data.get("verification_url", "")
    device_code = data.get("device_code", "")
    if not url or not device_code:
        return {"success": False, "error": "CLI did not return URL/device_code."}

    now = datetime.now(timezone.utc).isoformat()
    db = await XYZBaseModule.get_mcp_db_client()
    await LarkCredentialManager(db).patch_permission_state(agent_id, {
        "admin_request_url": url,
        "admin_request_device_code": device_code,
        "admin_request_generated_at": now,
    })

    return {
        "success": True,
        "data": {
            "url": url,
            "click_label": "Click 2",
            "user_facing_message": _MSG_CLICK2_GENERATED,
            "stage_after": "waiting_admin",
            "fresh_url": None,
        },
    }


async def _advance_admin_approved(agent_id: str, cred) -> dict:
    """event="admin_approved" — Mint fresh device_code & return Click 3 URL."""
    ps = cred.permission_state or {}
    if not ps.get("admin_request_url"):
        return {
            "success": False,
            "error": (
                "No admin request on file. Call `lark_permission_advance("
                "agent_id)` with no event first to generate Click 2."
            ),
        }

    # The Click 2 device_code was bound to the submit-to-admin phase and
    # is useless for minting a token. We must run auth login again to get
    # a fresh pair, regardless of whether the first one is expired.
    result = await _cli._run_with_agent_id(
        ["auth", "login", "--domain", "all", "--recommend", "--json", "--no-wait"],
        agent_id,
        timeout=60.0,
    )
    if not result.get("success"):
        return {
            "success": False,
            "error": f"Failed to generate Click 3 URL: {result.get('error', 'unknown')}",
        }

    data = result.get("data", {})
    url = data.get("verification_url", "")
    device_code = data.get("device_code", "")
    if not url or not device_code:
        return {"success": False, "error": "CLI did not return URL/device_code."}

    now = datetime.now(timezone.utc).isoformat()
    db = await XYZBaseModule.get_mcp_db_client()
    await LarkCredentialManager(db).patch_permission_state(agent_id, {
        "admin_approved_at": now,
        "user_authz_url": url,
        "user_authz_device_code": device_code,
        "user_authz_generated_at": now,
    })

    return {
        "success": True,
        "data": {
            "url": url,
            "click_label": "Click 3",
            "user_facing_message": _MSG_CLICK3_GENERATED,
            "stage_after": "waiting_user_click",
            "fresh_url": None,
        },
    }


async def _advance_user_authorized(agent_id: str, cred) -> dict:
    """event="user_authorized" — Exchange Click 3 device_code for a token."""
    ps = cred.permission_state or {}
    device_code = ps.get("user_authz_device_code")
    if not device_code:
        return {
            "success": False,
            "error": (
                "No Click 3 device_code on file. Call `lark_permission_advance("
                "agent_id, event='admin_approved')` first to mint one."
            ),
        }

    result = await _cli._run_with_agent_id(
        ["auth", "login", "--device-code", device_code, "--json"],
        agent_id,
        timeout=60.0,
    )

    db = await XYZBaseModule.get_mcp_db_client()
    mgr = LarkCredentialManager(db)

    if result.get("success"):
        now = datetime.now(timezone.utc).isoformat()
        data = result.get("data", {})
        granted = (
            data.get("scopes")
            or data.get("granted_scopes")
            or data.get("user", {}).get("scopes", [])
            or []
        )
        await mgr.update_auth_status(agent_id, AUTH_STATUS_USER_LOGGED_IN)
        await mgr.patch_permission_state(agent_id, {
            "user_oauth_completed_at": now,
            "user_scopes_granted": list(granted) if isinstance(granted, (list, tuple)) else [],
            "user_authz_url": None,
            "user_authz_device_code": None,
            "bot_scopes_confirmed": True,
            "console_setup_done_at": now,
        })
        return {
            "success": True,
            "data": {
                "url": None,
                "click_label": None,
                "user_facing_message": _MSG_AUTHORIZED_OK,
                "stage_after": "completed",
                "fresh_url": None,
            },
        }

    # --- Failure paths ---
    err_msg = (result.get("error") or "").lower()

    # authorization_pending / slow_down: user hasn't clicked yet, OR clicked
    # recently but Lark hasn't propagated. Do NOT auto-retry — leave the
    # device_code intact so the user can click and we can re-poll.
    if "authorization_pending" in err_msg or "slow_down" in err_msg:
        return {
            "success": False,
            "error": "Lark still reports authorization_pending.",
            "data": {
                "url": None,
                "click_label": None,
                "user_facing_message": _MSG_AUTHORIZED_PENDING,
                "stage_after": cred.current_click_stage(),
                "fresh_url": None,
            },
        }

    # expired / consumed / invalid_grant — regenerate a fresh Click 3 URL
    is_stale = any(kw in err_msg for kw in (
        "expired",
        "invalid_grant",
        "is invalid",
        "restart the device",
    ))
    if is_stale:
        regen = await _cli._run_with_agent_id(
            ["auth", "login", "--domain", "all", "--recommend", "--json", "--no-wait"],
            agent_id,
            timeout=60.0,
        )
        if regen.get("success"):
            regen_data = regen.get("data", {})
            new_url = regen_data.get("verification_url", "")
            new_code = regen_data.get("device_code", "")
            if new_url and new_code:
                now = datetime.now(timezone.utc).isoformat()
                await mgr.patch_permission_state(agent_id, {
                    "user_authz_url": new_url,
                    "user_authz_device_code": new_code,
                    "user_authz_generated_at": now,
                })
                return {
                    "success": False,
                    "error": "Previous Click 3 URL expired; fresh URL generated.",
                    "data": {
                        "url": new_url,
                        "click_label": "Click 3",
                        "user_facing_message": _MSG_AUTHORIZED_EXPIRED.format(fresh_url=new_url),
                        "stage_after": "waiting_user_click",
                        "fresh_url": new_url,
                    },
                }
        return {
            "success": False,
            "error": "Click 3 URL expired and regeneration also failed.",
        }

    # Other failures — propagate as-is
    return {"success": False, "error": result.get("error", "Unknown failure")}


async def _advance_availability_ok(agent_id: str, cred) -> dict:
    """event="availability_ok" — Mark optional app visibility flag."""
    db = await XYZBaseModule.get_mcp_db_client()
    await LarkCredentialManager(db).patch_permission_state(agent_id, {
        "availability_confirmed": True,
    })
    return {
        "success": True,
        "data": {
            "url": None,
            "click_label": None,
            "user_facing_message": _MSG_AVAILABILITY_OK,
            "stage_after": cred.current_click_stage(),
            "fresh_url": None,
        },
    }


# ───────────────────────────────────────────────────────────────────────────
# Tool registration
# ───────────────────────────────────────────────────────────────────────────

def register_lark_mcp_tools(mcp: Any) -> None:
    """Register Lark MCP tools on the given FastMCP server."""

    @mcp.tool()
    async def lark_cli(agent_id: str, command: str) -> dict:
        """Run any lark-cli command with per-agent profile isolation.

        Call this for any Lark data operation: send/search messages, read/create
        docs, query calendar, etc.

        First-use tip: for a domain you haven't used in this session, call
        `lark_skill(agent_id, <domain>)` first (e.g. "lark-im",
        "lark-calendar") to load its SKILL.md. That tool is also the ONLY
        way to read any Lark skill file — follow the `path=` hints it
        emits next to each link. Do NOT try to `Read` skill files
        directly; they live in the MCP container, not your workspace.

        Identity — **ALWAYS specify `--as bot` or `--as user` explicitly**:
          - `--as bot`: the bot uses its own identity. Default for routine
            messaging (`im +messages-send`), creating docs, and any app-level
            write. **Prefer this for day-to-day chat.**
          - `--as user`: the bot impersonates the owner. Use ONLY for reading
            owner-private data (own calendar / docs / mail) AND only when
            `is_owner_interacting=True`.
          - Do NOT omit `--as`. lark-cli defaults to `auto`, which silently
            picks `user` whenever a user token exists — that triggers
            `missing_scope: im:message:send_as_user` on send commands
            (we do not grant that scope by default, and do not plan to).

        Do NOT add `--profile` or `--format json` — both are injected /
        forbidden by shortcut commands.

        On failure:
          - "missing_scope: X": recovery depends on whether X is a
            bot-identity or user-identity scope, and the system prompt's
            "Incremental scope authorization" section covers the traps
            (don't re-mint while a URL is in flight; mint and poll
            across two turns; bot scopes go to the developer console,
            not `auth login`). For the full contract load
            `lark_skill(agent_id, "lark-shared", "SKILL.md")`.
          - "authorization_pending" from `auth login --device-code`:
            the user hasn't clicked the verification URL yet. Do NOT
            mint a new one; remind them about the URL already sent.
          - "Command blocked: `auth login` without --scope ...": bare
            `auth login` is reserved for `lark_permission_advance`.
            Re-issue with `--scope <missing>` instead.
          - "Command blocked" (other): hit the lifecycle whitelist —
            use the dedicated tool (lark_setup / lark_permission_advance
            / ...).
          - "No Lark bot bound": run lark_setup or lark_bind first.
        """
        cred = await _get_credential(agent_id)
        if not cred:
            return {"success": False, "error": "No Lark bot bound. Use lark_setup or lark_bind first."}

        allowed, reason = validate_command(command)
        if not allowed:
            return {"success": False, "error": f"Command blocked: {reason}"}

        try:
            args = sanitize_command(command)
        except ValueError as e:
            return {"success": False, "error": str(e)}

        result = await _cli._run_with_agent_id(args, agent_id)

        # Self-heal: if a `--as user` call succeeded, that's direct proof the
        # user OAuth token is valid AND the app has the scope published.
        # Flip the three-click flow forward even if event="user_authorized"
        # was never explicitly called.
        try:
            if result.get("success") and _command_uses_user_identity(args):
                ps = cred.permission_state or {}
                if not ps.get("user_oauth_completed_at"):
                    now = datetime.now(timezone.utc).isoformat()
                    db = await XYZBaseModule.get_mcp_db_client()
                    await LarkCredentialManager(db).patch_permission_state(agent_id, {
                        "user_oauth_completed_at": now,
                        "user_authz_url": None,
                        "user_authz_device_code": None,
                        "bot_scopes_confirmed": True,
                        "console_setup_done_at": ps.get("console_setup_done_at") or now,
                    })
                    await LarkCredentialManager(db).update_auth_status(
                        agent_id, AUTH_STATUS_USER_LOGGED_IN
                    )
                    logger.info(
                        f"lark_cli: self-healed OAuth state for {agent_id} "
                        f"(observed successful --as user call)"
                    )
        except Exception as e:
            logger.debug(f"lark_cli self-heal skipped: {e}")

        return result

    @mcp.tool()
    async def lark_setup(agent_id: str, brand: str, owner_email: str = "") -> dict:
        """Create a NEW Lark/Feishu app and bind it as this agent's bot
        (Click 1 of 3). Agent-assisted flow — subprocesses
        `lark-cli config init --new` and extracts the auth URL.

        State: agent has NO credential row. If the user is pasting values
        that look like `cli_xxx` + a long secret, they have an existing
        app → use `lark_bind` instead.

        BEFORE CALLING — ask the user, do NOT default:
          - brand: "feishu" (飞书 · 中国大陆) vs "lark" (International).
            Wrong brand makes WebSocket subscribe fail silently (err 1000040351).
          - owner_email: used to resolve "me" in Lark org directory.

        RETURN data.auth_url → send to user verbatim:
          "点此链接完成 app 创建，完成后告诉我。"

        This is Click 1 only. Two more clicks follow (permission request +
        user authorization), driven by `lark_permission_advance`. After the
        user confirms Click 1 and the Matrix shows row 1 = ✅, the next tool
        is determined by Matrix row 2 stage, not by user's words.
        """
        import re

        if brand not in ("feishu", "lark"):
            return {"success": False, "error": "brand must be 'feishu' or 'lark'."}

        existing = await _get_credential(agent_id)
        if existing:
            return {
                "success": False,
                "error": (
                    "Agent already has a Lark bot (or a pending setup). "
                    "Unbind first via frontend LarkConfig or DELETE /api/lark/unbind."
                ),
            }

        agent_name = await _get_agent_name(agent_id)
        profile_name = build_profile_name(agent_name, agent_id)

        workspace = ensure_workspace(agent_id)
        env = get_home_env(agent_id)

        try:
            proc = await asyncio.create_subprocess_exec(
                "lark-cli", "config", "init", "--new",
                "--brand", brand, "--name", profile_name,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env=env,
            )
        except FileNotFoundError:
            return {"success": False, "error": "lark-cli not found. Install: npm install -g @larksuite/cli"}

        try:
            collected = b""

            async def _read_until_url():
                nonlocal collected
                while True:
                    chunk = await proc.stdout.read(4096)
                    if not chunk:
                        return
                    collected += chunk
                    if b"http" in collected.lower():
                        try:
                            extra = await asyncio.wait_for(
                                proc.stdout.read(4096), timeout=2.0
                            )
                            if extra:
                                collected += extra
                        except asyncio.TimeoutError:
                            pass
                        return

            try:
                await asyncio.wait_for(_read_until_url(), timeout=30.0)
            except asyncio.TimeoutError:
                try:
                    proc.kill()
                    await proc.wait()
                except (ProcessLookupError, OSError):
                    pass
                return {
                    "success": False,
                    "error": "Timed out waiting for setup URL from CLI.",
                    "raw_output": collected.decode(errors="replace")[:2000],
                }

            output_text = collected.decode(errors="replace")
            urls = re.findall(r"https?://\S+", output_text)
            if not urls:
                try:
                    proc.kill()
                    await proc.wait()
                except (ProcessLookupError, OSError):
                    pass
                return {
                    "success": False,
                    "error": "Could not extract setup URL from CLI output.",
                    "raw_output": output_text[:2000],
                }
            auth_url = urls[0]

            from ._lark_credential_manager import LarkCredential

            db = await XYZBaseModule.get_mcp_db_client()
            mgr = LarkCredentialManager(db)
            await mgr.save_credential(LarkCredential(
                agent_id=agent_id,
                app_id="pending_setup",
                app_secret_ref="",
                brand=brand,
                profile_name=profile_name,
                workspace_path=str(workspace),
                auth_status="not_logged_in",
                is_active=False,
            ))

            _finalize_task = asyncio.create_task(
                _finalize_setup(agent_id, proc, workspace, profile_name),
                name=f"lark_finalize_setup:{agent_id}",
            )

            def _log_finalize_exception(task: asyncio.Task, aid: str = agent_id) -> None:
                if task.cancelled():
                    logger.warning(f"Lark finalize_setup cancelled for agent={aid}")
                    return
                exc = task.exception()
                if exc is not None:
                    logger.error(
                        f"Lark finalize_setup failed for agent={aid}: {exc!r}",
                        exc_info=exc,
                    )

            _finalize_task.add_done_callback(_log_finalize_exception)

            return {
                "success": True,
                "data": {
                    "auth_url": auth_url,
                    "profile_name": profile_name,
                    "workspace": str(workspace),
                    "message": (
                        "Step 1/3 — Open the URL in a browser to create your "
                        "Lark app. When you see a success page, tell me.\n\n"
                        "After Click 1 completes, we'll do Click 2 (submit "
                        "scope request to admin) and Click 3 (your personal "
                        "authorization) via lark_permission_advance, then "
                        "paste App Secret to enable receive."
                    ),
                    "next_step": (
                        "After user confirms Click 1 done, call "
                        "lark_permission_advance(agent_id) to mint Click 2 URL."
                    ),
                },
            }

        except Exception as e:
            try:
                proc.kill()
                await proc.wait()
            except (ProcessLookupError, OSError, UnboundLocalError):
                pass
            return {"success": False, "error": f"Setup failed: {e}"}

    @mcp.tool()
    async def lark_bind(
        agent_id: str,
        app_id: str,
        app_secret: str,
        brand: str,
        owner_email: str = "",
    ) -> dict:
        """Bind an EXISTING Lark/Feishu app (user has app_id + app_secret on hand).

        State: agent has NO credential row yet. Triggered when user pastes:
          - string starting with `cli_` + a long secret, OR
          - phrases like "帮我绑定 / bind my app / here's my id and secret".

        BEFORE CALLING — ask "Feishu or Lark International?". App ID prefix is
        identical across platforms; wrong choice → WebSocket subscribe fails
        silently (err 1000040351).

        After success: bot can SEND. To RECEIVE:
          1. Run `lark_permission_advance(agent_id)` for three-click authorization.
          2. Run `lark_enable_receive(agent_id, app_secret)` to start subscriber.

        Args:
            agent_id: The agent to bind the bot to.
            app_id: Lark app ID (starts with `cli_`).
            app_secret: Lark app secret.
            brand: "feishu" (中国大陆) or "lark" (International).
            owner_email: Optional but recommended so "me/my/I" resolves correctly.
        """
        from ._lark_service import do_bind

        if brand not in ("feishu", "lark"):
            return {"success": False, "error": "brand must be 'feishu' or 'lark'."}

        if owner_email and "@" not in owner_email:
            return {"success": False, "error": "Invalid owner_email format."}

        db = await XYZBaseModule.get_mcp_db_client()
        mgr = LarkCredentialManager(db)
        return await do_bind(mgr, agent_id, app_id, app_secret, brand, owner_email)

    @mcp.tool()
    async def lark_permission_advance(agent_id: str, event: str = "") -> dict:
        """Single entry for Lark three-click permission lifecycle.

        Three clicks in enterprise tenants:
          Click 1 — create app (handled by `lark_setup`)
          Click 2 — submit scope request to admin  → event=""
          Click 3 — user's personal authorization  → event="admin_approved" (generates URL)
                                                   → event="user_authorized" (completes)

        EVENT VALUES — pick by reading Matrix row 2 stage, NOT by user's words.
        "完成了 / 点了 / done" can mean any of Click 1/2/3 depending on stage.

          event=""                stage=not_started
            Generates Click 2 URL (submit scope request to admin). Idempotent:
            returns existing URL if admin_request_url already on file.
          event="admin_approved"  stage=waiting_admin
            User confirmed admin approved. Mints a FRESH device_code and
            returns Click 3 URL. Rejects if admin_request_url is empty.
          event="user_authorized" stage=waiting_user_click
            Polls user_authz_device_code for token.
            authorization_pending → ask user to re-confirm; NO auto-retry.
            expired → auto-regenerate Click 3 URL, returned as data.fresh_url.
          event="availability_ok" stage=completed (optional)
            User confirmed app is visible to all staff.

        Return always includes `user_facing_message` — send to user verbatim.
        """
        cred = await _get_credential(agent_id)
        if not cred:
            return {
                "success": False,
                "error": "No Lark bot bound. Run lark_setup or lark_bind first.",
            }

        stage = cred.current_click_stage()

        # Guard: already completed + non-availability event → harmless no-op
        if stage == "completed" and event in ("admin_approved", "user_authorized"):
            return {
                "success": False,
                "error": "Already completed. Check Matrix row 2 before calling.",
                "data": {
                    "url": None,
                    "click_label": None,
                    "user_facing_message": _MSG_ALREADY_COMPLETED,
                    "stage_after": "completed",
                    "fresh_url": None,
                },
            }

        if event == "":
            return await _advance_start(agent_id, cred)
        if event == "admin_approved":
            return await _advance_admin_approved(agent_id, cred)
        if event == "user_authorized":
            return await _advance_user_authorized(agent_id, cred)
        if event == "availability_ok":
            return await _advance_availability_ok(agent_id, cred)

        return {
            "success": False,
            "error": (
                f"Unknown event '{event}'. Valid: '' | 'admin_approved' | "
                f"'user_authorized' | 'availability_ok'."
            ),
        }

    @mcp.tool()
    async def lark_enable_receive(agent_id: str, app_secret: str) -> dict:
        """Store plain App Secret so the WebSocket subscriber can auto-reply
        to incoming Lark messages (Phase 3, after three-click authorization).

        State: Matrix rows 1 + 2 = ✅, row 3 = ❌.

        Guide user: open `dev_console_url` (from lark_status), go to
        'Credentials & Basic Info', copy App Secret, paste here.

        Subscriber starts within ~10s after success; call `lark_status` after
        15s if user wants confirmation.
        """
        cred = await _get_credential(agent_id)
        if not cred:
            return {"success": False, "error": "No Lark bot bound. Run lark_setup first."}

        secret = (app_secret or "").strip()
        if not secret:
            return {
                "success": False,
                "error": "app_secret is empty. Re-copy from the dev console and try again.",
            }

        db = await XYZBaseModule.get_mcp_db_client()
        mgr = LarkCredentialManager(db)
        await mgr.set_app_secret_encoded(agent_id, secret)

        return {
            "success": True,
            "data": {
                "message": (
                    "App Secret stored. The trigger will pick up the bot and "
                    "start receiving messages within ~10 seconds. Call "
                    "lark_status after that to verify."
                ),
                "profile_name": cred.profile_name,
            },
        }

    @mcp.tool()
    async def lark_status(agent_id: str) -> dict:
        """Read current auth/receive/connectivity state; self-heal Matrix from CLI.

        Use for:
          - Sanity check after lark_setup or lark_bind completes
          - User asks "is Lark working / what bot am I using / am I logged in"
          - Matrix looks stale (e.g. `--as user` calls succeed but row 2 still ❌)

        Do NOT use to verify a fresh Click 3 — that's the job of
        `lark_permission_advance(event="user_authorized")`, which polls Lark
        to actually mint the token. `lark_status` only reads local state.

        Returns include `stage` (not_started | waiting_admin | waiting_user_click
        | completed), `receive_enabled`, `dev_console_url`, plus raw auth / doctor
        data from the CLI.
        """
        cred = await _get_credential(agent_id)
        if not cred:
            return {"success": False, "error": "No Lark bot bound."}

        auth = await _cli._run_with_agent_id(["auth", "status"], agent_id)
        doctor = await _cli._run_with_agent_id(["doctor"], agent_id)

        # Self-heal: CLI reports valid user token but our DB still has
        # user_oauth_completed_at=None → trust CLI, flip state forward.
        auth_data = auth.get("data", {}) if auth.get("success") else {}
        cli_user_logged_in = (
            auth_data.get("identity") == "user"
            or auth_data.get("tokenType") == "user"
            or auth_data.get("tokenStatus") == "valid"
            or bool(auth_data.get("users"))
        )
        if cli_user_logged_in and not cred.user_oauth_ok():
            now = datetime.now(timezone.utc).isoformat()
            ps = cred.permission_state or {}
            db = await XYZBaseModule.get_mcp_db_client()
            await LarkCredentialManager(db).patch_permission_state(agent_id, {
                "user_oauth_completed_at": now,
                "user_authz_url": None,
                "user_authz_device_code": None,
                "bot_scopes_confirmed": True,
                "console_setup_done_at": ps.get("console_setup_done_at") or now,
            })
            await LarkCredentialManager(db).update_auth_status(
                agent_id, AUTH_STATUS_USER_LOGGED_IN
            )
            logger.info(
                f"lark_status: self-healed OAuth state for {agent_id} "
                f"(CLI confirms user token is valid)"
            )
            cred = await _get_credential(agent_id)

        return {
            "success": True,
            "data": {
                "auth": auth.get("data", {}),
                "doctor": doctor.get("data", {}),
                "stage": cred.current_click_stage(),
                "receive_enabled": cred.receive_enabled(),
                "user_oauth_ok": cred.user_oauth_ok(),
                "permission_state": cred.permission_state,
                "dev_console_url": _dev_console_url(cred.brand, cred.app_id),
                "profile_name": cred.profile_name,
                "app_id": cred.app_id,
                "brand": cred.brand,
            },
        }

    @mcp.tool()
    async def lark_skill(
        agent_id: str,
        name: str,
        path: str = "SKILL.md",
    ) -> dict:
        """Read any file from a Lark CLI skill pack. This is the **only**
        way to access Lark skill docs on NarraNexus — the `Read` / `Glob`
        / `Grep` tools cannot see these files (they live inside the MCP
        container, not in the agent workspace).

        Typical flow:

          1. First call: `lark_skill(agent_id, "lark-shared")` — auth,
             permissions, and the `--as user` vs `--as bot` rules every
             other domain relies on.
          2. Then the domain skill, e.g. `lark_skill(agent_id, "lark-im")`.
             That returns the skill's SKILL.md index page.
          3. When the returned markdown references another file, call this
             tool again with `path=<the relative path from that link>`.
             Example: a link `[+search-user](references/lark-contact-search-user.md)`
             inside `lark-contact/SKILL.md` is fetched as
             `lark_skill(agent_id, "lark-contact",
                         path="references/lark-contact-search-user.md")`.
             A cross-skill link `[auth rules](../lark-shared/SKILL.md)`
             means "switch skill": `lark_skill(agent_id, "lark-shared")`.

        The MCP tool auto-rewrites in-document links next to each link
        label so the correct `lark_skill(...)` call is spelled out for
        you — copy it verbatim.

        Available skills: lark-shared (read first), lark-im, lark-contact,
        lark-calendar, lark-doc, lark-sheets, lark-drive, lark-mail,
        lark-task, lark-wiki, lark-vc, lark-minutes, lark-base, lark-event,
        lark-whiteboard, lark-slides, lark-okr, lark-approval,
        lark-attendance, lark-workflow-meeting-summary,
        lark-workflow-standup-report, lark-openapi-explorer,
        lark-skill-maker.

        Args:
            agent_id: Kept for API consistency; skill content is
                agent-agnostic.
            name: Skill name; "lark-im" or "im" form both accepted.
            path: Relative filename inside the skill directory. Defaults
                to "SKILL.md" (the index page). Must stay within the
                skill dir — "../elsewhere" is rejected.
        """
        from ._lark_skill_loader import get_available_skills, load_skill_file
        normalized = name if name.startswith("lark-") else f"lark-{name}"
        content = load_skill_file(normalized, path)
        if content is None:
            logger.info(
                f"[LarkSkill] miss agent={agent_id} skill={normalized} path={path!r}"
            )
            return {
                "success": False,
                "error": (
                    f"File '{path}' not found in skill '{normalized}' "
                    "(or path escapes the skill directory)."
                ),
                "available": get_available_skills(),
            }
        logger.info(
            f"[LarkSkill] hit agent={agent_id} skill={normalized} "
            f"path={path!r} size={len(content)}"
        )
        return {
            "success": True,
            "name": normalized,
            "path": path,
            "content": content,
        }

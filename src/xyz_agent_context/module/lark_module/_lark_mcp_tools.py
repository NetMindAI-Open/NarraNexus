"""
@file_name: _lark_mcp_tools.py
@date: 2026-04-16
@description: Lark MCP tools — single generic lark_cli tool + lifecycle tools.

Tools exposed:
  - lark_cli(agent_id, command)                    — Run any lark-cli command
  - lark_setup(agent_id, brand, email)             — Create NEW Lark app (agent-assisted)
  - lark_bind(agent_id, app_id, app_secret, ...)   — Bind EXISTING app (user has credentials)
  - lark_configure_permissions(agent_id)           — One-shot permission bootstrap
  - lark_auth(agent_id, scopes)                    — Targeted OAuth (by scope)
  - lark_auth_complete(agent_id, dc)               — Complete OAuth device flow
  - lark_mark_console_done(agent_id, flags)        — Confirm console manual steps
  - lark_status(agent_id)                          — Auth + connectivity + state
  - lark_enable_receive(agent_id, secret)          — Enable real-time auto-reply
  - lark_skill(agent_id, name)                     — Load SKILL.md for a Lark domain
"""

from __future__ import annotations

import asyncio
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

    We only treat EXPLICIT user identity as an observable success signal.
    `--as auto` / no flag / `--as bot` don't count, since those might route
    through the bot token even for commands that happen to support user id.
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


# Recommended bot scopes the user should enable in the dev console during
# the one-time manual setup. Covers every domain the SDK subscriber + the
# lark_cli MCP tool need for daily agent work.
_RECOMMENDED_BOT_SCOPES = [
    # Messaging (core — required for send + receive)
    "im:message",
    "im:message:send_as_bot",
    "im:resource",
    "im:chat",
    "im:chat:readonly",
    # Contact (needed for lookup, sender resolution, owner binding)
    "contact:user.base:readonly",
    "contact:user.email:readonly",
    "contact:contact:readonly",
    # Docs / Calendar / Drive (everyday agent work)
    "docs:document",
    "docs:document:readonly",
    "calendar:calendar",
    "calendar:calendar:readonly",
    "drive:drive",
    "drive:drive:readonly",
    "sheets:spreadsheet",
    "sheets:spreadsheet:readonly",
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
            logger.error(f"lark_setup: {agent_id} malformed config.json: {e}")
            db = await XYZBaseModule.get_mcp_db_client()
            await LarkCredentialManager(db).delete_credential(agent_id)
            return

        # Step 3: find our profile (matched by name) in the apps list
        apps = config.get("apps", [])
        our_app = next((a for a in apps if a.get("name") == profile_name), None)
        if not our_app and len(apps) == 1:
            # CLI may omit `name` when only a single profile exists; use the first
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
        logger.error(f"lark_setup: {agent_id} _finalize_setup unexpected error: {e}")
        try:
            db = await XYZBaseModule.get_mcp_db_client()
            await LarkCredentialManager(db).delete_credential(agent_id)
        except Exception:
            pass


def register_lark_mcp_tools(mcp: Any) -> None:
    """Register Lark MCP tools and resources on the given FastMCP server."""

    # =====================================================================
    # Core Tool: lark_cli
    # =====================================================================

    @mcp.tool()
    async def lark_cli(agent_id: str, command: str) -> dict:
        """
        Run any lark-cli command with per-agent profile isolation. This is
        the main execution tool for ALL Lark data operations.

        **WHEN TO CALL**: any time you need to interact with Lark data —
        send messages, search contacts, read/create docs, query calendar,
        manage tasks, etc.

        **BEFORE FIRST USE OF A DOMAIN** (in this session): call
        `lark_skill(agent_id, name)` to load that domain's SKILL.md. The
        skill doc teaches correct syntax and identity rules — without it
        you'll waste turns guessing. Example: before any `im +...` command,
        call `lark_skill(agent_id, "lark-im")`.

        **COMMAND FORMAT**: whatever you'd type after `lark-cli`. Examples:
          - "im +messages-send --user-id ou_xxx --text hello --as bot"
          - "contact +search-user --query 'John Smith' --as user"
          - "calendar +agenda --as user"
          - "docs +create --title 'My Doc' --markdown '# Content' --as bot"
          - "schema im.messages.create"    (look up API field definitions)
          - "im +messages-send --help"     (discover a command's flags)

        **IDENTITY — pick the right `--as`** (required on most commands):
          - `--as bot`: sending messages, creating docs, actions the app performs
          - `--as user`: search/read that requires user identity (contact
            search, message search, doc search)

        **DO NOT**:
          - add `--profile` — profile isolation is injected automatically
          - add `--format json` — Shortcut commands (the ones with `+`) reject it
          - shell out to `lark-cli` via Bash — always use this tool

        **ON FAILURE**:
          - error contains "missing scope X" or "permission denied"
            → call `lark_auth(agent_id, scopes="X")`, send URL to user
          - "Command blocked" (whitelist hit)
            → you tried a lifecycle command; use the dedicated tool instead
              (lark_setup / lark_auth / lark_auth_complete)
          - "No Lark bot bound"
            → call `lark_setup(agent_id, ...)` first

        Args:
            agent_id: The agent performing this action.
            command: The lark-cli command string (WITHOUT the "lark-cli"
                     prefix and WITHOUT --profile).

        Returns:
            {"success": True, "data": <parsed CLI output>} or
            {"success": False, "error": "..."}.
        """
        cred = await _get_credential(agent_id)
        if not cred:
            return {"success": False, "error": "No Lark bot bound. Use lark_setup to create one."}

        # Validate command
        allowed, reason = validate_command(command)
        if not allowed:
            return {"success": False, "error": f"Command blocked: {reason}"}

        # Parse into args
        try:
            args = sanitize_command(command)
        except ValueError as e:
            return {"success": False, "error": str(e)}

        # Execute with HOME isolation
        result = await _cli._run_with_agent_id(args, agent_id)

        # Self-healing state inference: if the user's lark-cli interaction
        # succeeded via user identity, that's direct proof user OAuth is
        # active — even if lark_auth_complete was never explicitly called
        # or if the state was stuck as False from some earlier hiccup.
        # Flip the matrix so Agent and user see reality.
        try:
            if result.get("success") and _command_uses_user_identity(args):
                from datetime import datetime, timezone
                ps = cred.permission_state or {}
                if not ps.get("user_oauth_completed_at"):
                    now = datetime.now(timezone.utc).isoformat()
                    db = await XYZBaseModule.get_mcp_db_client()
                    # Observable fact: a --as user call succeeded → the user
                    # OAuth token is valid, and Lark had the scope published
                    # for the app (otherwise the call would return missing-
                    # scope). Flip both OAuth + bot-scope milestones.
                    await LarkCredentialManager(db).patch_permission_state(agent_id, {
                        "user_oauth_completed_at": now,
                        "user_oauth_url": None,
                        "user_oauth_device_code": None,
                        "user_oauth_mode": None,
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
            # Self-heal is best-effort; never fail the actual call.
            logger.debug(f"lark_cli self-heal skipped: {e}")

        return result

    # =====================================================================
    # Lifecycle: lark_setup
    # =====================================================================

    @mcp.tool()
    async def lark_setup(agent_id: str, brand: str = "lark", owner_email: str = "") -> dict:
        """
        Create a NEW Lark/Feishu app and bind it as this agent's bot. Replaces
        the manual 9-step app-creation process with a single authorization URL.

        **WHEN TO CALL**: the user asks to "connect Lark / Feishu", "set up
        Lark", or similar AND has no existing app. Only works when the agent
        has NO bot bound yet.

        **DO NOT CALL** when the user already provides an App ID + App Secret.
        That means they have an existing app — use `lark_bind` instead.
        Signs the user is pasting existing credentials:
          - A string starting with `cli_` (App ID)
          - A long random string alongside it (App Secret)
          - Phrases like "帮我绑定", "bind my app", "here's my app id/secret"
        Calling `lark_setup` in that case creates a useless NEW app and
        discards the credentials the user just gave you.

        **BEFORE CALLING — COLLECT FROM USER (always ask, do NOT silently
        default)**:
          1. `brand`: are they using "feishu" (飞书 · 中国大陆) or "lark"
             (Lark · International)? These are DIFFERENT platforms. Example
             opening line: "To set up Lark I need two things — are you on
             Feishu (飞书) or Lark International? And what's your Lark /
             Feishu email for identity linking?"
          2. `owner_email`: their Lark/Feishu email. Used after bind to find
             their `open_id` in the org directory so you know who "me" is.

        **AFTER RETURN — TWO-PHASE FLOW**:
          Phase 1 — create the app in browser:
          - `success=True` with `data.auth_url` → send the URL to the user
            verbatim. Tell them:
              "Please open this link in your browser to finish app creation.
               Tell me when you're done."
          - The app is created once the user authorizes; the bot binding
            completes in the background.

          Phase 2 — enable real-time receive:
          - After the user confirms Phase 1 is done → call `lark_status` to
            verify `auth.status == bot_ready` AND read `dev_console_url`.
          - Guide the user: "The bot can now SEND messages. To let it
            auto-reply when someone messages you on Lark, I need your App
            Secret. Open <dev_console_url>, go to 'Credentials & Basic
            Info', and paste me the App Secret."
          - When user pastes, call `lark_enable_receive(agent_id, <secret>)`.
          - Real-time receive is live within ~10s after that.

          Skipping Phase 2 is fine if the user only needs the bot to send
          (e.g. outbound notifications). Tell them clearly that without
          Phase 2 the bot won't hear incoming Lark messages.

        **FAILURE**:
          - "Agent already has a Lark bot" → tell the user; offer to unbind
            (they can do so from the frontend LarkConfig panel, or via
            DELETE /api/lark/unbind) before trying again.
          - URL extraction timeout / "Could not extract setup URL"
            → usually a network or lark-cli installation issue. Tell the
            user exactly what happened; don't silently retry.

        Args:
            agent_id: The agent to set up.
            brand: "feishu" (中国大陆) or "lark" (International). Default
                   "lark" is a fallback — ALWAYS confirm with the user first.
            owner_email: User's Lark/Feishu email address.

        Returns:
            {"success": True, "data": {"auth_url": "...", ...}} or error.
        """
        import re

        if brand not in ("feishu", "lark"):
            return {"success": False, "error": "brand must be 'feishu' or 'lark'."}

        # Refuse if this agent is already bound or has a pending setup row
        existing = await _get_credential(agent_id)
        if existing:
            return {
                "success": False,
                "error": (
                    "Agent already has a Lark bot (or a pending setup). "
                    "Unbind first via frontend LarkConfig or DELETE /api/lark/unbind."
                ),
            }

        # Build a stable, human-readable profile name from the agent's name
        agent_name = await _get_agent_name(agent_id)
        profile_name = build_profile_name(agent_name, agent_id)

        # Isolated workspace so the CLI writes config to workspace/.lark-cli/
        workspace = ensure_workspace(agent_id)
        env = get_home_env(agent_id)

        # Launch the interactive CLI setup. It prints an auth URL then blocks
        # until the user completes app creation in the browser. We extract the
        # URL here and hand off the process to _finalize_setup (background).
        try:
            proc = await asyncio.create_subprocess_exec(
                "lark-cli", "config", "init", "--new",
                "--brand", brand, "--name", profile_name,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,  # merge so URL always lands in stdout
                env=env,
            )
        except FileNotFoundError:
            return {"success": False, "error": "lark-cli not found. Install: npm install -g @larksuite/cli"}

        try:
            # Scan stdout for the auth URL (usually within the first few seconds).
            collected = b""

            async def _read_until_url():
                nonlocal collected
                while True:
                    chunk = await proc.stdout.read(4096)
                    if not chunk:
                        return
                    collected += chunk
                    if b"http" in collected.lower():
                        # Read a bit more so the full URL line lands in the buffer.
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

            # Persist a placeholder credential so concurrent lark_setup calls
            # fail fast. is_active=False keeps the trigger watcher from trying
            # to start a subscriber before finalize writes the real app_id.
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

            # Hand the subprocess off to a background finalizer. When the
            # user finishes in the browser (or we time out after 15 min),
            # it reads the CLI-written config.json and flips the DB row
            # to bot_ready with the real app_id + keychain reference.
            asyncio.create_task(
                _finalize_setup(agent_id, proc, workspace, profile_name)
            )

            return {
                "success": True,
                "data": {
                    "auth_url": auth_url,
                    "profile_name": profile_name,
                    "workspace": str(workspace),
                    "message": (
                        "Step 1/2 — Open the URL in a browser to create your Lark "
                        "app. When you see a success page, tell me.\n\n"
                        "Step 2/2 (later) — I'll then need you to paste the App "
                        "Secret so I can auto-reply to incoming Lark messages. "
                        "Without that step, I can send to Lark but can't listen."
                    ),
                    "next_step": (
                        "After browser auth, call lark_status(agent_id) to get "
                        "dev_console_url, then guide the user through "
                        "lark_enable_receive."
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

    # =====================================================================
    # Lifecycle: lark_bind (existing app)
    # =====================================================================

    @mcp.tool()
    async def lark_bind(
        agent_id: str,
        app_id: str,
        app_secret: str,
        brand: str = "lark",
        owner_email: str = "",
    ) -> dict:
        """
        Bind an EXISTING Lark/Feishu app to this agent using credentials the
        user already has on hand (App ID + App Secret from the Lark Open
        Platform / 飞书开放平台 console).

        **WHEN TO CALL**: the user pastes app_id + app_secret (and usually
        email). Typical cues:
          - "帮我绑定 cli_xxx, secret_xxx, me@corp.com"
          - "bind my existing Feishu app: id=... secret=..."
          - Any time the user supplies values that look like an App ID
            (starts with `cli_`) AND an App Secret.

        Use `lark_setup` INSTEAD when the user has no app yet and wants to
        create one from scratch via browser flow.

        **BEFORE CALLING — CONFIRM BRAND**: the user may not say explicitly.
        Ask "Feishu (飞书 · 中国大陆) or Lark International?" unless the
        context makes it unambiguous.

        **AFTER RETURN**:
          - `success=True` → the bot can now SEND messages. Then, still
            needed for auto-reply:
              1. Call `lark_configure_permissions` (or `lark_auth`) to grant
                 scopes like im:message:receive_v1, contact:user.base:readonly.
              2. Call `lark_enable_receive(agent_id, app_secret)` to start
                 listening for incoming messages.
          - `success=False` with "App ID already bound" → tell the user which
            other agent has it. Each Lark app binds to one agent.
          - `success=False` with "Credential verification failed" → the
            app_id/app_secret combo is wrong; ask the user to re-check.

        Args:
            agent_id: The agent to bind the bot to.
            app_id: Lark app ID (typically starts with `cli_`).
            app_secret: Lark app secret.
            brand: "feishu" (中国大陆) or "lark" (International).
            owner_email: User's Lark/Feishu email for identity linking.
                         Optional but recommended — without it the bot can't
                         resolve "me" / "my calendar" / etc.

        Returns:
            {"success": True, "data": {"profile_name": ..., "brand": ...,
             "app_id": ..., "auth_status": "bot_ready",
             "owner_open_id": ..., "owner_name": ...}} or
            {"success": False, "error": "..."}.
        """
        from ._lark_service import do_bind

        if brand not in ("feishu", "lark"):
            return {"success": False, "error": "brand must be 'feishu' or 'lark'."}

        if owner_email and "@" not in owner_email:
            return {"success": False, "error": "Invalid owner_email format."}

        db = await XYZBaseModule.get_mcp_db_client()
        mgr = LarkCredentialManager(db)
        return await do_bind(mgr, agent_id, app_id, app_secret, brand, owner_email)

    # =====================================================================
    # Lifecycle: lark_auth + lark_auth_complete
    # =====================================================================

    @mcp.tool()
    async def lark_auth(agent_id: str, scopes: str = "") -> dict:
        """
        Initiate an OAuth login flow to grant the bound bot one or more
        permission scopes. Returns a verification URL + device_code.

        **WHEN TO CALL** (one of):
          - A previous `lark_cli` call failed with "missing scope X",
            "permission denied", or a similar scope error → pass the missing
            scope name in `scopes`.
          - The user explicitly asks to grant more permissions / complete
            OAuth / re-authorize.

        **DO NOT CALL PREEMPTIVELY**: never request scopes "just in case" or
        before the user's first real action. Unnecessary auth prompts annoy
        users. Wait for an actual failure or explicit user request.

        **AFTER RETURN**:
          1. `data.verification_url` and `data.device_code` are returned.
          2. Send the URL to the user — do not annotate it, just present
             it as "the authorization link".
          3. The user will see one of two buttons:
             - **Authorize** → a single click completes the grant.
             - **Submit for approval** → the user is requesting permissions
               that need admin approval. They click, wait for an admin, then
               come back. You may need to call lark_auth again to get a
               fresh URL after approval.
          4. After the user confirms they clicked "Authorize" → call
             `lark_auth_complete(agent_id, device_code)` with the SAME
             device_code you got here.

        **FAILURE**:
          - "No Lark bot bound" → call `lark_setup` first.
          - Timeout → retry once. If it times out again, ask the user about
            network issues; don't silently loop.

        Args:
            agent_id: The agent whose bot to authorize.
            scopes: Space-separated scope names as surfaced by Lark error
                    messages. Example: "im:chat:create contact:user.base:readonly".
                    If empty, falls back to `--recommend` (Lark's default
                    bundle). Prefer specific scopes when you know what's
                    missing.

        Returns:
            {"success": True, "data": {"verification_url": "...",
             "device_code": "...", "next_step": "..."}} or error.
        """
        cred = await _get_credential(agent_id)
        if not cred:
            return {"success": False, "error": "No Lark bot bound. Use lark_setup first."}

        if scopes:
            args = ["auth", "login", "--scope", scopes, "--json", "--no-wait"]
        else:
            args = ["auth", "login", "--recommend", "--json", "--no-wait"]

        result = await _cli._run_with_agent_id(
            args,
            agent_id,
            timeout=60.0,
        )
        if result.get("success"):
            data = result.get("data", {})
            device_code = data.get("device_code", "")
            verification_url = data.get("verification_url", "")
            if device_code and verification_url:
                # Persist pending OAuth state so lark_auth_complete can read
                # the device_code from DB — the Agent should never have to
                # hand-copy this long random string across turns.
                db = await XYZBaseModule.get_mcp_db_client()
                await LarkCredentialManager(db).patch_permission_state(agent_id, {
                    "user_oauth_url": verification_url,
                    "user_oauth_device_code": device_code,
                    # mode="targeted" so lark_auth_complete knows NOT to
                    # auto-flip bot_scopes_confirmed (which would be wrong
                    # for a single-scope incremental grant).
                    "user_oauth_mode": "targeted",
                })
                result["data"]["next_step"] = (
                    "Send the verification_url to the user. After they "
                    "authorize, call `lark_auth_complete(agent_id)` — "
                    "DO NOT pass device_code, it's stored in DB."
                )
        return result

    @mcp.tool()
    async def lark_auth_complete(agent_id: str, device_code: str = "") -> dict:
        """
        Finalize an OAuth login flow after the user has clicked the
        authorization URL. Exchanges the pending device_code for tokens
        and flips `auth_status` to `user_logged_in`.

        **MANDATORY FIRST RESPONSE** to any user "done / authorized /
        完成了 / 点好了 / 授权了" confirmation. OAuth device flow requires
        the client to poll with the device_code AFTER user clicks — that
        poll IS this tool. Skipping it (e.g. calling `lark_status` instead)
        leaves the token unclaimed on Lark's side; the click effectively
        evaporates and `user_oauth_ok` stays false. NEVER verify OAuth
        completion with `lark_status` before calling this tool.

        **WHEN TO CALL**: the user confirms they clicked "Authorize" on the
        URL you sent them. Typical cues: "done", "authorized", "I clicked
        it", "点好了", "完成了", "授权了", etc. Call IMMEDIATELY — don't
        wait, don't poll, don't call `lark_status` first.

        **NEVER PASS device_code YOURSELF**. Leave it empty and this tool
        reads the pending code from DB. Hand-copying long random strings
        across turns is lossy — we've seen them truncated at punctuation
        boundaries, causing "device_code is invalid" loops that waste
        many rounds. The device_code is generated and stored atomically
        by `lark_configure_permissions` / `lark_auth`; trust the DB.

        **DO NOT POLL OR PREEMPT**: only call this once, in direct response
        to user confirmation. Do not loop unless explicitly told.

        **AFTER RETURN**:
          - `success=True` → retry the original command. Tell the user:
            "Authorized, retrying..."
          - `success=False` with `data.fresh_oauth_url`: the previous code
            expired or authorization was still pending. Send the fresh URL
            to the user and call this tool again after they re-click.
          - `success=False` without a fresh URL: propagate the error text.

        Args:
            agent_id: The agent whose bot is being authorized.
            device_code: Optional. If empty (recommended), the tool reads
                         the pending device_code from DB. Only pass a value
                         if you have a specific non-pending code to finalize.

        Returns:
            {"success": True, "data": {...}} on success, or
            {"success": False, "error": "...", "data": {"fresh_oauth_url": "...", "fresh_device_code": "..."}}
            when the previous code was stale and we auto-regenerated.
        """
        cred = await _get_credential(agent_id)
        if not cred:
            return {"success": False, "error": "No Lark bot bound."}

        # Prefer the DB-stored device_code over whatever the caller passed.
        # LLMs sometimes hand-copy the long device_code and truncate it at
        # punctuation boundaries — never trust the parameter unless the DB
        # has none.
        stored_code = (cred.permission_state or {}).get("user_oauth_device_code") or ""
        effective_code = stored_code or device_code
        if not effective_code:
            return {
                "success": False,
                "error": (
                    "No pending OAuth to complete. Call "
                    "`lark_configure_permissions` first to start a new flow."
                ),
            }

        result = await _cli._run_with_agent_id(
            ["auth", "login", "--device-code", effective_code, "--json"],
            agent_id,
            timeout=60.0,
        )

        if result.get("success"):
            from datetime import datetime, timezone
            db = await XYZBaseModule.get_mcp_db_client()
            mgr = LarkCredentialManager(db)
            await mgr.update_auth_status(agent_id, AUTH_STATUS_USER_LOGGED_IN)
            data = result.get("data", {})
            granted = (
                data.get("scopes")
                or data.get("granted_scopes")
                or data.get("user", {}).get("scopes", [])
                or []
            )
            now = datetime.now(timezone.utc).isoformat()
            current_state = (await mgr.get_credential(agent_id)).permission_state or {}
            was_recommend_all = current_state.get("user_oauth_mode") == "recommend_all"

            patch: dict = {
                "user_oauth_completed_at": now,
                "user_scopes_granted": list(granted) if isinstance(granted, (list, tuple)) else [],
                "user_oauth_url": None,
                "user_oauth_device_code": None,
                "user_oauth_mode": None,
            }
            # When recommend_all, Lark auto-publishes a version with every
            # auto-approvable scope — the "bot scopes + version publish"
            # milestone is done atomically with OAuth. Flip both flags.
            if was_recommend_all:
                patch["bot_scopes_confirmed"] = True
                patch["console_setup_done_at"] = now
            await mgr.patch_permission_state(agent_id, patch)
            return result

        # --- OAuth completion failed ---
        err_msg = (result.get("error") or "").lower()

        # `authorization_pending` / `slow_down`: user just hasn't clicked
        # yet (or clicked milliseconds ago and Lark hasn't propagated).
        # DO NOT auto-regen — that would invalidate the URL the user is
        # about to click. Return a specific actionable message.
        if "authorization_pending" in err_msg or "slow_down" in err_msg:
            return {
                "success": False,
                "error": (
                    "Lark says authorization is still pending. The user "
                    "either hasn't clicked 'Authorize' yet, or clicked very "
                    "recently and it hasn't propagated. Wait a few seconds "
                    "and try once more — do not regenerate the URL."
                ),
            }

        # Genuinely stale / consumed / malformed device_code — regenerate.
        # Narrow matchers to avoid false positives (earlier version matched
        # "device_code" as a substring of normal error messages).
        is_stale = any(kw in err_msg for kw in (
            "expired",
            "invalid_grant",
            "is invalid",           # "The device_code is invalid"
            "restart the device",   # Lark's explicit hint
        ))
        if not is_stale:
            # Some other failure (network, access_denied, app misconfig) —
            # propagate as-is. Don't mask with a regen.
            return result

        # Regenerate URL automatically so the user gets ONE next-step action
        logger.info(
            f"lark_auth_complete: device_code stale for {agent_id}, regenerating URL"
        )
        regen = await _cli._run_with_agent_id(
            ["auth", "login", "--domain", "all", "--recommend", "--json", "--no-wait"],
            agent_id,
            timeout=60.0,
        )
        if not regen.get("success"):
            return {
                "success": False,
                "error": (
                    f"OAuth completion failed ({result.get('error', 'unknown')}), "
                    f"and regenerating a fresh URL also failed. Ask the user to "
                    f"try again in a moment."
                ),
                "original_error": result.get("error"),
            }

        regen_data = regen.get("data", {})
        new_url = regen_data.get("verification_url", "")
        new_code = regen_data.get("device_code", "")
        if new_url and new_code:
            db = await XYZBaseModule.get_mcp_db_client()
            await LarkCredentialManager(db).patch_permission_state(agent_id, {
                "user_oauth_url": new_url,
                "user_oauth_device_code": new_code,
                "user_oauth_mode": "recommend_all",
            })

        return {
            "success": False,
            "error": (
                "Previous OAuth URL expired or authorization was still pending. "
                "I've generated a fresh link — send it to the user, wait for "
                "them to click AND confirm, then call this tool again with the "
                "new device_code."
            ),
            "data": {
                "fresh_oauth_url": new_url,
                "fresh_device_code": new_code,
                "original_error": result.get("error"),
            },
        }

    # =====================================================================
    # Lifecycle: lark_configure_permissions + lark_mark_console_done
    # =====================================================================

    @mcp.tool()
    async def lark_configure_permissions(agent_id: str) -> dict:
        """
        Kick off the one-time "grant all recommended permissions" flow for
        this agent's bound Lark bot. Returns BOTH the user-OAuth URL (one
        click → all user scopes across every domain) AND the dev-console
        checklist the user must finish manually (bot scopes + availability
        + version publish — these are Lark admin-console operations that
        have no public API).

        **WHEN TO CALL**: exactly ONCE, right after `lark_setup` succeeds
        and the user confirms Phase 1 is done. Skipping this step leaves
        the bot able to send/read only its own resources — most real
        agent work (calendar, docs, contact lookup, etc.) will hit
        permission_denied.

        **WORKFLOW YOU DRIVE WITH THE USER**:
          1. Call this tool. Receive `oauth_url`, `oauth_device_code`,
             `dev_console_url`, and `console_checklist`.
          2. Send the user a single message:
             "Two quick things:
                A. Click this to grant all user-level Lark access in one go:
                   <oauth_url>
                B. Open <dev_console_url> and finish three clicks:
                   {console_checklist}
              Tell me when both are done."
          3. When user confirms the OAuth click:
             → call `lark_auth_complete(agent_id, device_code=<oauth_device_code>)`
          4. When user confirms the dev-console checklist:
             → call `lark_mark_console_done(agent_id)`
          5. Finally ask for App Secret → call `lark_enable_receive` so
             the trigger subscriber can start.

        **IDEMPOTENT** — re-calling rotates the OAuth URL (useful if the
        first one expired). State in DB is overwritten.

        Args:
            agent_id: The agent whose bot to configure.

        Returns:
            {"success": True, "data": {
                "oauth_url": "...", "oauth_device_code": "...",
                "dev_console_url": "...", "console_checklist": [...],
                "recommended_bot_scopes": [...],
                "message": "<instructions to pass to the user>"
            }}
        """
        cred = await _get_credential(agent_id)
        if not cred:
            return {"success": False, "error": "No Lark bot bound. Run lark_setup first."}

        # Launch the "all domains, recommended scopes" OAuth flow.
        result = await _cli._run_with_agent_id(
            ["auth", "login", "--domain", "all", "--recommend", "--json", "--no-wait"],
            agent_id,
            timeout=60.0,
        )
        if not result.get("success"):
            return result

        data = result.get("data", {})
        oauth_url = data.get("verification_url", "")
        device_code = data.get("device_code", "")
        if not oauth_url or not device_code:
            return {"success": False, "error": "CLI did not return an OAuth URL.", "raw": data}

        # Persist the pending OAuth so get_instructions can reason about it.
        # mode="recommend_all" tells lark_auth_complete to also flip
        # bot_scopes_confirmed on success — Lark auto-publishes a version
        # with all granted scopes when the user approves the
        # --recommend OAuth URL, so no separate console trip is needed.
        db = await XYZBaseModule.get_mcp_db_client()
        mgr = LarkCredentialManager(db)
        await mgr.patch_permission_state(agent_id, {
            "user_oauth_url": oauth_url,
            "user_oauth_device_code": device_code,
            "user_oauth_mode": "recommend_all",
        })

        console_url = _dev_console_url(cred.brand, cred.app_id)
        optional_hint = (
            "（可选）让其他同事能看到这个 bot：\n"
            f"1. 打开 {console_url} → 「版本管理」→「创建版本」\n"
            "2. 在「可见范围」里勾选你希望看到它的同事（或选全员）\n"
            "3. 点「保存」\n"
            "4. 点「申请线上发版」→ 等管理员审批\n"
            "5. 审批通过后其他人才会看到。\n"
            "不做这一步的话 bot 只有你自己可见，不影响它给你用。"
        )

        return {
            "success": True,
            "data": {
                "oauth_url": oauth_url,
                "oauth_device_code": device_code,
                "dev_console_url": console_url,
                "availability_hint": optional_hint,
                "recommended_bot_scopes": _RECOMMENDED_BOT_SCOPES,
                "message": (
                    "Tell the user TWO things:\n\n"
                    "**1. Permission request (REQUIRED)**:\n"
                    f"   点这个链接提交权限申请: {oauth_url}\n"
                    "   ⚠️ IMPORTANT: Tell the user clearly that clicking this "
                    "link ONLY submits the permission request. The enterprise "
                    "admin must approve it before it takes effect. If the user "
                    "is the admin, they should go to Lark Admin Console to "
                    "approve. After admin approval, the user must come back "
                    "for a SECOND authorization click (Step 2b).\n"
                    f"   When they confirm admin approval is done, call "
                    f"lark_auth(agent_id) to generate a new OAuth link for "
                    f"the actual user authorization.\n\n"
                    "**2. Visibility / availability (ALWAYS mention)**:\n"
                    f"   {optional_hint}\n"
                    "   Tell the user: right now ONLY they can see this bot. "
                    "If they want colleagues to also interact with it, they "
                    "need to do the above steps. This is optional but the "
                    "user should know about it upfront.\n"
                    "   Call lark_mark_console_done(availability_ok=True) "
                    "when the user confirms they set availability."
                ),
            },
        }

    @mcp.tool()
    async def lark_mark_console_done(
        agent_id: str,
        bot_scopes_ok: bool = True,
        availability_ok: bool = False,
    ) -> dict:
        """
        Record that the user has finished the dev-console manual steps.

        Two things can be tracked separately:
          - **bot_scopes_ok** (REQUIRED): scopes enabled + version published.
            Without this, most Lark APIs will hit permission_denied.
          - **availability_ok** (OPTIONAL): availability set to all staff /
            department. Without this, ONLY THE OWNER can see/use the bot
            inside Lark — other org members won't find it. This is fine
            for a personal-use bot; only needed if the user wants to share.

        **WHEN TO CALL**: when the user explicitly confirms they did the
        required step ("I added the scopes and published"). Pass
        availability_ok=True only if they ALSO say they set availability
        to all staff / multiple departments. Do NOT assume — ask.

        **DEFAULT**: availability_ok defaults to False because it's the
        less common intent. Always reflect what the user actually said.

        Args:
            agent_id: The agent.
            bot_scopes_ok: User says bot scopes are enabled AND a new
                           version was published. Required for most APIs.
            availability_ok: User says availability is set to all staff
                             (or multiple departments). Optional — without
                             it, only the owner can see the bot in Lark.

        Returns:
            {"success": True, "data": {"console_setup_done_at": "...", ...}}
            — console_setup_done_at is set once bot_scopes_ok is True
            (availability is tracked separately and does not block it).
        """
        cred = await _get_credential(agent_id)
        if not cred:
            return {"success": False, "error": "No Lark bot bound."}

        from datetime import datetime, timezone
        db = await XYZBaseModule.get_mcp_db_client()
        mgr = LarkCredentialManager(db)

        # Guard: in the recommend_all flow, bot_scopes_confirmed is driven
        # by lark_auth_complete succeeding — not by user say-so. If the
        # Agent tries to mark scopes done while there's still a pending
        # OAuth device_code, refuse and tell it to complete OAuth first.
        # This prevents the stuck-state bug where Agent parallel-calls
        # mark_console_done + auth_complete, auth_complete fails (expired
        # code / pending authz), but mark_console_done succeeds and makes
        # the status matrix lie.
        current_state = cred.permission_state or {}
        has_pending_oauth = bool(
            current_state.get("user_oauth_url")
            and current_state.get("user_oauth_device_code")
            and not current_state.get("user_oauth_completed_at")
        )
        if has_pending_oauth and bot_scopes_ok:
            return {
                "success": False,
                "error": (
                    "Refusing to mark console done while OAuth is still "
                    "pending. The `--recommend` OAuth auto-grants scopes and "
                    "auto-publishes — skipping it leaves the app with no "
                    "declared scopes. Call "
                    "`lark_auth_complete(agent_id, device_code=...)` first. "
                    "If that returns 'expired' or 'pending', re-run "
                    "`lark_configure_permissions(agent_id)` for a fresh URL, "
                    "wait for the user to click, THEN call auth_complete."
                ),
            }

        now = datetime.now(timezone.utc).isoformat()
        # console_setup_done_at tracks the REQUIRED work (bot scopes +
        # version published). Availability is optional and persisted on
        # its own flag so the status matrix can still render ➖ / ✅.
        patched = await mgr.patch_permission_state(agent_id, {
            "bot_scopes_confirmed": bool(bot_scopes_ok),
            "availability_confirmed": bool(availability_ok),
            "console_setup_done_at": now if bot_scopes_ok else None,
        })
        return {"success": True, "data": patched}

    # =====================================================================
    # Lifecycle: lark_status
    # =====================================================================

    @mcp.tool()
    async def lark_status(agent_id: str) -> dict:
        """
        Check the bound Lark bot's auth state and connectivity. Combines
        `auth status` (identity + login state) with `doctor` (network and
        CLI sanity checks).

        **NEVER USE TO VERIFY A FRESH USER OAUTH CLICK**. If the user just
        said "done / 完成了 / 授权了", call `lark_auth_complete` FIRST.
        That tool polls Lark to exchange the device_code for an access
        token — `lark_status` only reads local state and will return
        `user_oauth_ok=false` even when the user clicked successfully,
        because the token was never claimed. Confusing these two tools
        wastes a full regeneration cycle (user has to click twice).

        **WHEN TO CALL**:
          - Just after `lark_setup` completes, to verify the bind.
          - When a `lark_cli` call fails with a vague error and you want
            to know whether the bot itself is healthy.
          - When the user asks "is Lark working?", "what bot am I using?",
            "am I logged in?", etc.
          - AFTER `lark_auth_complete` returned success, as a final sanity
            check — not INSTEAD of it.
          - When the status matrix shows Step 2 (OAuth) as ❌ and NO
            pending device_code exists — this tool self-heals the DB
            state if the CLI says the user is logged in.

        **DO NOT CALL PREEMPTIVELY**: never before every `lark_cli` — that
        wastes a round-trip. Trust the normal error paths in typical use.

        **AFTER RETURN**:
          - `auth.status` is one of `not_logged_in` / `bot_ready` /
            `user_logged_in` / `expired`. If `expired` → call `lark_auth`
            to re-authorize.
          - `doctor` fields show network / CLI / config issues. Surface
            any problems to the user in plain language — don't silently
            retry on network errors.
          - `receive_enabled=false` → the bot can SEND messages but WON'T
            auto-reply to incoming Lark messages. Guide the user through
            `lark_enable_receive` (paste App Secret from `dev_console_url`).

        Args:
            agent_id: The agent to check.

        Returns:
            {"success": True, "data": {"auth": {...}, "doctor": {...},
             "receive_enabled": bool, "dev_console_url": "..."}}.
        """
        cred = await _get_credential(agent_id)
        if not cred:
            return {"success": False, "error": "No Lark bot bound."}

        auth = await _cli._run_with_agent_id(["auth", "status"], agent_id)
        doctor = await _cli._run_with_agent_id(["doctor"], agent_id)

        # Self-healing: if the CLI says a user token is valid but our DB
        # still has user_oauth_completed_at=None (because auth_complete was
        # never called or the call landed on an already-consumed code),
        # trust the CLI and flip the state forward.
        auth_data = auth.get("data", {}) if auth.get("success") else {}
        cli_user_logged_in = (
            auth_data.get("identity") == "user"
            or auth_data.get("tokenType") == "user"
            or auth_data.get("tokenStatus") == "valid"
            or bool(auth_data.get("users"))
        )
        if cli_user_logged_in and not cred.user_oauth_ok():
            from datetime import datetime, timezone
            now = datetime.now(timezone.utc).isoformat()
            ps = cred.permission_state or {}
            db = await XYZBaseModule.get_mcp_db_client()
            await LarkCredentialManager(db).patch_permission_state(agent_id, {
                "user_oauth_completed_at": now,
                "user_oauth_url": None,
                "user_oauth_device_code": None,
                "user_oauth_mode": None,
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
            # Re-read cred so the response reflects reality
            cred = await _get_credential(agent_id)

        return {
            "success": True,
            "data": {
                "auth": auth.get("data", {}),
                "doctor": doctor.get("data", {}),
                "receive_enabled": cred.receive_enabled(),
                "user_oauth_ok": cred.user_oauth_ok(),
                "console_setup_ok": cred.console_setup_ok(),
                "permission_state": cred.permission_state,
                "dev_console_url": _dev_console_url(cred.brand, cred.app_id),
                "profile_name": cred.profile_name,
                "app_id": cred.app_id,
                "brand": cred.brand,
            },
        }

    # =====================================================================
    # Lifecycle: lark_enable_receive
    # =====================================================================

    @mcp.tool()
    async def lark_enable_receive(agent_id: str, app_secret: str) -> dict:
        """
        Enable real-time message RECEIVING by storing the bot's App Secret.

        Without this step, the bot can still SEND messages (via `lark_cli`)
        but **will not automatically reply** to incoming Lark messages —
        the backend subscriber needs the plain App Secret to initialize the
        Lark SDK WebSocket client (which cannot read from the keychain).

        **WHEN TO CALL**: after `lark_setup` completes AND the user pastes
        you the App Secret from the Lark developer console. Agent-assisted
        setups leave `receive_enabled=false` in `lark_status` until this
        runs.

        **BEFORE CALLING — GUIDE THE USER**:
          1. Call `lark_status(agent_id)` to get `dev_console_url`.
          2. Send the URL to the user and say exactly:
              "Open this link, go to 'Credentials & Basic Info', and
               copy the App Secret back to me. Without this I can't
               auto-reply when you receive Lark messages — I can only
               send when you ask."
          3. Wait for the user to paste the secret.
          4. Call this tool with the pasted value.

        **AFTER RETURN**:
          - `success=True` → tell the user "Real-time replies will be live
            within ~10 seconds." (The watcher polls every 10s.)
          - Call `lark_status` again after 15s if the user wants confirmation.

        **FAILURE**:
          - "No Lark bot bound" → they need `lark_setup` first.
          - Empty / whitespace secret → tell user to re-copy.

        Args:
            agent_id: The agent whose bot to enable receive for.
            app_secret: The plain App Secret the user pasted from the Lark
                        developer console. Stored base64-encoded in DB (not
                        encrypted — treat DB access as trusted).

        Returns:
            {"success": True, "data": {"message": "..."}} or error.
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

    # =====================================================================
    # Skill doc loader
    # =====================================================================

    @mcp.tool()
    async def lark_skill(agent_id: str, name: str) -> dict:
        """
        Load the SKILL.md knowledge doc for a Lark CLI domain.

        **WHEN TO CALL**: Before using a Lark domain you haven't used in this
        session. The SKILL.md teaches you correct command syntax, identity
        rules (--as user vs --as bot), ID types (open_id / chat_id / user_id),
        and common gotchas. Reading it first prevents multi-turn trial-and-error.

        **RECOMMENDED FIRST CALL**: lark_skill(agent_id, "lark-shared") —
        covers authentication, permission handling, and --as user/bot rules
        that apply to all other domains.

        **AVAILABLE SKILLS** (call this tool to load any):
        - lark-shared: authentication, permissions, --as user/bot (read first)
        - lark-im: messaging — send/reply/search messages, manage chats
        - lark-contact: people search by email / name / phone
        - lark-calendar: agenda, create events, free/busy query
        - lark-doc: create and edit Lark docs
        - lark-sheets: spreadsheets read/write
        - lark-drive: file upload/download, folder management
        - lark-mail: email draft/compose/send/reply/search
        - lark-task: todo and checklist management
        - lark-wiki: knowledge space navigation
        - lark-vc: video meeting recordings and summaries
        - lark-minutes: meeting minutes AI summaries
        - lark-base: multi-dimensional tables (Base)
        - lark-event: realtime event subscription
        - lark-whiteboard: charts / flowcharts / mindmaps
        - lark-workflow-meeting-summary / lark-workflow-standup-report
        - lark-openapi-explorer / lark-skill-maker (advanced)

        **FAILURE**: unknown skill name → returns the available list so you
        can pick a valid one.

        Args:
            agent_id: The agent performing this action. Kept for API
                      consistency with other Lark tools; skill content is
                      the same across all agents.
            name: Skill name without the "lark-" requirement-free form.
                  Accepts either "lark-im" or "im". See list above.

        Returns:
            {"success": True, "name": "lark-im", "content": "<markdown>"} or
            {"success": False, "error": "...", "available": ["lark-im", ...]}.
        """
        from ._lark_skill_loader import get_available_skills, load_skill_content
        # Accept both "im" and "lark-im" forms
        normalized = name if name.startswith("lark-") else f"lark-{name}"
        content = load_skill_content(normalized)
        if not content:
            return {
                "success": False,
                "error": f"Skill '{normalized}' not found.",
                "available": get_available_skills(),
            }
        return {"success": True, "name": normalized, "content": content}

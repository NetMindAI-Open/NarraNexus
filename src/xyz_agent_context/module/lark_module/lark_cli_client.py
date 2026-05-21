"""
@file_name: lark_cli_client.py
@date: 2026-04-10
@description: Unified wrapper for all lark-cli subprocess calls.

Design
------
DB is the single source of truth for every bot credential (app_id,
app_secret, profile name, brand). The workspace (a per-agent directory
used as HOME) is a derived view that the CLI reads — if it's missing or
stale it gets rebuilt from DB before the next command runs.

Flow for any agent-scoped command:
  _run_with_agent_id(args, agent_id)
    → fetch cred from DB
    → (lazy migration) if workspace_path is empty, compute + persist it
    → _ensure_hydrated(cred): rewrite workspace/.lark-cli/config.json
      from DB via `lark-cli config init --app-secret-stdin` if stale
    → subprocess lark-cli with HOME=workspace (no --profile needed —
      each workspace has exactly one active profile)

The old shared `~/.lark-cli/config.json` + `--profile` multiplexing was
retired in favour of one workspace per agent. This mirrors how a single-
machine user runs lark-cli: one HOME, one profile, no flags.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from loguru import logger


class LarkCLIClient:
    """Async wrapper around lark-cli subprocess calls."""

    # =========================================================================
    # Routing entrypoint
    # =========================================================================

    async def _run_with_agent_id(
        self,
        args: list[str],
        agent_id: str,
        stdin_data: str = "",
        timeout: float = 60.0,
    ) -> dict:
        """Single routing entrypoint for every agent-scoped lark-cli call.

        Looks up the credential, ensures the workspace is hydrated from DB
        (creating or rebuilding config.json if needed), and runs the CLI
        with HOME=workspace.

        Special case: `config init --new` (interactive app creation) is
        called BEFORE the credential exists, so it bypasses hydration and
        goes straight to `_run_with_home`.
        """
        is_init_new = (
            len(args) >= 3
            and args[0] == "config"
            and args[1] == "init"
            and "--new" in args
        )
        if is_init_new:
            return await self._run_with_home(args, agent_id, stdin_data, timeout)

        from xyz_agent_context.module.base import XYZBaseModule
        from ._lark_credential_manager import LarkCredentialManager
        from ._lark_workspace import get_home_env, get_workspace_path

        db = await XYZBaseModule.get_mcp_db_client()
        mgr = LarkCredentialManager(db)
        cred = await mgr.get_credential(agent_id)
        if not cred:
            return {
                "success": False,
                "error": f"No Lark credential for agent {agent_id}. Run lark_setup first.",
            }

        # Lazy migration: pre-refactor manual binds had workspace_path=""
        if not cred.workspace_path:
            cred.workspace_path = str(get_workspace_path(agent_id))
            await mgr.update_workspace_path(agent_id, cred.workspace_path)

        hydrated, err = await self._ensure_hydrated(cred)
        if not hydrated:
            return {"success": False, "error": err}

        env = get_home_env(agent_id)
        cmd = ["lark-cli"] + args
        logger.debug(f"lark-cli [{agent_id}/{cred.profile_name}]: {' '.join(cmd)}")
        return await self._exec_lark_cli(cmd, stdin_data, timeout, env=env)

    # =========================================================================
    # Hydration: reconcile workspace config.json with DB
    # =========================================================================

    async def _ensure_hydrated(self, cred) -> tuple[bool, str]:
        """Make sure workspace/.lark-cli/config.json reflects the DB cred.

        Idempotent: if the workspace already holds a config entry for
        cred.app_id, it's a no-op. Otherwise we rebuild via `config init
        --app-id X --app-secret-stdin --name ...` with HOME=workspace.

        Returns (success, error_msg). Failure reasons:
          - No plain secret in DB (agent-assisted pre-enable) AND no
            existing workspace config (fresh machine after DB migration).
            → Tell the caller so it can surface the error to the user
              ("please paste your App Secret via lark_enable_receive").
        """
        from ._lark_workspace import ensure_workspace, get_home_env

        workspace = Path(cred.workspace_path)
        config_path = workspace / ".lark-cli" / "config.json"

        # Already up to date?
        if config_path.is_file():
            try:
                current = json.loads(config_path.read_text(encoding="utf-8"))
                apps = current.get("apps", [])
                if any(a.get("appId") == cred.app_id for a in apps):
                    return True, ""
            except (json.JSONDecodeError, OSError):
                pass  # corrupted or unreadable → rebuild

        # Need to hydrate. We need the plain secret from DB.
        plain_secret = cred.get_app_secret()
        if not plain_secret:
            return False, (
                "Workspace config missing and DB has no plain App Secret. "
                "If this is an agent-assisted setup, the user needs to complete "
                "`lark_enable_receive` once to unlock both trigger AND CLI. "
                "If this is a manual bind, re-bind via frontend LarkConfig."
            )

        ensure_workspace(cred.agent_id)
        env = get_home_env(cred.agent_id)
        cmd = [
            "lark-cli", "config", "init",
            "--app-id", cred.app_id,
            "--app-secret-stdin",
            "--brand", cred.brand,
            "--name", cred.profile_name,
        ]
        logger.info(
            f"Hydrating workspace for {cred.agent_id} "
            f"(app_id={cred.app_id}, profile={cred.profile_name})"
        )
        result = await self._exec_lark_cli(cmd, stdin_data=plain_secret, timeout=30.0, env=env)
        if result.get("success"):
            return True, ""
        return False, f"Workspace hydration failed: {result.get('error', 'unknown')}"

    # =========================================================================
    # Direct HOME-only runner (for config init --new)
    # =========================================================================

    async def _run_with_home(
        self,
        args: list[str],
        agent_id: str,
        stdin_data: str = "",
        timeout: float = 60.0,
    ) -> dict:
        """Run lark-cli with HOME=workspace, no hydration, no --profile.

        Only used for `config init --new` inside lark_setup, which creates
        the credential itself — there's nothing to hydrate from yet.
        """
        from ._lark_workspace import get_home_env

        env = get_home_env(agent_id)
        cmd = ["lark-cli"] + args
        logger.debug(f"lark-cli HOME [{agent_id}]: {' '.join(cmd)}")
        return await self._exec_lark_cli(cmd, stdin_data, timeout, env=env)

    # =========================================================================
    # Shared subprocess + JSON parser
    # =========================================================================

    async def _exec_lark_cli(
        self,
        cmd: list[str],
        stdin_data: str,
        timeout: float,
        env: dict | None = None,
    ) -> dict:
        """Spawn lark-cli, collect stdout, parse JSON, handle errors."""
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                stdin=asyncio.subprocess.PIPE if stdin_data else None,
                env=env,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(input=stdin_data.encode() if stdin_data else None),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            try:
                proc.kill()
                await proc.wait()
            except (ProcessLookupError, OSError):
                pass
            return {"success": False, "error": f"CLI command timed out after {timeout}s"}
        except FileNotFoundError:
            return {"success": False, "error": "lark-cli not found. Install: npm install -g @larksuite/cli"}

        stdout_str = stdout.decode().strip()
        stderr_str = stderr.decode().strip()

        if proc.returncode != 0:
            error_msg = stderr_str or stdout_str or f"CLI exited with code {proc.returncode}"
            error_data: dict = {}
            try:
                parsed = json.loads(stdout_str)
                if isinstance(parsed, dict) and "error" in parsed:
                    err = parsed["error"]
                    error_msg = err.get("message", error_msg)
                    if "console_url" in err:
                        error_msg += f"\n\nEnable permission here: {err['console_url']}"
                    error_data = err
            except (json.JSONDecodeError, AttributeError):
                pass
            return {"success": False, "error": error_msg, "error_data": error_data}

        try:
            data = json.loads(stdout_str) if stdout_str else {}
        except json.JSONDecodeError:
            data = {"raw_output": stdout_str}

        return {"success": True, "data": data}

    # =========================================================================
    # Lifecycle
    # =========================================================================

    async def profile_remove(self, agent_id: str) -> dict:
        """Remove the CLI profile + keychain entry for an agent.

        Runs `lark-cli profile remove` with HOME=workspace so the CLI
        cleans its own config.json + the keychain reference it owns.
        The workspace directory itself is the caller's responsibility
        (e.g. delete_agent uses shutil.rmtree).
        """
        from xyz_agent_context.module.base import XYZBaseModule
        from ._lark_credential_manager import LarkCredentialManager
        from ._lark_workspace import get_home_env, get_workspace_path

        db = await XYZBaseModule.get_mcp_db_client()
        cred = await LarkCredentialManager(db).get_credential(agent_id)
        if not cred:
            return {"success": False, "error": "No Lark credential for this agent."}

        workspace = Path(cred.workspace_path or str(get_workspace_path(agent_id)))
        if not (workspace / ".lark-cli" / "config.json").is_file():
            # Nothing to remove — workspace already empty or never hydrated
            return {"success": True, "data": {"message": "no workspace to clean"}}

        env = get_home_env(agent_id)
        cmd = ["lark-cli", "profile", "remove", cred.profile_name]
        return await self._exec_lark_cli(cmd, stdin_data="", timeout=30.0, env=env)

    # =========================================================================
    # Business methods — all agent_id-scoped, route via _run_with_agent_id
    # =========================================================================

    async def get_user(
        self, agent_id: str, user_id: str = "", identity: str = "user"
    ) -> dict:
        """Get a user's contact info (display name, etc.).

        Defaults to `--as user` — the agent owner's authorized user token,
        stored per-agent in the isolated workspace HOME. We need it because
        the bot tenant token lacks `contact:user.base:readonly`, so
        `--as bot` only ever returns open_id/union_id with no name. Pass
        `identity="bot"` for an app-identity lookup. Omit user_id to read
        the calling identity's own info.
        """
        args = ["contact", "+get-user", "--as", identity]
        if user_id:
            args.extend(["--user-id", user_id])
        return await self._run_with_agent_id(args, agent_id)

    async def send_message(
        self,
        agent_id: str,
        chat_id: str = "",
        user_id: str = "",
        text: str = "",
        markdown: str = "",
    ) -> dict:
        """Send a message to a chat or user."""
        args = ["im", "+messages-send"]
        if chat_id:
            args.extend(["--chat-id", chat_id])
        elif user_id:
            args.extend(["--user-id", user_id])
        if text:
            args.extend(["--text", text])
        elif markdown:
            args.extend(["--markdown", markdown])
        return await self._run_with_agent_id(args, agent_id)

    async def list_chat_messages(
        self,
        agent_id: str,
        chat_id: str = "",
        user_id: str = "",
        limit: int = 20,
    ) -> dict:
        """List recent messages in a chat or P2P conversation."""
        args = ["im", "+chat-messages-list", "--as", "bot"]
        if chat_id:
            args.extend(["--chat-id", chat_id])
        elif user_id:
            args.extend(["--user-id", user_id])
        args.extend(["--page-size", str(limit)])
        return await self._run_with_agent_id(args, agent_id)

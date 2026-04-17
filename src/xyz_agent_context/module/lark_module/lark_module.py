"""
@file_name: lark_module.py
@date: 2026-04-10
@description: LarkModule — Lark/Feishu integration module.

Provides MCP tools for messaging, contacts, docs, calendar, and tasks.
Each agent can bind its own Lark bot via CLI --profile isolation.

Instance level: Agent-level (one per Agent, enabled when bot is bound).
"""

from __future__ import annotations

from typing import Any, Optional

from loguru import logger

from xyz_agent_context.module.base import XYZBaseModule
from xyz_agent_context.channel.channel_sender_registry import ChannelSenderRegistry
from xyz_agent_context.schema import (
    ModuleConfig,
    MCPServerConfig,
    ContextData,
    HookAfterExecutionParams,
    WorkingSource,
)

from ._lark_credential_manager import LarkCredentialManager
from .lark_cli_client import LarkCLIClient


# MCP server port — must not conflict with other modules
# MessageBusModule: 7820, JobModule: 7803
LARK_MCP_PORT = 7830

# Shared CLI client (stateless)
_cli = LarkCLIClient()


async def _lark_send_to_agent(
    agent_id: str, target_id: str, message: str, **kwargs
) -> dict:
    """
    Channel sender function registered in ChannelSenderRegistry.
    Allows other modules to send Lark messages on behalf of an agent.
    """
    db = await XYZBaseModule.get_mcp_db_client()
    mgr = LarkCredentialManager(db)
    cred = await mgr.get_credential(agent_id)
    if not cred:
        return {"success": False, "error": "No Lark bot bound to this agent."}
    return await _cli.send_message(agent_id, user_id=target_id, text=message)


class LarkModule(XYZBaseModule):
    """
    Lark/Feishu integration module.

    Enables agents to interact with Lark: search contacts, send messages,
    create documents, manage calendar events, and handle tasks.
    """

    _sender_registered = False

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not LarkModule._sender_registered:
            ChannelSenderRegistry.register("lark", _lark_send_to_agent)
            LarkModule._sender_registered = True

    # =========================================================================
    # Configuration
    # =========================================================================

    @staticmethod
    def get_config() -> ModuleConfig:
        return ModuleConfig(
            name="LarkModule",
            priority=6,
            enabled=True,
            description=(
                "Lark/Feishu integration: search colleagues, send messages, "
                "create documents, manage calendar, and handle tasks."
            ),
            module_type="capability",
        )

    # =========================================================================
    # MCP Server
    # =========================================================================

    async def get_mcp_config(self) -> Optional[MCPServerConfig]:
        return MCPServerConfig(
            server_name="lark_module",
            server_url=f"http://localhost:{LARK_MCP_PORT}/sse",
            type="sse",
        )

    def create_mcp_server(self) -> Optional[Any]:
        try:
            # Use the official mcp SDK's FastMCP — same as every other module
            # in this project. The standalone `fastmcp` v2 package has a
            # different Settings schema (no transport_security field), which
            # made module_runner._run_mcp_in_thread crash the LarkModule MCP
            # thread at startup and silently disable the whole lark flow.
            from mcp.server.fastmcp import FastMCP

            mcp = FastMCP("LarkModule MCP")
            mcp.settings.port = LARK_MCP_PORT

            from ._lark_mcp_tools import register_lark_mcp_tools
            register_lark_mcp_tools(mcp)

            logger.info(f"LarkModule MCP server created on port {LARK_MCP_PORT}")
            return mcp
        except Exception as e:
            logger.error(f"Failed to create LarkModule MCP server: {e}")
            return None

    # =========================================================================
    # Instructions
    # =========================================================================

    async def get_instructions(self, ctx_data: ContextData) -> str:
        """Dynamic instructions with Configuration Status matrix every turn.

        The Agent always sees:
          1. Mode (LARK CHANNEL vs OWNER CHAT)
          2. Configuration Status matrix — 5 binary checks
          3. Explicit "next step" instruction pointing at the right MCP tool
             whenever any check is ❌
          4. How to learn lark-cli syntax (lark_skill MCP tool)
          5. Iron rules (MCP-only, never Bash)
        """
        lark_info = ctx_data.extra_data.get("lark_info")

        if not lark_info:
            return (
                "## Lark/Feishu Integration\n\n"
                "**No Lark bot bound to this agent.**\n\n"
                "If the user wants to connect Lark/Feishu, call "
                "`mcp__lark_module__lark_setup(agent_id, brand, owner_email)`. "
                "Always ASK the user whether they use Feishu (飞书) or Lark "
                "International before calling — do not silently default.\n\n"
                "**IMPORTANT**: never use Bash to run `lark-cli`, `npm install`, "
                "or `clawhub install` — all Lark functionality goes through "
                "`mcp__lark_module__*` tools. The hook guard will block shell "
                "calls containing `lark-cli` and tell you which MCP tool to use.\n"
            )

        brand_display = "Feishu" if lark_info.get("brand") == "feishu" else "Lark"
        bot_name = lark_info.get("bot_name") or "(name pending)"
        app_id = lark_info.get("app_id", "")
        auth = lark_info.get("auth_status", "not_logged_in")

        if auth in ("not_logged_in", "expired"):
            return (
                f"## Lark/Feishu Integration\n\n"
                f"Bot **{bot_name}** ({brand_display}) is bound but credentials are "
                f"{'expired' if auth == 'expired' else 'not active'}. "
                f"Ask the user to re-bind via the frontend LarkConfig panel, "
                f"or call `mcp__lark_module__lark_setup` to create a fresh app."
            )

        # --- Mode indicator ------------------------------------------------
        ws = ctx_data.working_source
        is_lark_channel = (
            ws == WorkingSource.LARK
            or (isinstance(ws, str) and ws == WorkingSource.LARK.value)
        )
        logger.info(f"LarkModule.get_instructions: working_source={ws!r}, is_lark_channel={is_lark_channel}")

        if is_lark_channel:
            mode_section = (
                "**Mode: LARK CHANNEL** — you are handling an incoming Lark message. "
                "Reply via `mcp__lark_module__lark_cli(agent_id, command=\"im +messages-send ...\")`.\n\n"
            )
        else:
            mode_section = (
                "**Mode: OWNER CHAT** — you are in the owner's direct chat window. "
                "Reply as normal text. Do NOT call `im +messages-send` — that goes "
                "to Lark users, not to the owner's chat.\n\n"
            )

        # --- Configuration Status matrix ----------------------------------
        app_created = bool(app_id) and app_id != "pending_setup"
        user_oauth_ok = bool(lark_info.get("user_oauth_ok"))
        bot_scopes_ok = bool(lark_info.get("bot_scopes_confirmed"))
        availability_ok = bool(lark_info.get("availability_confirmed"))
        receive_ok = bool(lark_info.get("receive_enabled"))
        pending_oauth_url = lark_info.get("pending_oauth_url", "")
        pending_device_code = lark_info.get("pending_oauth_device_code", "")

        def _tick(ok: bool) -> str:
            return "✅" if ok else "❌"

        # OAuth cell uses ⏳ when a URL is outstanding
        oauth_cell = (
            "✅" if user_oauth_ok
            else ("⏳ awaiting user click" if pending_oauth_url else "❌")
        )

        # Availability is OPTIONAL — if the user skips it, the bot still
        # works; only they can see/use it. Render as "optional" when
        # unconfirmed so it doesn't look like a blocker.
        availability_cell = "✅" if availability_ok else "➖ optional (bot stays private)"

        status_matrix = (
            "### Configuration Status (check every turn before acting)\n\n"
            "| # | Step                                  | State |\n"
            "|---|---------------------------------------|-------|\n"
            f"| 1 | App created                           | {_tick(app_created)} |\n"
            f"| 2 | User OAuth (`auth login --domain all`) | {oauth_cell} |\n"
            f"| 3 | Bot scopes enabled in dev console      | {_tick(bot_scopes_ok)} |\n"
            f"| 4 | Availability = all staff *(optional)* | {availability_cell} |\n"
            f"| 5 | Real-time receive (App Secret in DB)   | {_tick(receive_ok)} |\n\n"
        )

        # --- Next-step coach -----------------------------------------------
        # "Fully configured" does NOT require availability — that step only
        # controls whether other org members can discover/use this bot.
        all_done = app_created and user_oauth_ok and bot_scopes_ok and receive_ok
        if all_done:
            if availability_ok:
                coach = (
                    "**All configured.** Bot is org-visible; you can send, "
                    "receive, and hit every standard API.\n\n"
                )
            else:
                coach = (
                    "**All required steps done.** Bot works fully for the owner. "
                    "If the user wants OTHER people in their org to be able to "
                    "see/use this bot, remind them to set availability to "
                    "'全员可用' (or specific departments) in the dev console "
                    "and publish a new version — without that step, only the "
                    "owner can see the bot. Call "
                    "`mcp__lark_module__lark_mark_console_done(agent_id, "
                    "availability_ok=True)` after they confirm.\n\n"
                )
        else:
            steps: list[str] = []
            if not user_oauth_ok and not pending_oauth_url:
                steps.append(
                    "- Step 2 + 3 (permission bootstrap): call "
                    "`mcp__lark_module__lark_configure_permissions(agent_id)` "
                    "to get ONE OAuth URL covering all user scopes plus the "
                    "console checklist for bot scopes."
                )
            if pending_oauth_url and not user_oauth_ok:
                steps.append(
                    f"- Step 2 (OAuth pending): ask the user to click this link: "
                    f"`{pending_oauth_url}` — when they confirm, call "
                    f"`mcp__lark_module__lark_auth_complete(agent_id, device_code=\"{pending_device_code}\")`."
                )
            if user_oauth_ok and not bot_scopes_ok:
                steps.append(
                    "- Step 3 (console checklist): remind the user to finish "
                    "the dev-console task — enable bot scopes and publish a "
                    "version. Availability (step 4) is optional; mention it "
                    "only if they want other org members to see the bot. "
                    "When they confirm scopes are done, call "
                    "`mcp__lark_module__lark_mark_console_done(agent_id, "
                    "bot_scopes_ok=True, availability_ok=<True if they set it, else False>)`."
                )
            if not receive_ok:
                if lark_info.get("is_agent_assisted"):
                    brand_key = lark_info.get("brand", "lark")
                    console = (
                        f"https://open.feishu.cn/app/{app_id}" if brand_key == "feishu"
                        else f"https://open.larksuite.com/app/{app_id}"
                    )
                    steps.append(
                        f"- Step 5 (real-time receive): this bot can SEND but "
                        f"can't auto-reply. Ask the user to open {console} → "
                        f"'Credentials & Basic Info' → copy App Secret → paste "
                        f"it back. Call "
                        f"`mcp__lark_module__lark_enable_receive(agent_id, app_secret=\"...\")`."
                    )
                else:
                    steps.append(
                        "- Step 5 (real-time receive): App Secret missing in DB. "
                        "Re-bind via the frontend LarkConfig panel."
                    )
            coach = (
                "**Not fully configured yet.** Next actions (in order):\n"
                + "\n".join(steps)
                + "\n\n**Do these proactively** when the user asks about Lark "
                "setup OR when a command hits a permission error. Don't wait "
                "for the user to guess what's missing.\n\n"
            )

        # --- How to use lark_cli + skill discovery ------------------------
        try:
            from ._lark_skill_loader import get_available_skills
            available = get_available_skills()
        except Exception:
            available = []

        if available:
            skill_list = ", ".join(f"`{s}`" for s in available)
            skill_section = (
                "### How to drive `lark_cli`\n\n"
                "All Lark operations route through `mcp__lark_module__lark_cli(agent_id, command=\"...\")`. "
                "Do NOT add `--profile` or `--format json` (isolation is automatic, `+` "
                "commands reject `--format`).\n\n"
                "**Before using any Lark domain you haven't used this session**, call "
                "`mcp__lark_module__lark_skill(agent_id, name=\"<domain>\")` to load its "
                "SKILL.md. The SKILL.md teaches correct syntax, identity rules (`--as user` "
                "vs `--as bot`), and gotchas — skipping it wastes turns on trial-and-error.\n"
                f"- **Recommended FIRST call** each session: `lark_skill(agent_id, \"lark-shared\")` "
                f"(auth + permission handling + `--as` rules that apply everywhere).\n"
                f"- Available domain skills: {skill_list}\n"
                f"- Example: `lark_skill(agent_id, \"lark-im\")` before any `im +...`.\n"
                "- Quick runtime help inside `lark_cli`: `<domain> +<command> --help`.\n"
                "- API field lookup inside `lark_cli`: `schema <resource>` "
                "(e.g. `schema im.messages.create`).\n\n"
            )
        else:
            skill_section = (
                "### How to drive `lark_cli`\n\n"
                "All Lark operations route through `mcp__lark_module__lark_cli(agent_id, "
                "command=\"...\")`. Do NOT add `--profile` or `--format json`.\n\n"
                "No SKILL docs are installed locally. Use `<domain> +<command> --help` "
                "and `schema <resource>` inside `lark_cli` for reference.\n\n"
            )

        # --- Owner identity -----------------------------------------------
        owner_section = ""
        owner_id = lark_info.get("owner_open_id", "")
        owner_name = lark_info.get("owner_name", "")
        if owner_id:
            owner_section = (
                f"\n**Owner**: {owner_name} (open_id: `{owner_id}`). "
                f"When the user says \"me/my/I\" in Lark context → this person.\n\n"
            )

        # --- Iron rules ---------------------------------------------------
        rules = (
            "### Iron rules (non-negotiable)\n\n"
            "1. **MCP only — NEVER Bash**. `lark-cli` invocations via Bash/shell "
            "are intercepted by the hook guard and returned as an error. All "
            "Lark work goes through `mcp__lark_module__*` tools:\n"
            "   - `lark_cli` for any CLI-backed operation\n"
            "   - `lark_setup`, `lark_configure_permissions`, `lark_auth_complete`, "
            "`lark_mark_console_done`, `lark_enable_receive` for lifecycle\n"
            "   - `lark_status` for health checks, `lark_skill` for docs\n"
            "2. **Never run `npm install`, `clawhub install`, or any package "
            "installer** via Bash to 'get Lark working' — the stack is already "
            "installed. If an MCP tool fails, report the error; don't improvise "
            "a workaround.\n"
            "3. **Identity: always add `--as bot`** when sending messages, "
            "creating docs, or performing actions. Use `--as user` only for "
            "search/read that requires user identity "
            "(e.g. `contact +search-user`, `docs +search`).\n"
            "4. **Permission errors drive `lark_auth`** for specific scopes — "
            "do NOT preemptively call `lark_auth`. The generic permission "
            "bootstrap happens once via `lark_configure_permissions`.\n"
            "5. `im +messages-send` sends to a Lark user/chat — it is NOT how "
            "you reply to the owner's chat window.\n"
        )

        header = f"**Bot**: **{bot_name}** ({brand_display}, app `{app_id}`)."
        return (
            "## Lark/Feishu Integration\n\n"
            f"{mode_section}"
            f"{header}\n"
            f"{owner_section}"
            f"{status_matrix}"
            f"{coach}"
            f"{skill_section}"
            f"{rules}"
        )

    # =========================================================================
    # Hooks
    # =========================================================================

    async def hook_data_gathering(self, ctx_data: ContextData) -> ContextData:
        """Inject Lark bot info + permission_state so get_instructions can
        render a complete Configuration Status matrix every turn."""
        try:
            mgr = LarkCredentialManager(self.db)
            cred = await mgr.get_credential(self.agent_id)
            if cred and cred.is_active:
                ps = cred.permission_state or {}
                lark_info = {
                    "app_id": cred.app_id,
                    "brand": cred.brand,
                    "bot_name": cred.bot_name,
                    "auth_status": cred.auth_status,
                    "profile_name": cred.profile_name,
                    "is_agent_assisted": bool(cred.workspace_path),
                    # Derived booleans — get_instructions renders these as
                    # ticks/crosses in the status matrix.
                    "receive_enabled": cred.receive_enabled(),
                    "user_oauth_ok": cred.user_oauth_ok(),
                    "console_setup_ok": cred.console_setup_ok(),
                    "bot_scopes_confirmed": bool(ps.get("bot_scopes_confirmed")),
                    "availability_confirmed": bool(ps.get("availability_confirmed")),
                    # Pending OAuth kept around so instructions can prompt
                    # the user to finish clicking the URL if one is live.
                    "pending_oauth_url": ps.get("user_oauth_url") or "",
                    "pending_oauth_device_code": ps.get("user_oauth_device_code") or "",
                }
                if cred.owner_open_id:
                    lark_info["owner_open_id"] = cred.owner_open_id
                    lark_info["owner_name"] = cred.owner_name
                ctx_data.extra_data["lark_info"] = lark_info
        except Exception as e:
            logger.warning(f"LarkModule hook_data_gathering failed: {e}")
        return ctx_data

    async def hook_after_event_execution(
        self, params: HookAfterExecutionParams
    ) -> None:
        """Post-execution cleanup for Lark-triggered executions."""
        # Only process Lark-triggered executions
        ws = params.execution_ctx.working_source
        # working_source can be either the enum or its string value
        if str(ws) != WorkingSource.LARK.value:
            return
        # Future: mark messages as read, update sync state, etc.
        logger.debug(f"LarkModule after_execution for agent {params.execution_ctx.agent_id}")

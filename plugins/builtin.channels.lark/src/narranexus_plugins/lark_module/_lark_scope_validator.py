"""
@file_name: _lark_scope_validator.py
@date: 2026-05-27
@description: Check whether a freshly-bound Lark app actually has the
permission scopes NarraNexus needs, instead of letting the user discover
gaps the hard way (silent message-receive failure, "Unknown" sender names,
etc.).

Why this exists: previously `do_bind` only verified that the App ID +
Secret could mint a tenant_access_token. That tells us the credentials
are real — it does NOT tell us whether the app has the scopes we use
(`im:message` to receive, `im:message:send_as_bot` to reply,
`contact:user.base:readonly` to resolve sender names, etc.). Users would
bind successfully, then discover bots silently failing when messages
arrived or sender names showed as "Unknown". The investigation report
(2026-05-27) flagged this as the highest-frequency confusing failure
besides event-subscription-not-enabled.

Lookup mechanism: `lark-cli auth scopes --format json` returns the scopes
the app actually has enabled on the developer console (both bot- and
user-token sides). We compare against required + optional lists, return
a structured report, and let `do_bind` decide whether to block or warn.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any

from loguru import logger


# ── Scope policy ───────────────────────────────────────────────────────────
# REQUIRED scopes: the bot is unusable without them. bind() should refuse.
# OPTIONAL scopes: the bot works but a specific feature is degraded. bind()
# should succeed and surface a warning so the user knows to enable later.
#
# Sourced from `_lark_mcp_tools.py:_RECOMMENDED_BOT_SCOPES` + comments in
# `lark_cli_client.py:279` (the contact: scope must use the user token,
# not the bot token, because bot tokens lack contact read).

REQUIRED_BOT_SCOPES: set[str] = {
    "im:message",                # receive inbound chat messages (WS events)
    "im:message:send_as_bot",    # send replies as the bot identity
    "im:resource",               # message attachments (files, images)
    "im:chat",                   # read chat metadata (members, names)
    "im:chat:readonly",          # message history retrieval
}

REQUIRED_USER_SCOPES: set[str] = {
    # The Xinyao-pain scope: without this, every sender shows as "Unknown"
    # and the agent hallucinates identities. Strictly speaking the bot can
    # still receive + reply, but the UX is so degraded that we treat it
    # as required.
    "contact:user.base:readonly",
}

# Optional scopes — warn but don't block. Add new ones here as the platform
# grows new integrations (docs / drive / calendar / wiki / sheets).
OPTIONAL_SCOPES: set[str] = {
    "contact:user.email:readonly",
    "contact:contact:readonly",
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
}


@dataclass
class ScopeCheckResult:
    """Outcome of comparing the app's actual scopes against the policy."""

    # Scopes we require but the app does NOT have. Non-empty = bind should fail.
    missing_required: list[str] = field(default_factory=list)
    # Scopes we'd like but the app doesn't have. Bind still ok, just warn.
    missing_optional: list[str] = field(default_factory=list)
    # Scopes the app does have (for diagnostics + UI display).
    granted_bot_scopes: list[str] = field(default_factory=list)
    granted_user_scopes: list[str] = field(default_factory=list)
    # True iff the scope check itself ran successfully. False means we
    # couldn't call lark-cli or parse its output; we then SKIP scope
    # enforcement (don't punish the user for our tooling problem) and log
    # a warning.
    check_ran: bool = False
    error: str = ""  # populated when check_ran=False

    @property
    def is_blocking(self) -> bool:
        """True iff bind() should fail (missing required scopes)."""
        return bool(self.missing_required)

    @property
    def has_warnings(self) -> bool:
        """True iff there's something worth telling the user about (optional gaps)."""
        return bool(self.missing_optional)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


async def check_app_scopes(cli, agent_id: str) -> ScopeCheckResult:
    """Run `lark-cli auth scopes --format json` and diff against policy.

    `cli` is the shared `LarkCLIClient` instance — passed in (rather than
    imported globally) to keep this module mockable without monkey-patching
    a module-level singleton.

    Returns ScopeCheckResult; never raises. A failed scope-check (lark-cli
    error, JSON parse error) returns `check_ran=False` so the caller can
    decide whether to fail-open (skip enforcement) or fail-closed (block).
    Current `do_bind` policy is fail-open: missing scopes only block when
    the scope check itself succeeded.
    """
    res = await cli._run_with_agent_id(
        ["auth", "scopes", "--format", "json"],
        agent_id=agent_id,
    )

    if not res.get("success"):
        return ScopeCheckResult(
            check_ran=False,
            error=str(res.get("error", "lark-cli auth scopes failed")),
        )

    data = res.get("data") or {}
    # The CLI's exact JSON shape isn't documented in code; we accept several
    # common shapes defensively. Adapt here if a future lark-cli changes the
    # field names.
    bot_scopes: list[str] = _extract_scope_list(data, ("bot_scopes", "botScopes", "bot"))
    user_scopes: list[str] = _extract_scope_list(data, ("user_scopes", "userScopes", "user"))
    # Some shapes nest everything under a single "scopes" list with type tags.
    if not bot_scopes and not user_scopes:
        flat = data.get("scopes")
        if isinstance(flat, list):
            for item in flat:
                if isinstance(item, dict):
                    scope_name = item.get("scope") or item.get("name") or ""
                    types = item.get("token_types") or item.get("tokenTypes") or []
                    if isinstance(types, list):
                        if "bot" in types and scope_name:
                            bot_scopes.append(scope_name)
                        if "user" in types and scope_name:
                            user_scopes.append(scope_name)
                elif isinstance(item, str):
                    # Treat as bot scope by default — common shorthand
                    bot_scopes.append(item)

    bot_set = set(bot_scopes)
    user_set = set(user_scopes)
    have = bot_set | user_set

    missing_required = sorted((REQUIRED_BOT_SCOPES | REQUIRED_USER_SCOPES) - have)
    missing_optional = sorted(OPTIONAL_SCOPES - have)

    return ScopeCheckResult(
        missing_required=missing_required,
        missing_optional=missing_optional,
        granted_bot_scopes=sorted(bot_set),
        granted_user_scopes=sorted(user_set),
        check_ran=True,
    )


def _extract_scope_list(data: dict, keys: tuple[str, ...]) -> list[str]:
    """Try several common key names; return a flat list of scope strings."""
    for k in keys:
        v = data.get(k)
        if isinstance(v, list):
            out: list[str] = []
            for item in v:
                if isinstance(item, str):
                    out.append(item)
                elif isinstance(item, dict):
                    name = item.get("scope") or item.get("name") or ""
                    if name:
                        out.append(name)
            if out:
                return out
    return []


def format_scope_failure_message(result: ScopeCheckResult, brand: str, app_id: str) -> str:
    """Build a user-friendly message for a blocking scope failure.

    Used by `do_bind` to populate the bind response when
    `result.is_blocking` is True.
    """
    console_base = (
        "https://open.feishu.cn/app" if brand == "feishu"
        else "https://open.larksuite.com/app"
    )
    missing_list = ", ".join(f"`{s}`" for s in result.missing_required)
    return (
        f"Your Lark app is missing required permission scopes: {missing_list}.\n\n"
        f"Open {console_base}/{app_id}/permission, enable each scope above, "
        f"then click 'Create version & publish' so the changes take effect.\n\n"
        f"After publishing, click Bind again to retry."
    )


def format_scope_warning_message(result: ScopeCheckResult) -> str:
    """Build a user-friendly warning for non-blocking missing optional scopes."""
    missing_list = ", ".join(f"`{s}`" for s in result.missing_optional)
    return (
        f"Bot bound successfully. Heads up: these optional scopes are not "
        f"enabled, so the related features won't work: {missing_list}. "
        f"You can enable them later in the developer console if you need them."
    )


# Re-export with debug surface for the diagnostic MCP tool layer.
def get_scope_policy() -> dict[str, list[str]]:
    """Return the full scope policy as JSON-serialisable dict.

    Used by the `lark_diagnose_binding` MCP tool so the agent can tell
    users which scopes are required vs optional vs already-granted.
    """
    return {
        "required_bot_scopes": sorted(REQUIRED_BOT_SCOPES),
        "required_user_scopes": sorted(REQUIRED_USER_SCOPES),
        "optional_scopes": sorted(OPTIONAL_SCOPES),
    }


def _log_unused() -> None:
    # silence the linter for `logger` if module is used without log lines
    logger.debug("lark scope validator module loaded")

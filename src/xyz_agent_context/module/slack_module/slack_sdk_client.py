"""
@file_name: slack_sdk_client.py
@date: 2026-05-08
@description: Thin async wrapper around slack_sdk.web.async_client.AsyncWebClient.

Encapsulates the runtime calls Slack channel code needs:
- auth_test (validate token + discover bot identity)
- send_message (chat.postMessage with optional thread_ts)
- get_user_info (resolve display name)
- get_conversation_history / replies (build context)
- generic api_call (used by `slack_cli` MCP dispatcher)

This is the ONLY module in the package that imports slack_sdk directly.
The trigger and module talk to Slack via this client.
"""

from __future__ import annotations

from typing import Any

from loguru import logger
from slack_sdk.errors import SlackApiError
from slack_sdk.web.async_client import AsyncWebClient

from ._slack_text_sanitizer import sanitize_slack_mrkdwn

# Slack Web API methods whose ``text`` field is user-visible and
# rendered as mrkdwn. We pass these through ``sanitize_slack_mrkdwn``
# on the way out so the agent can't accidentally ship GitHub-style
# ``[text](url)`` links (which render as literal text in Slack) or
# bare URLs adjacent to CJK punctuation (which get absorbed by the
# auto-linker into a broken URL).
_TEXT_BEARING_METHODS: frozenset[str] = frozenset(
    {
        "chat.postMessage",
        "chat.update",
        "chat.postEphemeral",
        "chat.scheduleMessage",
        "chat.meMessage",
    }
)


class SlackSDKError(RuntimeError):
    """Raised when slack_sdk surfaces an API error.

    Carries the upstream `error` code (e.g. ``invalid_auth``,
    ``channel_not_found``) so callers can branch on it without
    parsing strings.
    """

    def __init__(self, code: str, message: str = ""):
        super().__init__(message or code)
        self.code = code


class SlackSDKClient:
    """Async Slack Web API client. One instance per credential / call site."""

    def __init__(self, bot_token: str):
        if not bot_token:
            raise ValueError("bot_token must be provided")
        self._client = AsyncWebClient(token=bot_token)

    @property
    def web(self) -> AsyncWebClient:
        """Expose underlying client for code that needs raw access (e.g.
        Socket Mode requires the same WebClient instance)."""
        return self._client

    async def auth_test(self) -> dict[str, Any]:
        """Validate token + return bot identity (team_id, team, user_id, user)."""
        try:
            resp = await self._client.auth_test()
            return dict(resp.data)
        except SlackApiError as e:
            code = (e.response.get("error") if e.response else "") or "unknown"
            raise SlackSDKError(code, f"auth.test failed: {code}") from e

    async def send_message(
        self,
        channel: str,
        text: str,
        thread_ts: str | None = None,
    ) -> dict[str, Any]:
        """chat.postMessage. Returns full response dict (includes ``ts``).

        ``text`` is run through ``sanitize_slack_mrkdwn`` so agent output
        with GitHub markdown links or CJK-adjacent bare URLs renders
        correctly. Idempotent on already-correct mrkdwn.
        """
        text = sanitize_slack_mrkdwn(text)
        try:
            resp = await self._client.chat_postMessage(
                channel=channel, text=text, thread_ts=thread_ts
            )
            return dict(resp.data)
        except SlackApiError as e:
            code = (e.response.get("error") if e.response else "") or "unknown"
            logger.warning(f"[slack] send_message failed: channel={channel} code={code}")
            raise SlackSDKError(code, f"chat.postMessage failed: {code}") from e

    async def get_user_info(self, user_id: str) -> dict[str, Any]:
        """users.info. Returns the ``user`` sub-object or empty dict."""
        try:
            resp = await self._client.users_info(user=user_id)
            return dict(resp.data.get("user", {}))
        except SlackApiError as e:
            code = (e.response.get("error") if e.response else "") or "unknown"
            logger.warning(f"[slack] users.info failed: user={user_id} code={code}")
            return {}

    async def lookup_user_by_email(self, email: str) -> dict[str, Any]:
        """users.lookupByEmail. Returns the ``user`` sub-object or {}.

        Used at bind time to resolve the owner's Slack identity from their
        email address. Empty dict on miss (email not found / not allowed
        by workspace policy).
        """
        try:
            resp = await self._client.users_lookupByEmail(email=email)
            return dict(resp.data.get("user", {}))
        except SlackApiError as e:
            code = (e.response.get("error") if e.response else "") or "unknown"
            logger.warning(f"[slack] users.lookupByEmail failed: email={email} code={code}")
            return {}

    async def get_conversation_history(
        self, channel: str, limit: int = 20
    ) -> list[dict[str, Any]]:
        """conversations.history. Returned newest-first by Slack — caller
        usually wants to ``reverse`` for chronological order."""
        try:
            resp = await self._client.conversations_history(
                channel=channel, limit=limit
            )
            return list(resp.data.get("messages", []))
        except SlackApiError as e:
            code = (e.response.get("error") if e.response else "") or "unknown"
            logger.warning(f"[slack] conversations.history failed: channel={channel} code={code}")
            return []

    async def get_conversation_replies(
        self, channel: str, ts: str, limit: int = 20
    ) -> list[dict[str, Any]]:
        """conversations.replies — thread context."""
        try:
            resp = await self._client.conversations_replies(
                channel=channel, ts=ts, limit=limit
            )
            return list(resp.data.get("messages", []))
        except SlackApiError as e:
            code = (e.response.get("error") if e.response else "") or "unknown"
            logger.warning(f"[slack] conversations.replies failed: channel={channel} ts={ts} code={code}")
            return []

    async def api_call(self, method: str, args: dict[str, Any]) -> dict[str, Any]:
        """Generic Slack Web API dispatcher.

        Backs the ``slack_cli`` MCP tool so an Agent can call any of the
        ~250 Web API methods without us pre-wrapping each one. Failures
        surface as Slack's native ``{"ok": false, "error": "..."}``
        envelope rather than raising — Agents already know how to read
        the envelope from the per-method skill docs.

        For methods listed in ``_TEXT_BEARING_METHODS`` the ``text``
        arg is run through ``sanitize_slack_mrkdwn`` first — same fix
        as ``send_message`` but applied to the agent-facing path. The
        sanitizer mutates a *copy* of ``args`` so the caller's dict is
        not changed.
        """
        if method in _TEXT_BEARING_METHODS and isinstance(args, dict):
            raw_text = args.get("text")
            if isinstance(raw_text, str) and raw_text:
                args = {**args, "text": sanitize_slack_mrkdwn(raw_text)}
        try:
            resp = await self._client.api_call(method, json=args)
            return dict(resp.data)
        except SlackApiError as e:
            return {
                "ok": False,
                "error": (e.response.get("error") if e.response else "") or "unknown",
                "method": method,
            }
        except Exception as e:  # pragma: no cover — defensive
            logger.exception(f"[slack] api_call({method}) unexpected error")
            return {"ok": False, "error": f"client_exception:{type(e).__name__}", "method": method}

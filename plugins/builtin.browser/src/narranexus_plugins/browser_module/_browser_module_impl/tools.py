"""
@file_name: tools.py
@author:
@date: 2026-09-22
@description: Browser MCP operations bound to the calling turn and conversation.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, Awaitable, Callable, Literal

from loguru import logger
from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult, ContentBlock, ImageContent, TextContent

from narranexus.platform.module_system import parse_bearer_identity


def _error(code: str, message: str) -> dict[str, Any]:
    return {"outcome": "ERROR", "code": code, "message": message}


async def policy_context(service: Any, agent_id: str) -> dict:
    """Describe the owner's configured script permissions."""
    policy = (await service.policy_for(agent_id, fresh=True)).to_dict()
    return {key: policy[key] for key in ("default_origin_policy", "origins")}


def register_tools(mcp: FastMCP, get_service: Callable) -> None:
    """Keep identity, readiness, scope binding and failures common to all tools."""

    async def invoke_scoped(
        agent_id: str,
        operation: Callable[[Any, Any, Any], Awaitable[dict]],
        *,
        create: bool = False,
        requires_session: bool = True,
    ) -> dict[str, Any]:
        try:
            try:
                request = mcp.get_context().request_context.request
                auth = request.headers.get("authorization", "") if request else ""
            except (LookupError, ValueError):
                auth = ""
            identity = parse_bearer_identity(auth)
            # Both adapters receive this bearer; tool arguments never supply
            # the scope, and a cached session is never an identity fallback.
            # The turn/conversation fields are self-declared by the runtime
            # (same trust level as agent_id, not covered by the identity
            # signature): they attribute audit rows and bind login requests and
            # visual observations to the calling turn, and authorize nothing.
            if identity.agent_id != agent_id or not identity.event_id or not identity.thread_id:
                return _error(
                    "missing_caller_scope",
                    "The runtime did not supply this browser call's agent, turn and conversation scope. "
                    "Continue through the platform runtime; do not guess scope identifiers.",
                )
            service = get_service()
            if requires_session:
                refusal = await asyncio.to_thread(service.require_ready)
                if refusal is not None:
                    return refusal
            if create:
                session, refusal = await service.open_session(
                    agent_id, turn_id=identity.event_id, thread_id=identity.thread_id,
                )
                if refusal is not None:
                    return refusal
            else:
                session = service.session_for(agent_id)
            if session is None:
                if requires_session:
                    return _error("session_not_open", "No browser is open. Call browser_open with the requested URL.")
                return await operation(service, None, identity)
            session.bind_scope(turn_id=identity.event_id, thread_id=identity.thread_id)
            result = await operation(service, session, identity)
            return {**result, "session_id": agent_id}
        except Exception as exc:
            logger.exception("Browser tool failed for {}", agent_id)
            return _error("browser_operation_failed", f"Browser operation failed: {exc}")

    async def invoke(agent_id: str, operation: Callable, **options: Any) -> dict[str, Any]:
        # bind_scope uses a ContextVar. The awaited task contains that binding
        # even if a transport reuses its parent task for subsequent tool calls.
        # Cancellation propagates to the operation; nothing runs detached.
        return await asyncio.create_task(invoke_scoped(agent_id, operation, **options))

    @mcp.tool()
    async def browser_status(agent_id: str) -> dict[str, Any]:
        """Check browser runtime readiness and current control ownership."""
        async def status(service, session, identity):
            runtime = (await asyncio.to_thread(service.status)).to_dict()
            refusal = await asyncio.to_thread(service.require_ready)
            result = {**runtime, **(refusal or {"outcome": "OK"})}
            try:
                result["configured_policy"] = await policy_context(service, agent_id)
            except Exception:
                logger.exception("Could not read browser policy for {}", agent_id)
                result["configured_policy"] = {"state": "unknown", "reason": "policy-unavailable"}
            result["profile"] = {"scope": "agent", "persistent": True, "login_state": "unverified"}
            result["session_id"] = agent_id if session else None
            if session:
                result["control"] = session.control.to_dict()
                result.update(session.pages.snapshot())
            return result

        return await invoke(agent_id, status, requires_session=False)

    @mcp.tool()
    async def browser_request_install(agent_id: str) -> dict[str, Any]:
        """Return the user's browser installation action; never start an install."""
        async def install(service, session, identity):
            return await asyncio.to_thread(service.require_ready) or {
                "outcome": "OK", "message": "The browser is already installed.",
            }

        return await invoke(agent_id, install, requires_session=False)

    @mcp.tool()
    async def browser_open(agent_id: str, url: str, new_page: bool = False) -> dict[str, Any]:
        """Open any HTTP(S) URL without site approval.

        By default navigate the active page. Set new_page to preserve it and
        open another tab. The result includes page IDs and the active page.
        """
        async def navigate(service, session, identity):
            return await session.navigate(url, new_page=new_page)

        return await invoke(agent_id, navigate, create=True)

    @mcp.tool()
    async def browser_tabs(
        agent_id: str, action: Literal["list", "select", "close"] = "list", page_id: str | None = None,
    ) -> dict[str, Any]:
        """List all tabs/popups, select a page to operate, or close one by ID.

        List returns titles, URLs, opener IDs and active_page_id even during
        human takeover. Select/close wait for human handback. Get IDs from
        this list, never invent them. Closing the final page leaves a blank tab.
        """
        async def tabs(service, session, identity):
            if action == "list":
                return {"outcome": "OK", **session.pages.snapshot()}
            if not page_id:
                return _error("missing_page_id", "Select or close requires a page_id from browser_tabs.")
            return await (session.select_page(page_id) if action == "select" else session.close_page(page_id))

        return await invoke(agent_id, tabs)

    @mcp.tool()
    async def browser_read(agent_id: str, selector: str | None = None, offset: int = 0) -> dict[str, Any]:
        """Read visible text and actionable selectors, labels, fields and options.

        Narrow to a CSS selector, or continue at data.next_offset using the same
        selector. Offsets count Unicode characters and must be nonnegative safe
        integers. data.total describes the current scope's text, not a frozen
        page; restart at zero after changes. Password values are omitted.
        Use returned selectors with browser_act; reread after the DOM changes.
        """
        async def read(service, session, identity):
            return await session.read_page(selector=selector, offset=offset)

        return await invoke(agent_id, read)

    @mcp.tool()
    async def browser_look(
        agent_id: str, selector: str | None = None, x: float | None = None,
        y: float | None = None, width: float | None = None,
        height: float | None = None, scale: float = 1,
    ) -> CallToolResult:
        """See the current page as an actual image, including pictures, charts and canvas.

        Capture the viewport, one visible CSS selector, or a viewport CSS-pixel
        rectangle (all of x/y/width/height). Scale 1..3 magnifies small details;
        output is capped near 1568px / 1.15MP, so zoom further by capturing a
        smaller region. The model receives the image itself, not a file reference. For visual
        click/scroll use the returned observation_id and IMAGE pixel coordinates
        in browser_act; no manual crop/scale conversion. Observe again after
        changes. Only the visible region is captured; scroll to inspect more.
        """
        async def look(service, session, identity):
            return await session.observe_page(
                selector=selector, x=x, y=y, width=width, height=height, scale=scale,
            )

        result = await invoke(agent_id, look)
        metadata = {key: value for key, value in result.items() if key != "data"}
        content: list[ContentBlock] = [TextContent(type="text", text=json.dumps(metadata, ensure_ascii=False))]
        failed = result.get("outcome") != "OK"
        if not failed:
            content.append(ImageContent(type="image", data=result["data"], mimeType=result["image"]["mime_type"]))
        return CallToolResult(content=content, isError=failed)

    @mcp.tool()
    async def browser_act(
        agent_id: str,
        action: Literal["click", "fill", "select", "press", "scroll"],
        selector: str | None = None,
        text: str | None = None,
        key: str | None = None,
        value: str | None = None,
        x: float | None = None,
        y: float | None = None,
        delta_x: float | None = None,
        delta_y: float | None = None,
        observation_id: str | None = None,
    ) -> dict[str, Any]:
        """Interact with a web page using a fixed browser action.

        Choose targets from the current page: click(selector or x/y),
        fill(selector, text), select(selector, value), press(key, optional
        selector), or scroll(delta_x/delta_y, optional selector or x/y).
        Keys may include chords such as Control+A. Coordinates use viewport
        pixels unless observation_id comes from browser_look, in which case
        x/y are pixels in that image and are mapped automatically. A stale
        observation is rejected; call browser_look again. Supply both x and y,
        without a selector. Read or look again to verify
        the result. These actions need no site permission or full_cdp_access,
        including on a new origin. Agent actions wait during human takeover.
        Limits: text/value up to 65536 characters, selectors up to 4096
        characters; fill longer content in several steps.
        """
        async def act(service, session, identity):
            arguments = {
                "selector": selector, "text": text, "key": key, "value": value,
                "x": x, "y": y, "delta_x": delta_x, "delta_y": delta_y,
                "observation_id": observation_id,
            }
            return await session.act(action=action, **{
                name: argument for name, argument in arguments.items() if argument is not None
            })

        return await invoke(agent_id, act)

    @mcp.tool()
    async def browser_run(agent_id: str, script: str) -> dict[str, Any]:
        """Evaluate one JavaScript expression in the page and return its JSON value.

        Requires explicitly configured full_cdp_access. Expressions are limited
        to 65536 characters. An async IIFE can express a sequence of actions. Inspect the current
        page to choose selectors. ERROR returns the failure to diagnose;
        NEEDS_HUMAN requires the user's action before continuing.
        """
        async def run(service, session, identity):
            return await session.run_script(script)

        return await invoke(agent_id, run)

    @mcp.tool()
    async def browser_control_state(agent_id: str) -> dict[str, Any]:
        """Read who holds control. Agent actions wait while the user is driving."""
        async def control(service, session, identity):
            return {"outcome": "OK", **session.control.to_dict()}

        return await invoke(agent_id, control)

    @mcp.tool()
    async def browser_request_login(agent_id: str, reason: str = "Sign in to continue") -> dict[str, Any]:
        """Hand the current page to the user for login, verification or CAPTCHA.

        Publishes an in-app notification and waits for the user to take and
        explicitly return control. The returned fresh read is evidence to
        inspect, not a claim of successful login. Never ask for credentials
        in chat. Disconnecting the panel does not complete a login request.
        """
        async def login(service, session, identity):
            return await service.request_login(
                agent_id, reason=reason, turn_id=identity.event_id, thread_id=identity.thread_id,
            )

        return await invoke(agent_id, login)

    @mcp.tool()
    async def browser_save_evidence(agent_id: str, title: str = "Browser evidence") -> dict[str, Any]:
        """Capture the authorized page as an image artifact in the agent's workspace."""
        async def evidence(service, session, identity):
            if not identity.user_id:
                return _error("missing_caller_user", "The runtime must supply the artifact owner.")
            result = await session.capture_screenshot()
            if result.get("outcome") != "OK":
                return result
            from narranexus_plugins.browser_module._browser_module_impl.evidence import save_evidence

            return await save_evidence(agent_id=agent_id, identity=identity, capture=result, title=title)

        return await invoke(agent_id, evidence)

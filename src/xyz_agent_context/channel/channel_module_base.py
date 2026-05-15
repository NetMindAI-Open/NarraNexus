"""
@file_name: channel_module_base.py
@date: 2026-05-08
@description: Abstract base for IM channel Modules.

Phase 2 of the IM channel abstraction. Owns the boilerplate every IM
Module needs (sender registry self-registration, ``hook_data_gathering``
template, MCP server creation glue) WITHOUT constraining each channel's
specific MCP tools or LLM instructions — those are abstract methods the
subclass owns fully.

Lifecycle owned by the base
---------------------------
``__init__``                     → registers ``self.send_to_agent`` in ChannelSenderRegistry
``hook_data_gathering``          → loads credential, calls ``build_extra_data``, injects into ctx_data.extra_data
``hook_after_event_execution``   → filters by working_source, delegates to ``_on_event_executed`` hook
``get_mcp_config``               → standard MCPServerConfig from class attrs
``create_mcp_server``            → builds FastMCP, calls subclass ``register_mcp_tools``

Subclass MUST set class attrs
-----------------------------
``channel_name``       — lowercase key, e.g. "lark"
``brand_display``      — human label, e.g. "Lark / Feishu"
``working_source``     — ``WorkingSource.LARK`` etc.
``ctx_data_key``       — key under which ``build_extra_data`` is injected
                         into ``ctx_data.extra_data`` (e.g. "lark_info")
``mcp_server_name``    — string name passed to FastMCP constructor
``mcp_port``           — TCP port the MCP server binds to

Subclass MUST implement
-----------------------
``get_credential(agent_id) -> Optional[Any]``
``send_to_agent(agent_id, target_id, message, **kw) -> dict``
``register_mcp_tools(mcp) -> None``
``get_instructions(ctx_data) -> str``
``build_extra_data(cred, ctx_data) -> dict``

Subclass MAY override
---------------------
``_on_event_executed(params)`` — default no-op

What this base does NOT abstract (deliberately)
-----------------------------------------------
- ``get_instructions`` content. Lark's is 600+ lines (three-click flow,
  iron rules, identity guide); Telegram's might be 150 lines. Each
  channel's instructions are its product surface.
- MCP tool registration. ``register_mcp_tools`` is abstract; each
  channel registers its own tools (Lark: ``lark_cli`` / ``lark_setup`` /
  …; Slack: ``slack_send`` / ``slack_thread`` / …).
- Credential schema. Different channels need wildly different fields
  (Lark: app_id+secret+brand+permission_state; Slack: bot_token+app_token+team_id).

Iron rule: each abstract method does ONE thing. The base captures the
shape; subclasses fill in the platform-specific content.
"""
from __future__ import annotations

from abc import abstractmethod
from typing import Any, Optional

from loguru import logger

from xyz_agent_context.module.base import XYZBaseModule, mcp_host
from xyz_agent_context.channel.channel_sender_registry import ChannelSenderRegistry
from xyz_agent_context.schema import (
    ContextData,
    HookAfterExecutionParams,
    MCPServerConfig,
    WorkingSource,
)


class ChannelModuleBase(XYZBaseModule):
    """Abstract base for IM channel Modules. See module docstring for contract."""

    # ── Subclass MUST override ────────────────────────────────────────────
    channel_name: str = ""
    brand_display: str = ""
    working_source: WorkingSource = WorkingSource.CHAT  # subclass overrides
    ctx_data_key: str = ""
    mcp_server_name: str = ""
    mcp_port: int = 0

    # ── Class-level guard so multi-instance instantiation doesn't double-register ──
    # Maps channel_name -> True once that channel's sender has been registered.
    # Class-level so it survives across all subclass instances within a process.
    _sender_registered_for_channel: dict[str, bool] = {}

    def __init__(self, *args, **kwargs):
        if not self.channel_name:
            raise ValueError(
                f"{type(self).__name__}.channel_name must be set on the subclass"
            )
        if not self.ctx_data_key:
            raise ValueError(
                f"{type(self).__name__}.ctx_data_key must be set on the subclass"
            )
        super().__init__(*args, **kwargs)
        # Register sender exactly once per channel — subsequent instances
        # would just overwrite the registry entry and log noisily.
        if not ChannelModuleBase._sender_registered_for_channel.get(self.channel_name):
            ChannelSenderRegistry.register(self.channel_name, self.send_to_agent)
            ChannelModuleBase._sender_registered_for_channel[self.channel_name] = True

    # ────────────────────────────────────────────────────────────────────
    # Abstract — subclass MUST implement
    # ────────────────────────────────────────────────────────────────────

    @abstractmethod
    async def get_credential(self, agent_id: str) -> Optional[Any]:
        """Load this agent's credential row. Return None if not bound."""

    @abstractmethod
    async def send_to_agent(
        self, agent_id: str, target_id: str, message: str, **kwargs
    ) -> dict:
        """Sender registered in ChannelSenderRegistry.

        Channel-specific delivery: e.g. for Lark this calls lark-cli to
        send a DM; for Slack it calls chat.postMessage; for Telegram
        sendMessage.
        """

    @abstractmethod
    def register_mcp_tools(self, mcp) -> None:
        """Subclass registers its MCP tools on the FastMCP instance.

        Called by ``create_mcp_server``. Each channel's MCP tools are
        wildly different (Lark exposes one ``lark_cli`` for everything;
        Slack exposes ``slack_send``, ``slack_thread``, ``slack_search``;
        Telegram exposes ``tg_send``, ``tg_bind`` etc.). The base does
        not constrain tool count, naming, or signatures.
        """

    @abstractmethod
    async def get_instructions(self, ctx_data: ContextData) -> str:
        """Per-turn LLM instruction. Channel content is fully subclass-owned.

        Subclass implementations vary in length from ~30 lines (Telegram
        DM-only) to 600+ lines (Lark with three-click flow, identity
        rules, content-delivery guide). The base does not constrain the
        shape.
        """

    @abstractmethod
    async def build_extra_data(self, cred: Any, ctx_data: ContextData) -> dict:
        """Return the dict to inject as ``ctx_data.extra_data[self.ctx_data_key]``.

        Args:
            cred: The credential returned by ``get_credential``.
            ctx_data: Current context (read-only here; subclasses use
                it to read e.g. the channel_tag for trust-signal
                derivation like ``is_owner_interacting``).
        """

    # ────────────────────────────────────────────────────────────────────
    # Subclass MAY override
    # ────────────────────────────────────────────────────────────────────

    async def _on_event_executed(self, params: HookAfterExecutionParams) -> None:
        """Subclass override hook for channel-specific post-execution logic.

        Default no-op. Called only when ``working_source`` matches —
        the base's ``hook_after_event_execution`` does that filtering.
        """

    async def cleanup_for_agent(self, agent_id: str, db) -> dict[str, int]:
        """Remove all per-agent state owned by this channel.

        Called by ``backend/routes/auth.py:delete_agent`` for each
        ``ChannelModuleBase`` subclass in MODULE_MAP — single registry-
        driven cleanup walk replaces what used to be inline channel-
        specific blocks in ``delete_agent``. Returns ``{table_name: n,
        ...}`` for the caller to merge into its stats dict.

        Default implementation handles the common case:
          1. Look up the credential row for ``agent_id`` in
             ``self._credential_table_name()`` (subclasses override if
             non-standard naming).
          2. Walk ``bus_channel_members`` for ``channel_id LIKE
             "{channel_name}_%"`` and remove this agent's membership.
          3. For any inbox channel left empty, delete its ``bus_messages``
             + ``bus_channels`` rows.
          4. Delete the credential row.

        Subclasses with extra cleanup (Lark's CLI profile + workspace
        directory; future channels with on-disk caches; etc.) override
        this and call ``super().cleanup_for_agent(...)`` last.
        """
        from loguru import logger

        stats: dict[str, int] = {}
        cred_table = self._credential_table_name()

        try:
            cred = await db.get_one(cred_table, {"agent_id": agent_id})
            if not cred:
                return stats

            # Inbox cleanup — symmetric for every channel that uses the
            # `{channel_name}_*` channel_id namespace.
            members = await db.get("bus_channel_members", {"agent_id": agent_id})
            channel_prefix = f"{self.channel_name}_"
            for m in members:
                cid = m.get("channel_id", "")
                if not cid.startswith(channel_prefix):
                    continue
                await db.delete(
                    "bus_channel_members",
                    {"channel_id": cid, "agent_id": agent_id},
                )
                remaining = await db.get(
                    "bus_channel_members", {"channel_id": cid}
                )
                if not remaining:
                    await db.delete("bus_messages", {"channel_id": cid})
                    await db.delete("bus_channels", {"channel_id": cid})

            # Credential row
            result = await db.execute(
                f"DELETE FROM {cred_table} WHERE agent_id = %s",
                (agent_id,),
                fetch=False,
            )
            cnt = result if isinstance(result, int) else 0
            if cnt > 0:
                stats[cred_table] = cnt
        except Exception as e:  # noqa: BLE001
            logger.warning(
                f"{type(self).__name__} cleanup_for_agent failed: {e}"
            )
        return stats

    def _credential_table_name(self) -> str:
        """Default credential table name. Override only if your channel uses
        a non-standard table (Lark uses ``lark_credentials``, predating the
        ``channel_*_credentials`` convention)."""
        return f"channel_{self.channel_name}_credentials"

    # ────────────────────────────────────────────────────────────────────
    # Concrete — base provides; subclasses inherit
    # ────────────────────────────────────────────────────────────────────

    async def hook_data_gathering(self, ctx_data: ContextData) -> ContextData:
        """Load credential → build extra_data → inject into ctx_data.

        Failures are swallowed and logged: a missing credential or a
        transient DB error must not break the agent loop's ability to
        gather context for OTHER modules.
        """
        try:
            cred = await self.get_credential(self.agent_id)
            if cred is not None:
                ctx_data.extra_data[self.ctx_data_key] = await self.build_extra_data(
                    cred, ctx_data
                )
        except Exception as e:
            logger.warning(
                f"{type(self).__name__} hook_data_gathering failed: {e}"
            )
        return ctx_data

    async def hook_after_event_execution(
        self, params: HookAfterExecutionParams
    ) -> None:
        """Filter by ``working_source``, then delegate to ``_on_event_executed``.

        ``working_source`` may arrive as the ``WorkingSource`` enum or as
        its plain string value (depending on serialization at call site).
        Compare against both — Python 3.11+'s ``str(enum_member)`` returns
        the qualified name (``"WorkingSource.LARK"``), so naive ``str(ws)``
        comparison is broken. Direct equality works because ``WorkingSource``
        inherits ``(str, Enum)`` and a member equals its string value.
        """
        ws = params.execution_ctx.working_source
        if ws != self.working_source and ws != self.working_source.value:
            return
        await self._on_event_executed(params)

    async def get_mcp_config(self) -> Optional[MCPServerConfig]:
        """Standard MCP config built from class attrs."""
        return MCPServerConfig(
            server_name=self.mcp_server_name,
            server_url=f"http://{mcp_host()}:{self.mcp_port}/sse",
            type="sse",
        )

    def create_mcp_server(self) -> Optional[Any]:
        """Build the FastMCP instance and let the subclass register tools.

        Returns None on import error so a stripped image without ``fastmcp``
        installed still boots — the rest of the channel runs without
        agent-callable tools.
        """
        try:
            from mcp.server.fastmcp import FastMCP
            mcp = FastMCP(self.mcp_server_name)
            mcp.settings.port = self.mcp_port
            self.register_mcp_tools(mcp)
            logger.info(
                f"{type(self).__name__} MCP server created on port {self.mcp_port}"
            )
            return mcp
        except Exception as e:
            logger.exception(
                f"Failed to create {type(self).__name__} MCP server: {e}"
            )
            return None

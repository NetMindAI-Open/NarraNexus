"""Agent data-access abstraction for MCP tools (blueprint P0).

MCP tools depend on the AgentDataStore protocol, not on repositories/db, so the
transport can be swapped by the composition root: DirectStore (local — direct
repository access) or HttpStore (cloud — backend API, no DB creds in mcp).

ChannelCredentialStore (blueprint P2, #2) is the sibling seam for per-channel
send credentials (Discord bot_token, Lark app_secret, …) — see
channel_store.py's module docstring for why it is a separate Protocol.
"""
from narranexus.platform.module_system.data_access.factory import (
    get_agent_data_store,
    get_channel_credential_store,
)
from narranexus.platform.module_system.data_access.store import (
    AgentDataStore,
    DirectStore,
    HttpStore,
)
from narranexus.platform.module_system.data_access.channel_store import (
    ChannelCredentialStore,
    DirectStore as ChannelDirectStore,
    HttpStore as ChannelHttpStore,
)
from narranexus.platform.module_system.data_access.workspace_cwd import (
    resolve_agent_workspace_cwd,
)

__all__ = [
    "AgentDataStore",
    "DirectStore",
    "HttpStore",
    "get_agent_data_store",
    "ChannelCredentialStore",
    "ChannelDirectStore",
    "ChannelHttpStore",
    "get_channel_credential_store",
    "resolve_agent_workspace_cwd",
]

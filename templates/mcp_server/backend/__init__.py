from narranexus.sdk import Contribution, McpServerSpec

# A remote MCP server every agent gets; headers may carry an auth token from settings.
MCP_SERVERS = (Contribution("__PLUGIN_PKG__", lambda: McpServerSpec("__PLUGIN_PKG__", "streamable_http", url="https://example.com/mcp")),)


def activate(ctx):
    pass

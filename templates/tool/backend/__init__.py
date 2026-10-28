from narranexus.sdk import Contribution, McpServerSpec, ToolSpec


class _Provider:
    def list_tools(self):
        return (
            # Reachable through tool_search by default; always_visible=True puts it in the up-front list.
            ToolSpec("__PLUGIN_PKG___echo", "Echo a message back", server="__PLUGIN_PKG__", input_schema={"type": "object", "properties": {"text": {"type": "string"}}}),
        )


# Contribution ids are global within a slot: two plugins that both call theirs
# "tools" cannot be loaded together. Name yours after the plugin.
TOOLS = (Contribution("__PLUGIN_PKG__", _Provider),)
# The MCP server that serves the tools. stdio: the agent runtime starts it per turn with the host's
# interpreter ("python" = the interpreter running NarraNexus) and nxplugins importable; url transports are reached directly.
MCP_SERVERS = (Contribution("__PLUGIN_PKG__", lambda: McpServerSpec("__PLUGIN_PKG__", "stdio", command="python", args=("-m", "nxplugins.__PLUGIN_PKG__.server"))),)


def activate(ctx):
    ctx.log.info("tools declared")

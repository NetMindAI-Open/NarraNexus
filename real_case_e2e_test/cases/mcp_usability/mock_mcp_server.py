"""Minimal mock SSE MCP server for #4 verification. One tool: add_numbers.
Run: .venv/bin/python real_case_e2e_test/cases/mcp_usability/mock_mcp_server.py
SSE URL: http://127.0.0.1:7901/sse"""
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

mcp = FastMCP("MockTestMCP", host="127.0.0.1", port=7955)
mcp.settings.transport_security = TransportSecuritySettings(
    enable_dns_rebinding_protection=False,
)

@mcp.tool()
def add_numbers(a: int, b: int) -> int:
    """Add two integers and return the sum. Use this to compute a + b."""
    return a + b

if __name__ == "__main__":
    mcp.run("sse")

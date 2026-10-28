"""The public browser tool returns native MCP images with scoped metadata."""
import base64
import json
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from PIL import Image
from mcp.types import CallToolResult

from tests.module.test_browser_module_integration import browser, call, runtime_headers  # noqa: F401


@pytest.mark.asyncio
async def test_browser_look_returns_native_image_and_text_without_base64_json(browser):
    module, _, session = browser
    data = BytesIO()
    Image.new("RGB", (20, 10), "green").save(data, "PNG")
    encoded = base64.b64encode(data.getvalue()).decode()
    session.observe_page = AsyncMock(return_value={
        "outcome": "OK", "data": encoded, "observation_id": "view_1",
        "image": {"mime_type": "image/png", "width": 20, "height": 10},
        "page_id": "p1",
    })
    mcp = module.build_instrumented_mcp_server()
    headers, _ = await runtime_headers(module)
    result = await call(mcp, "browser_look", headers, selector="#chart", scale=2)
    assert isinstance(result, CallToolResult)
    assert not result.isError
    assert [part.type for part in result.content] == ["text", "image"]
    assert result.content[1].data == encoded
    metadata = json.loads(result.content[0].text)
    assert metadata["observation_id"] == "view_1"
    assert "data" not in metadata
    assert metadata["session_id"] == "agent_1"
    assert session.bindings[-1][0] == "evt_1"
    tool = mcp._tool_manager.get_tool("browser_look")
    converted = tool.fn_metadata.convert_result(result)
    assert isinstance(converted, CallToolResult)
    assert converted.content[1].type == "image"


@pytest.mark.asyncio
async def test_browser_look_failure_is_text_only_and_scoped(browser):
    module, _, session = browser
    session.observe_page = AsyncMock(return_value={"outcome": "ERROR", "message": "No visible chart"})
    mcp = module.build_instrumented_mcp_server()
    headers, _ = await runtime_headers(module)
    result = await call(mcp, "browser_look", headers)
    assert isinstance(result, CallToolResult) and result.isError
    assert [part.type for part in result.content] == ["text"]
    assert "No visible chart" in result.content[0].text
    session.observe_page.reset_mock()
    denied = await call(mcp, "browser_look", {})
    assert denied.isError
    session.observe_page.assert_not_awaited()

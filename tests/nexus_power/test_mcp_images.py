"""MCP image data must reach model projection without breaking tool-call pairs."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from mcp.types import CallToolResult, ImageContent, TextContent

from narranexus_plugins.frameworks_nexus_power.core.contracts.model import ModelEvent, ProviderProfile
from narranexus_plugins.frameworks_nexus_power.core.contracts.tooling import (
    ToolCall, ToolContext, ToolImage, ToolResult,
)
from narranexus_plugins.frameworks_nexus_power.core._nexus_power_impl.tooling.mcp_channel import McpToolChannel
from narranexus_plugins.frameworks_nexus_power.core._nexus_power_impl.tooling.dispatcher import ToolDispatcher
from narranexus_plugins.frameworks_nexus_power.core._nexus_power_impl.tooling.policy import (
    DisallowedToolsLayer, PolicyEngine,
)
from narranexus_plugins.frameworks_nexus_power.core._nexus_power_impl.session.turn_ledger import TurnLedger
from narranexus_plugins.frameworks_nexus_power.core._nexus_power_impl.modeling.projector import PassthroughProjector
from narranexus_plugins.frameworks_nexus_power.core._nexus_power_impl.modeling.compaction import estimate_message_tokens


@pytest.mark.asyncio
async def test_images_survive_channel_dispatch_ledger_and_projector(tmp_path):
    image = ImageContent(type="image", mimeType="image/png", data="a" * 200_000)
    wire = CallToolResult(content=[TextContent(type="text", text="chart metadata"), image])
    channel = McpToolChannel({})
    channel._sessions["browser"] = SimpleNamespace(call_tool=AsyncMock(return_value=wire))
    channel._register_tools("browser", [SimpleNamespace(
        name="look", description="See a chart", inputSchema={"type": "object"}, annotations=None,
    )])
    ctx = ToolContext(agent_id="agent", workspace=str(tmp_path))
    dispatcher = ToolDispatcher((channel,), policy=PolicyEngine((DisallowedToolsLayer(),)), ctx=ctx)
    ledger = TurnLedger("thread")
    for call_id in ("first", "second"):
        ledger.record_model_event(ModelEvent(kind="tool_use", payload={
            "call_id": call_id, "tool_name": "mcp__browser__look", "args": {},
        }))
    ledger.record_model_event(ModelEvent(kind="done", payload={}))
    for call_id in ("first", "second"):
        result = await dispatcher.execute(ToolCall(id=call_id, name="mcp__browser__look", args={}))
        assert result.content == "chart metadata"
        assert image.data not in result.as_text()
        ledger.record_tool_result(call_id, result)
    messages = PassthroughProjector([]).project(ledger, ProviderProfile(name="test"))
    assert [message["role"] for message in messages] == ["assistant", "tool", "tool", "user"]
    assert [message["tool_call_id"] for message in messages[1:3]] == ["first", "second"]
    images = [part for part in messages[-1]["content"] if part["type"] == "image_url"]
    assert len(images) == 2
    assert all(part["image_url"]["url"] == "data:image/png;base64," + image.data for part in images)
    assert estimate_message_tokens(messages) < 12_000
    assert all(image.data not in message["content"] for message in messages[1:3])


def test_logged_tool_result_describes_images_without_base64():
    """The event log (NDJSON truth file, subprocess stdout) never replays
    image bytes, so it records a descriptor; only the provider view carries data."""
    data = "iVBORw0KGgo" + "A" * 100_000
    ledger = TurnLedger("thread")
    ledger.record_model_event(ModelEvent(kind="tool_use", payload={
        "call_id": "c1", "tool_name": "mcp__browser__look", "args": {},
    }))
    ledger.record_model_event(ModelEvent(kind="done", payload={}))
    [event] = ledger.record_tool_result("c1", ToolResult(
        call_id="c1", ok=True, content="meta", images=(ToolImage(mime_type="image/png", data=data),),
    ))
    assert data not in str(event.payload)
    assert data not in str([entry.payload for entry in ledger.entries()])
    [descriptor] = event.payload["images"]
    assert descriptor["mime_type"] == "image/png"
    assert descriptor["base64_chars"] == len(data)
    assert len(descriptor["sha256"]) == 64
    tool_message = next(m for m in ledger.provider_messages() if m["role"] == "tool")
    assert any(part.get("image_url", {}).get("url", "").endswith(data) for part in tool_message["content"])


@pytest.mark.asyncio
async def test_error_images_are_not_stringified_as_base64():
    channel = McpToolChannel({})
    channel._route["look"] = ("browser", "look")
    channel._sessions["browser"] = SimpleNamespace(call_tool=AsyncMock(return_value=CallToolResult(
        isError=True, content=[TextContent(type="text", text="capture failed"),
                              ImageContent(type="image", mimeType="image/png", data="secret-image")],
    )))
    result = await channel.call("look", {}, ToolContext(agent_id="a", workspace="/tmp"))
    assert not result.ok
    assert "capture failed" in result.error
    assert "secret-image" not in result.as_text()


def test_projected_size_counts_images_by_estimate_and_text_verbatim():
    from narranexus_plugins.frameworks_nexus_power.core._nexus_power_impl.modeling.compaction import (
        _IMAGE_STAND_IN,
        projected_chars,
    )

    text_only = {"role": "tool", "tool_call_id": "c1", "content": "x" * 5000}
    assert projected_chars([text_only]) == len(str(text_only))  # unchanged fast path
    image = {"type": "image_url", "image_url": {"url": "data:image/png;base64," + "A" * 3_000_000}}
    with_image = {"role": "user", "content": [{"type": "text", "text": "seen"}, image]}
    size = projected_chars([with_image])
    assert size < 50_000  # the 3 MB of base64 is not counted as text
    assert size == len(str({"role": "user", "content": [{"type": "text", "text": "seen"}, _IMAGE_STAND_IN]}))

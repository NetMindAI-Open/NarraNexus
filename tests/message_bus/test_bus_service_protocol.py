"""
@file_name: test_bus_service_protocol.py
@author: NarraNexus
@date: 2026-08-03
@description: The MessageBusService protocol and its implementations must
agree on the send signatures.

Found in PR #229 review: ``sender_turn_source`` was added to LocalMessageBus
and its callers, but the abstract protocol and CloudMessageBus were left
behind — so type-checking against the protocol hid the parameter, and a
future cloud implementation would TypeError on a keyword every caller
already passes. Signature drift between a protocol and its implementations
never fails at import time; only a test like this catches it.
"""
from __future__ import annotations

import inspect

import pytest

from xyz_agent_context.message_bus.cloud_bus import CloudMessageBus
from xyz_agent_context.message_bus.local_bus import LocalMessageBus
from xyz_agent_context.message_bus.message_bus_service import MessageBusService

SEND_METHODS = ("send_message", "send_to_agent")


@pytest.mark.parametrize("method", SEND_METHODS)
@pytest.mark.parametrize("impl", [LocalMessageBus, CloudMessageBus])
def test_send_signatures_match_the_protocol(method, impl):
    proto = inspect.signature(getattr(MessageBusService, method))
    got = inspect.signature(getattr(impl, method))
    assert list(got.parameters) == list(proto.parameters), (
        f"{impl.__name__}.{method} drifted from MessageBusService: "
        f"{list(got.parameters)} != {list(proto.parameters)}"
    )
    for name, proto_param in proto.parameters.items():
        assert got.parameters[name].default == proto_param.default, (
            f"{impl.__name__}.{method}({name}=...) default drifted"
        )


@pytest.mark.parametrize("method", SEND_METHODS)
def test_protocol_sends_accept_sender_turn_source(method):
    """The recipient-side classifier depends on this fact being writable
    through ANY bus implementation, not just the local one."""
    sig = inspect.signature(getattr(MessageBusService, method))
    param = sig.parameters.get("sender_turn_source")
    assert param is not None
    assert param.default is None


@pytest.mark.asyncio
async def test_cloud_stub_reaches_not_implemented_not_type_error():
    """Before the fix, passing the keyword every caller passes raised
    TypeError on the stub — masking the real 'not yet implemented' signal."""
    bus = CloudMessageBus(api_base_url="http://x", auth_token="t")
    with pytest.raises(NotImplementedError):
        await bus.send_to_agent(
            from_agent="a", to_agent="b", content="hi",
            sender_turn_source="chat",
        )
    with pytest.raises(NotImplementedError):
        await bus.send_message(
            from_agent="a", to_channel="ch", content="hi",
            sender_turn_source="message_bus",
        )

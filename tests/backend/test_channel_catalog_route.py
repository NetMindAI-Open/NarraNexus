"""
@file_name: test_channel_catalog_route.py
@author: Bin Liang
@date: 2026-09-07
@description: GET /api/plugins/channels answers the channel catalog from ingress.channels — the one source the shell renders, so the frontend keeps no hand-written channel table.

The frontend's ``registerBuiltinChannels.ts`` restated each channel's
``label`` / ``icon`` / ``order`` in TypeScript, duplicating the ``ChannelUi``
its Python descriptor already declared and leaving a third-party channel plugin
unable to appear in the Channels page without editing the engine's frontend.
This endpoint is the seam that removes the second copy.

Pinned here: the row shape the frontend consumes, that a plugin channel is
present the moment it registers and gone the moment its owner is removed
(the ``registry.json``-disabled / distribution-excluded case), the ``ui``
fallback for a descriptor with no ``ChannelUi``, and that one broken descriptor
costs its own row rather than the page.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.routes.channels.catalog import router as catalog_router
from narranexus.contracts.channel import ChannelDescriptor, ChannelUi
from narranexus.kernel.plugins.registries import KERNEL_REGISTRIES
from narranexus.kernel.plugins.registry import Contribution

STYLED = ChannelDescriptor(
    name="acme_chat", display_name="Acme Chat",
    ui=ChannelUi(label="Acme Chat", icon="bot", order=5),
)
PLAIN = ChannelDescriptor(name="plain_chat", display_name="Plain Chat")


@pytest.fixture
def client():
    registry = KERNEL_REGISTRIES.registry_for("ingress.channels")
    disposers = [
        registry.register_contribution(Contribution("acme_chat", lambda: STYLED), owner="acme.chat"),
        registry.register_contribution(Contribution("plain_chat", lambda: PLAIN), owner="plain.chat"),
    ]
    app = FastAPI()
    app.include_router(catalog_router, prefix="/api/plugins")
    try:
        yield TestClient(app)
    finally:
        for dispose in disposers:
            dispose.dispose()


def _rows(client):
    response = client.get("/api/plugins/channels")
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    return {row["name"]: row for row in body["data"]}


def test_a_registered_channel_is_listed_with_its_ui_and_owner(client):
    row = _rows(client)["acme_chat"]
    assert row == {
        "name": "acme_chat",
        "display_name": "Acme Chat",
        "owner": "acme.chat",
        "ui": {"label": "Acme Chat", "icon": "bot", "order": 5},
    }


def test_the_builtin_channels_come_from_the_registry_not_a_table(client):
    """The builtins this process booted are in the same answer as the plugin
    channel — there is one list, and it is the registry's."""
    rows = _rows(client)
    assert {"lark", "slack", "telegram", "wechat", "discord", "narramessenger"} <= set(rows)
    assert rows["lark"]["ui"]["label"] == "Lark / Feishu"
    assert rows["lark"]["owner"] == "builtin.channels.lark"


def test_a_descriptor_without_channel_ui_still_renders(client):
    """No ``ChannelUi`` means "I stated no presentation", not "omit me": the
    display name is the label and the row sorts last rather than first."""
    row = _rows(client)["plain_chat"]
    assert row["ui"] == {"label": "Plain Chat", "icon": "message-square", "order": 100}


def test_rows_are_ordered_by_ui_order_then_name(client):
    response = client.get("/api/plugins/channels")
    data = response.json()["data"]
    assert data == sorted(data, key=lambda r: (r["ui"]["order"], r["name"]))
    assert data[0]["name"] == "acme_chat"  # order=5, ahead of lark's 10


def test_a_removed_owner_disappears_from_the_catalog(client):
    """What boot does to a builtin disabled in registry.json."""
    assert "acme_chat" in _rows(client)
    KERNEL_REGISTRIES.registry_for("ingress.channels").remove_owner("acme.chat")
    assert "acme_chat" not in _rows(client)


def test_one_broken_descriptor_costs_its_own_row_only(client):
    """Per-entry isolation, the policy every other channel view uses."""

    def _boom():
        raise RuntimeError("descriptor unavailable")

    registry = KERNEL_REGISTRIES.registry_for("ingress.channels")
    dispose = registry.register_contribution(Contribution("broken_chat", _boom), owner="broken.chat")
    try:
        rows = _rows(client)
        assert "broken_chat" not in rows
        assert "acme_chat" in rows
    finally:
        dispose.dispose()

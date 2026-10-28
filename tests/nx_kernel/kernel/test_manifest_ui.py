"""
@file_name: test_manifest_ui.py
@author: Bin Liang
@date: 2026-09-03
@description: frontend.ui declarations derive onPage/onPanel/onCommand activation events and are strict.
"""
from __future__ import annotations

import pytest

from narranexus.contracts import ManifestError
from narranexus.kernel.plugins.manifest import derive_activation_events, parse_manifest
from narranexus.kernel.plugins.registries import Registries


def test_ui_declarations_derive_activation_events():
    tree = Registries().slots
    m = parse_manifest(
        {
            "id": "acme.w",
            "version": "1.0.0",
            "displayName": "W",
            "frontend": {
                "entry": "plugin.js",
                "ui": {"pages": [{"id": "acme.w.home", "path": "x/w"}], "panels": [{"id": "acme.w.p"}], "commands": [{"id": "acme.w.c", "label": "Go"}]},
            },
        },
        tree=tree,
    )
    assert derive_activation_events(m) == ("onPage:acme.w.home", "onPanel:acme.w.p", "onCommand:acme.w.c")
    assert m.frontend is not None and m.frontend.ui.pages[0].guard == "protected"
    with pytest.raises(ManifestError):
        parse_manifest({"id": "acme.w", "version": "1.0.0", "displayName": "W", "frontend": {"entry": "p.js", "ui": {"pages": [{"id": "x"}]}}}, tree=tree)


def test_slot_points_renderers_and_timeline_events_are_declarative():
    m = parse_manifest(
        {
            "id": "acme.w",
            "version": "1.0.0",
            "displayName": "W",
            "hosts": ["frontend"],
            "frontend": {
                "entry": "plugin.js",
                "ui": {
                    "conversationKinds": [{"id": "acme.room", "label": "Room"}],
                    "messageRenderers": [{"id": "acme.w.card", "contentPrefix": "card:"}],
                    "timelineEvents": [{"id": "acme.w.tick", "type": "acme_tick"}],
                    "slots": [
                        {"id": "acme.w.strip", "point": "composerExtensions", "when": ["conversationKind:chat", "!agentHas:JobModule"], "order": 7},
                        {"id": "acme.w.act", "point": "chatHeaderActions", "label": "Do it"},
                    ],
                },
            },
        },
        tree=Registries().slots,
    )
    ui = m.frontend.ui
    assert ui.message_renderers[0].content_prefix == "card:" and ui.timeline_events[0].type == "acme_tick"
    assert ui.slots[0].when == ("conversationKind:chat", "!agentHas:JobModule") and ui.slots[1].point == "chatHeaderActions"
    assert derive_activation_events(m) == ("onRenderer:acme.w.card", "onTimelineEvent:acme.w.tick", "onSlot:acme.w.strip", "onSlot:acme.w.act")


@pytest.mark.parametrize(
    "slot",
    [
        {"id": "x", "point": "composerExtensions", "when": ["visible:always"]},
        {"id": "x", "point": "footer"},
    ],
)
def test_bad_slot_declarations_are_rejected(slot):
    with pytest.raises(ManifestError):
        parse_manifest({"id": "acme.w", "version": "1.0.0", "displayName": "W", "hosts": ["frontend"], "frontend": {"entry": "p.js", "ui": {"slots": [slot]}}}, tree=Registries().slots)

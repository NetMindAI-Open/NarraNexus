"""
@file_name: test_current_turn_attachment_marker.py
@date: 2026-07-09
@description: Regressions for current-turn attachment marker injection.

Symptom that motivated this: on dev 2026-07-09 an agent replied "no
image was actually attached" to a NarraMessenger message that had a
real PNG attached. The file was persisted correctly (audit row +
bytes on disk under the owner's workspace), but the agent's current-
turn view of the user message had no marker pointing at the path,
so the model could not Read it.

Fix location: ``context_runtime.build_input_for_framework`` augments
the LLM-facing current-turn user message with markers synthesised via
``Attachment.markers_from_dicts`` — while leaving
``ctx_data.input_content`` (the string persisted by
``ChatModule.hook_persist_turn`` and echoed to the frontend chat
panel) untouched. Same seam covers WS chat and every IM channel: they
already stash attachments in ``trigger_extra_data["attachments"]``.

These tests lock:

  1. The staticmethod ``Attachment.markers_from_dicts`` returns one
     marker per input dict, joined with newlines, and skips malformed
     entries with a WARNING (never silently — silent drops are what
     produced the original "agent claims no file" incident).
  2. Empty / None input returns an empty string.
  3. The runtime helper produces markers whose path uses the
     agent-owner routing (so the file the agent Reads is the one the
     trigger persisted).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from xyz_agent_context.schema.attachment_schema import (
    Attachment,
    AttachmentCategory,
)


def _att_dict(file_id: str = "att_1d31e04a") -> dict:
    """Serialised Attachment as it lives in
    ``ctx_data.extra_data["attachments"]``."""
    return Attachment(
        file_id=file_id,
        mime_type="image/png",
        original_name="report.png",
        size_bytes=1024,
        category=AttachmentCategory.IMAGE,
        transcript=None,
    ).model_dump(mode="json")


def test_markers_from_dicts_renders_one_marker_per_valid_entry(monkeypatch):
    """Two well-formed dicts → two marker lines, in input order."""
    from xyz_agent_context.utils import attachment_storage as storage_mod

    monkeypatch.setattr(
        storage_mod,
        "resolve_attachment_path",
        lambda agent_id, user_id, file_id: Path(f"/ws/{file_id}.png"),
    )

    out = Attachment.markers_from_dicts(
        [_att_dict("att_first"), _att_dict("att_second")],
        agent_id="agent_x",
        user_id="user_owner",
    )

    lines = out.splitlines()
    assert len(lines) == 2
    assert "att_first" in lines[0]
    assert "att_second" in lines[1]
    for line in lines:
        assert line.startswith("[User uploaded image:")
        assert line.endswith("use Read tool to view]")


def test_markers_from_dicts_empty_input_returns_empty_string():
    """``None`` and ``[]`` both produce ``""`` — caller decides how to
    compose with the surrounding user message."""
    assert Attachment.markers_from_dicts(None, agent_id="a", user_id="u") == ""
    assert Attachment.markers_from_dicts([], agent_id="a", user_id="u") == ""


def test_markers_from_dicts_skips_malformed_entry_with_warning(
    monkeypatch, caplog
):
    """A dict missing required fields must NOT abort the whole marker
    block — the other valid entries still render — AND the drop must
    be logged (silent drops recreate the "agent claims no file"
    incident this whole fix addresses)."""
    import logging
    from xyz_agent_context.utils import attachment_storage as storage_mod

    monkeypatch.setattr(
        storage_mod,
        "resolve_attachment_path",
        lambda agent_id, user_id, file_id: Path(f"/ws/{file_id}.png"),
    )

    # loguru → propagate to stdlib so caplog can see the warning.
    from loguru import logger as _loguru
    handler_id = _loguru.add(
        lambda msg: logging.getLogger("attachment_markers").warning(msg),
        level="WARNING",
    )
    try:
        with caplog.at_level(logging.WARNING, logger="attachment_markers"):
            out = Attachment.markers_from_dicts(
                [_att_dict("att_good"), {"file_id": "totally_bogus"}],
                agent_id="agent_x",
                user_id="user_owner",
            )
    finally:
        _loguru.remove(handler_id)

    # Good entry still rendered.
    assert "att_good" in out
    # Bad entry did NOT render.
    assert "totally_bogus" not in out
    # And the drop is loud, not silent.
    assert any(
        "malformed attachment" in rec.getMessage().lower()
        for rec in caplog.records
    )


def test_markers_from_dicts_routes_path_via_owner_user_id(monkeypatch):
    """The path field in the marker MUST come from
    ``resolve_attachment_path(agent_id, owner_user_id, file_id)`` — the
    IM sender's user_id would resolve to a different workspace and the
    agent's Read tool would 404. Locks the owner-vs-sender routing."""
    from xyz_agent_context.utils import attachment_storage as storage_mod

    captured: dict = {}

    def _spy(agent_id, user_id, file_id):
        captured.update({
            "agent_id": agent_id,
            "user_id": user_id,
            "file_id": file_id,
        })
        return Path(f"/ws/{user_id}/{agent_id}/{file_id}.png")

    monkeypatch.setattr(storage_mod, "resolve_attachment_path", _spy)

    out = Attachment.markers_from_dicts(
        [_att_dict("att_route_check")],
        agent_id="agent_x",
        user_id="user_owner",
    )

    assert captured == {
        "agent_id": "agent_x",
        "user_id": "user_owner",
        "file_id": "att_route_check",
    }
    assert "/ws/user_owner/agent_x/att_route_check.png" in out


@pytest.mark.asyncio
async def test_build_input_for_framework_augments_current_turn_only(
    monkeypatch,
):
    """End-to-end at the ``context_runtime.build_input_for_framework``
    seam: when ``ctx_data.extra_data["attachments"]`` is set, the
    LLM-facing user message content picks up the marker, but the
    persisted ``ctx_data.input_content`` is unchanged.

    We stub the LLM message assembly minimally by driving
    ``build_input_for_framework`` with a ``ctx_data``-shaped object
    that carries what the runtime would carry after step_1 / step_2.
    """
    from pathlib import Path

    from xyz_agent_context.utils import attachment_storage as storage_mod

    monkeypatch.setattr(
        storage_mod,
        "resolve_attachment_path",
        lambda agent_id, user_id, file_id: Path(f"/ws/{file_id}.png"),
    )

    # Minimal ctx_data-shaped object. build_input_for_framework only
    # reaches for a small subset of fields when there's no history and
    # no active instance — enough to lock the injection semantics
    # without spinning up the full runtime.
    from types import SimpleNamespace

    ctx_data = SimpleNamespace(
        input_content="what does this image show?",
        agent_id="agent_x",
        user_id="user_owner",
        extra_data={
            "attachments": [_att_dict("att_incident_repro")],
        },
        chat_history=[],
        module_instructions=[],
        working_source="chat",
    )

    from xyz_agent_context.context_runtime.context_runtime import (
        ContextRuntime,
    )

    # ContextRuntime constructor is dependency-injectable; None-drive
    # everything we don't touch in this narrow path.
    runtime = ContextRuntime.__new__(ContextRuntime)

    final_messages, _mcp_servers = await runtime.build_input_for_framework(
        messages=[],
        system_prompt="you are an agent",
        active_instances=[],
        ctx_data=ctx_data,
    )

    # Last message is the current-turn user message.
    user_msg = final_messages[-1]
    assert user_msg["role"] == "user"
    # The persisted input_content is untouched.
    assert ctx_data.input_content == "what does this image show?"
    # The LLM-facing content carries the original text AND the marker.
    assert "what does this image show?" in user_msg["content"]
    assert "att_incident_repro" in user_msg["content"]
    assert "use Read tool to view]" in user_msg["content"]

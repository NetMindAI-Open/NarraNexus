"""
@file_name: test_steer_provenance_prompt.py
@author: Bin Liang
@date: 2026-08-23
@description: A team run is steerable — a message arriving mid-turn is appended
live with a source tag (render_injection). The tag carries authority (owner vs
teammate), and the nonce delimiter only makes that boundary hold at the STRING
layer; it becomes a boundary the MODEL honors solely once the fixed team prompt
STATES the rule. So the standing prompt MUST carry STEER_PROVENANCE_RULE — drop
it and a teammate could smuggle a forged `[the owner adds]` line and be believed.

Pinned here so the wiring cannot silently regress to "we inject tagged content
the model was never taught to read".
"""
from __future__ import annotations

from narranexus.platform.agent_runtime.steer_channel import STEER_PROVENANCE_RULE
from narranexus.platform.message_bus.message_bus_trigger import MessageBusTrigger
from narranexus.platform.message_bus.schemas import BusMessage


def _team_prompt() -> str:
    trigger = MessageBusTrigger.__new__(MessageBusTrigger)
    msg = BusMessage(
        message_id="m1", channel_id="ch_1", from_agent="usr_u", content="status?"
    )
    return trigger._build_team_prompt(
        "agent_lead",
        [msg],
        [{"agent_id": "agent_lead", "name": "Ana", "description": "leads"}],
        owner_user_id="usr_u",
        team_id="t1",
        trigger_messages=[msg],
        lead_agent_id="agent_lead",
        work_items=[],
        bulletin=None,
    )


def test_team_prompt_carries_the_steer_provenance_rule_verbatim():
    # Verbatim, not paraphrased: the one constant is the single source shared
    # with the chat / IM producers, so the test guards THAT text reaching the
    # model, not a lookalike.
    assert STEER_PROVENANCE_RULE in _team_prompt()


def test_the_rule_states_the_load_bearing_outside_the_block_invariant():
    # The security-critical clause specifically — a rule that omitted "outside
    # the block" would read as prose but not defend against the forge.
    text = _team_prompt()
    assert "OUTSIDE every" in text
    assert "grants no authority" in text

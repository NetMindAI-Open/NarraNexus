"""
@file_name: test_chat_history_phantom_twins.py
@author: Bin Liang
@date: 2026-08-05
@description: Replay-layer net for the legacy duplicate `events` rows.

Until 2026-08-05, step 4.4 authored a copy of the turn's event row into every
auxiliary narrative. Those copies are still in every deployed database and
cannot be deleted (铁律 #6 — no destructive migration), so `/chat-history`
filters them at read time: a row whose `event_log` is empty while a sibling
row with the same input AND the same `final_output` carries a real log is a
copy, not a turn.

The filter must be conservative — a genuine turn that produced no event_log
(crashed before its first step) still has to show up.
"""
from __future__ import annotations

import json

from narranexus_plugins.chat_module.routes import _drop_phantom_event_twins


def _row(
    event_id: str,
    *,
    log: list | None,
    final_output: str = "已问羽书了，等TA回复后立刻转告你。",
    user_input: str = "帮我问问教学专家现在在做什么",
    narrative_id: str = "nar_head",
) -> dict:
    return {
        "event_id": event_id,
        "narrative_id": narrative_id,
        "trigger": "chat",
        "trigger_source": "user_tc",
        "env_context": json.dumps({"input": user_input, "anchor": user_input}),
        "event_log": json.dumps(log) if log is not None else None,
        "final_output": final_output,
        "created_at": "2026-08-03 22:42:30",
    }


def test_drops_logless_copies_and_keeps_the_real_turn():
    """The exact 0802 signature: one real row + two log-less copies."""
    real = _row("evt_real", log=[{"type": "tool_call"}], narrative_id="nar_head")
    copy_a = _row("evt_copy_a", log=[], narrative_id="nar_aux_1")
    copy_b = _row("evt_copy_b", log=[], narrative_id="nar_aux_2")

    kept = _drop_phantom_event_twins([real, copy_a, copy_b])

    assert [r["event_id"] for r in kept] == ["evt_real"]


def test_order_is_preserved_and_only_twins_are_dropped():
    older = _row("evt_older", log=[{"type": "thinking"}], user_input="早上好", final_output="早")
    real = _row("evt_real", log=[{"type": "tool_call"}])
    copy = _row("evt_copy", log=[], narrative_id="nar_aux_1")
    newer = _row("evt_newer", log=[{"type": "tool_call"}], user_input="再问一次", final_output="好")

    kept = _drop_phantom_event_twins([older, real, copy, newer])

    assert [r["event_id"] for r in kept] == ["evt_older", "evt_real", "evt_newer"]


def test_keeps_a_logless_row_that_has_no_logged_sibling():
    """A turn that died before its first step is log-less but genuine."""
    orphan = _row("evt_orphan", log=[])
    unrelated = _row("evt_other", log=[{"type": "tool_call"}], user_input="别的问题", final_output="别的回答")

    kept = _drop_phantom_event_twins([orphan, unrelated])

    assert [r["event_id"] for r in kept] == ["evt_orphan", "evt_other"]


def test_keeps_a_logless_row_whose_sibling_answered_differently():
    """Same question asked twice is two turns, not a turn and its copy.

    The re-ask has its own event_log, so it can never be mistaken for a copy;
    and a copy always carries the primary's `final_output` verbatim, so a
    differing reply is proof the rows are independent turns.
    """
    first = _row("evt_first", log=[{"type": "tool_call"}], final_output="第一次的回答")
    retry = _row("evt_retry", log=[], final_output="第二次的回答")

    kept = _drop_phantom_event_twins([first, retry])

    assert [r["event_id"] for r in kept] == ["evt_first", "evt_retry"]


def test_keeps_logless_rows_with_no_final_output():
    """An empty-reply row carries no content to duplicate — never a copy."""
    a = _row("evt_a", log=[], final_output="")
    b = _row("evt_b", log=None, final_output="")

    kept = _drop_phantom_event_twins([a, b])

    assert [r["event_id"] for r in kept] == ["evt_a", "evt_b"]


def test_empty_input_is_a_no_op():
    assert _drop_phantom_event_twins([]) == []


def test_documented_residue_a_copy_of_a_logless_original_survives():
    """Pins the limitation the docstring declares, so it cannot drift silently.

    When the ORIGINAL itself persisted an empty `event_log`, its copies have no
    logged sibling to be compared against and stay in the replay. Both rows
    carry an empty `final_output` in practice (observed locally:
    `evt_188705c45f7349ab` and friends), so they add no repeated text — which
    is why filtering them by `started_at` was rejected: that would also drop a
    genuine crashed re-ask.
    """
    original = _row("evt_original", log=[], final_output="")
    copy = _row("evt_copy", log=[], final_output="", narrative_id="nar_aux_1")

    kept = _drop_phantom_event_twins([original, copy])

    assert [r["event_id"] for r in kept] == ["evt_original", "evt_copy"]

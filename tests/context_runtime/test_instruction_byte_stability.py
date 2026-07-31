"""
@file_name: test_instruction_byte_stability.py
@date: 2026-07-29
@description: Guard the invariant the whole caching effort rests on — a module's
              system-prompt instruction must not change when only per-turn
              volatile state changes.

Why this test exists
--------------------
The prompt cache matches a strict byte prefix ordered ``tools → system →
messages``. Anything volatile inside ``system`` voids the prefix behind it, so
every turn pays full price again. R4 moved the known volatile spans out (into a
``[Turn context]`` block at the tail of ``messages``, where changing bytes cost
nothing), and the transcript work moved history out the same way.

But nothing STOPS the next one from appearing. ``BaseModule.get_instructions``
renders ``self.instructions.format(**ctx_data.model_dump())``, so **any** module
that references a volatile ``ContextData`` field in its template silently becomes
a drift source. Four have already been found this way, each only after it showed
up in production token numbers:

  * ``current_time``          — R4a/b/c relocated it
  * ``bootstrap`` section     — 189 chars, disappears once per agent lifetime
  * ``agent_name``            — changes whenever the agent renames itself
  * narrative name in the     — changes whenever the narrative LLM rewrites it
    per-message timeline tag    (every turn is possible), and it is the WORST
                                position: the first entry of ``messages``

Finding these by reading production token graphs is slow and unreliable. This
test makes the next one fail here instead.

What it does NOT claim
----------------------
It only covers module instructions rendered through ``get_instructions``. The
narrative-name case above lives in ``context_runtime._format_timeline_tag`` (a
``messages`` prefix, not ``system``) and is out of scope — see
``reference/self_notebook/todo/2026-07-29-narrative-name-in-timeline-prefix.md``.
A green run here does not mean the whole prefix is stable.
"""
from __future__ import annotations

import pytest

from xyz_agent_context.module import MODULE_MAP
from xyz_agent_context.schema.context_schema import ContextData

# ContextData fields that legitimately differ between two turns of the SAME
# conversation. A module instruction that varies when only these change has a
# volatile span in its template and belongs in get_turn_context() instead.
#
# Deliberately NOT listed: agent_id / user_id / narrative_id (identity, fixed for
# a conversation) and deployment_* / agent_info_model_type / model_name
# (environment, fixed for a process).
_VOLATILE_FIELDS: dict[str, tuple[object, object]] = {
    "current_time": ("2026-01-01 00:00:00 UTC Monday", "2026-06-15 13:45:07 UTC Sunday"),
    "agent_name": ("Alpha", "A much longer renamed agent"),
    "agent_description": ("first description", "a substantially rewritten one"),
    "creator_name": ("Ann", "Bartholomew"),
    "current_speaker_name": ("Ann", "Bartholomew"),
    "is_creator": (True, False),
    "user_role": ("Creator (Boss)", "User/Customer"),
    "bootstrap_active": (True, False),
    "chat_history": ([], [{"role": "user", "content": "hi"}]),
    "user_profile": ({}, {"nickname": "x"}),
}

_BASE = dict(
    agent_id="agent_stability",
    user_id="user_stability",
    narrative_id="nar_stability",
    input_content="the current turn's message",
    agent_info_model_type="Claude Code",
    model_name="claude-opus-5",
    deployment_mode="local",
    deployment_context="LOCAL DEPLOYMENT",
)


def _ctx(**overrides) -> ContextData:
    return ContextData(**{**_BASE, **overrides})


def _low() -> ContextData:
    return _ctx(**{k: v[0] for k, v in _VOLATILE_FIELDS.items()})


def _high() -> ContextData:
    return _ctx(**{k: v[1] for k, v in _VOLATILE_FIELDS.items()})


# Modules KNOWN to interpolate volatile state into their system-prompt
# instruction, with the fields that drive it. Recorded as strict xfail rather
# than skipped so the guard has three properties at once:
#
#   * a module NOT listed here that starts drifting fails immediately;
#   * a listed module that gets FIXED turns into an unexpected pass, forcing
#     whoever fixed it to delete the entry (the list cannot rot silently);
#   * the current violations live in code next to the assertion, not only in a
#     todo file someone has to remember to read.
#
# BasicInfoModule was found in production on 2026-07-29: an agent renaming
# itself via awareness_module__update_agent_name changed the instruction by ONE
# character, which voided the whole prefix behind it (measured ~8,800 full-price
# tokens on the NetMind path, ~30,000 weighted on CC).
_KNOWN_DRIFT: dict[str, str] = {
    "BasicInfoModule": (
        "renders agent_name / agent_description / creator_name / "
        "current_speaker_name / is_creator / user_role straight into the system "
        "prompt. The first two change whenever the agent edits its own identity; "
        "the last four change per SENDER, so any group channel drifts on every "
        "turn from a different person. Fix is the R4 pattern (static pointer in "
        "get_instructions + the span in get_turn_context)."
    ),
}


@pytest.mark.parametrize("module_name", sorted(MODULE_MAP))
@pytest.mark.asyncio
async def test_module_instruction_is_byte_stable_across_volatile_state(
    module_name, request
):
    """Render one module's instruction twice, differing only in volatile fields.

    A failure means that module's template interpolates something that changes
    per turn. The fix is the R4 pattern: keep a static pointer in
    ``get_instructions`` and emit the volatile span from ``get_turn_context()``
    — which lands in the current user message, after the cache prefix. Moving
    bytes, never dropping them.
    """
    if module_name in _KNOWN_DRIFT:
        request.node.add_marker(
            pytest.mark.xfail(strict=True, reason=_KNOWN_DRIFT[module_name])
        )

    try:
        module = MODULE_MAP[module_name](agent_id=_BASE["agent_id"])
        a = await module.get_instructions(_low())
        b = await module.get_instructions(_high())
    except (KeyError, AttributeError, TypeError) as e:
        # Either the module needs constructor arguments a bare call cannot supply
        # (the channel modules take a channel spec), or its template references a
        # ContextData field this synthetic instance does not carry. Skipped, not
        # passed: an unrendered module has NOT been shown to be stable, and
        # saying so keeps the coverage gap visible in the test output.
        pytest.skip(f"{module_name} could not render on a synthetic ContextData: {e!r}")

    assert a == b, (
        f"{module_name}.get_instructions() changed when only per-turn volatile "
        f"state changed — it has a drift source in its template. Move the "
        f"volatile span to get_turn_context(). Lengths {len(a)} vs {len(b)}."
    )


@pytest.mark.asyncio
async def test_the_guard_actually_catches_a_drift_source():
    """Negative control: the assertion above must be capable of failing.

    Without this, a bug that made every module render "" (or the parametrize list
    resolve to nothing) would turn the whole guard green while checking nothing.
    """
    # Subclass a module the guard already constructs successfully, so this
    # exercises the SAME rendering path the parametrized cases use — only the
    # template differs.
    class _Drifting(MODULE_MAP["ChatModule"]):  # type: ignore[misc]
        """A module whose template interpolates a volatile field."""

    drifting = _Drifting(agent_id="agent_stability")
    drifting.instructions = "the time is {current_time}"

    a = await drifting.get_instructions(_low())
    b = await drifting.get_instructions(_high())
    assert a != b, "the guard's rendering path is not sensitive to volatile fields"

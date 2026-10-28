"""
@file_name: test_narra_failure_instructions.py
@date: 2026-09-09
@description: Lockstep guard for the narra-cli failure instructions carried by
the module's `_CLI_CAPABILITY` prompt block.

Prod 2026-09-09: `narra_cli` answered ``agent-token-invalid`` on 27 bindings at
once (the platform sent the token to the wrong backend). Two agent behaviours
made it worse and both are governed by this text — asserting a cause the agent
could not verify, and pasting the token into chat.

The BasicInfo Product Feedback Duty now enforces "one report per tool + code"
inside `submit_feedback` via `dedup_key`, so a caller that omits the key gets no
deduplication at all. narra_cli is the path the incident came in on and the only
one with its own concrete filing instructions, so the key has to be named here —
otherwise the gate is real but never engaged where it matters. `_narra_guide`'s
two surfaces are covered by test_narra_guide.py.
"""
import inspect

from narranexus_plugins.narramessenger_module.narramessenger_module import _CLI_CAPABILITY


def _submit_feedback_params() -> set[str]:
    """The REAL parameter names of basic_info's submit_feedback.

    Importing across plugins is fine in a test (tests are not modules, so
    铁律 #3 is untouched) and is the point of this guard: the three
    narramessenger surfaces hardcode `dedup_key` as prose, and nothing else
    ties that literal to the tool it names.
    """
    from narranexus_plugins.basic_info_module import _basic_info_mcp_tools as mt

    captured: dict = {}

    class _Mcp:
        def tool(self, **kw):
            def _wrap(fn):
                captured[kw["name"]] = fn
                return fn
            return _wrap

    mt._register_feedback_tool(_Mcp())
    return set(inspect.signature(captured["submit_feedback"]).parameters)


def test_capability_block_hands_submit_feedback_a_dedup_key():
    assert 'submit_feedback(category="error"' in _CLI_CAPABILITY
    assert 'dedup_key="narra_cli:<code>"' in _CLI_CAPABILITY
    # A platform-wide outage reproduces on every call; the key is what keeps
    # that to one report per agent, so the text must say why it is not optional.
    assert "always pass it" in _CLI_CAPABILITY


def test_the_kwarg_this_text_tells_the_agent_to_pass_actually_exists():
    # FastMCP validates arguments against the signature: if `dedup_key` were
    # renamed, an agent following these instructions verbatim would send an
    # unknown kwarg and submit_feedback would fail outright — on the one path
    # the 2026-09-09 incident came in on, silently disarming the gate.
    assert "dedup_key" in _submit_feedback_params()


def test_capability_block_defers_the_notification_claim_to_the_tool_result():
    # Whether the team was reached is the OUTCOME of the call — the send is
    # fire-and-forget and a deployment can disable feedback entirely — so the
    # agent relays the result instead of asserting it.
    assert "Relay only what that call's result says" in _CLI_CAPABILITY
    assert "do NOT assert a cause" in _CLI_CAPABILITY


def test_capability_block_keeps_the_by_design_answers_out_of_feedback():
    # official-agent-required / no_credential are answers for the user, not
    # product defects; filing them would drown the real signal.
    assert "official-agent-required" in _CLI_CAPABILITY
    assert "no_credential" in _CLI_CAPABILITY
    assert "by-design answers" in _CLI_CAPABILITY

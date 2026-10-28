"""
@file_name: test_ingress_guard_all_paths.py
@author:
@date: 2026-08-24
@description: Every inbound path must route through the ingress breaker.

There is no single chokepoint to place the guard in. ``LarkTrigger`` owns
its whole ``_process_message`` and never calls ``super()``; managed mode
bypasses the subscriber path entirely (no ``_subscribe_loop``, no dedup
store, no worker queue). So the seam is the METHOD — ``_ingress_admitted``
— and the invariant "every receive path calls it" is not something the
type system or any unit test can hold.

Same defect class, and the same answer, as
``test_trigger_envelope_every_channel``: ``build_trigger_extra_data`` was
hand-rolled at four sites and the turn envelope was added to one of them,
leaving Lark p2p and NarraMessenger DMs silently unprotected. These tests
pin the seam so the NEXT ingress gate cannot skip a channel — and so that
deleting the guard from Lark (which looks like harmless duplicate code)
fails loudly instead of quietly.
"""
from __future__ import annotations

import inspect

import pytest

from narranexus.platform.channel.channel_trigger_base import ChannelTriggerBase
from narranexus.platform.module_system.channel_trigger_map import CHANNEL_TRIGGER_MAP
from narranexus.platform.module_system.managed_channel_ingress import ManagedChannelIngress

# These assertions read source text, so they must not be satisfiable — or
# defeated — by PROSE. Both happened on the first run: a comment explaining
# "Lark never calls super()._process_message" made the "does it delegate?"
# check answer yes. Strip comment lines, and match call sites with their
# opening paren so a name mentioned in running text cannot count.
GUARD_CALL = "_ingress_admitted("
SUPER_CALL = "super()._process_message("


def _code(func) -> str:
    """Source of ``func`` with ``#`` comment lines removed."""
    lines = inspect.getsource(func).splitlines()
    return "\n".join(ln for ln in lines if not ln.strip().startswith("#"))


def test_every_process_message_implementation_reaches_the_guard():
    """Any class owning a ``_process_message`` must gate it, or delegate.

    Two acceptable shapes, and only two:
      - it calls ``_ingress_admitted`` itself (the base; Lark, which never
        delegates; Matrix's silent branch, which returns before it does)
      - it calls ``super()._process_message``, inheriting the base's gate

    A third shape — an override that does neither — is a channel quietly
    running without a breaker, which is exactly the state the whole PR
    exists to end.
    """
    owners = [ChannelTriggerBase] + [
        cls
        for cls in CHANNEL_TRIGGER_MAP.values()
        if "_process_message" in cls.__dict__
    ]
    assert len(owners) >= 2, "expected at least the base plus Lark's override"

    missing = []
    for cls in owners:
        src = _code(cls.__dict__["_process_message"])
        if GUARD_CALL in src or SUPER_CALL in src:
            continue
        missing.append(cls.__name__)
    assert not missing, (
        f"{missing} define their own _process_message without either "
        f"calling _ingress_admitted or delegating to super() — that "
        f"channel silently loses the ingress breaker while every other "
        f"channel keeps it."
    )


def test_lark_gates_its_own_path():
    """Lark is the one channel that owns the whole method. Pinned by name
    because 'looks like duplicate code, delete it' is exactly how this
    regresses."""
    lark = CHANNEL_TRIGGER_MAP["lark"]
    src = _code(lark.__dict__["_process_message"])
    assert SUPER_CALL not in src, (
        "if Lark started delegating, simplify this test rather than "
        "keeping a redundant gate"
    )
    assert GUARD_CALL in src


def test_matrix_silent_batch_path_is_gated():
    """``group_silent`` returns before super(), but still runs a memory
    pipeline — a repeat storm there is still a storm."""
    matrix = CHANNEL_TRIGGER_MAP["narramessenger"]
    src = _code(matrix.__dict__["_process_message"])
    silent_branch = src.split('if target == "group_silent"', 1)
    assert len(silent_branch) == 2, "the silent branch moved; re-pin this test"
    before_super = silent_branch[1].split(SUPER_CALL, 1)[0]
    assert GUARD_CALL in before_super


def test_managed_before_run_calls_the_guard():
    """Managed mode bypasses the native path; it needs its own call."""
    source = _code(ManagedChannelIngress.before_run)
    assert GUARD_CALL in source, (
        "ManagedChannelIngress.before_run must gate managed turns — it is "
        "the ONLY way into the pipeline on the Manyfold surface."
    )


def test_managed_guard_reuses_the_channel_tunables():
    """Managed and native must not drift into two sets of thresholds."""
    source = _code(ManagedChannelIngress._guard)
    assert "build_ingress_guard(" in source, (
        "the managed guard must be built from the trigger's own class "
        "attributes, not from hand-copied constants"
    )


@pytest.mark.parametrize("cls", sorted(CHANNEL_TRIGGER_MAP.values(), key=lambda c: c.__name__))
def test_every_channel_answers_the_agent_peer_question(cls):
    """``is_agent_peer`` feeds three consumers (breaker thresholds, DM
    fallback, DM prompt). Every channel must at least inherit an answer."""
    assert callable(getattr(cls, "is_agent_peer", None))


def test_guard_is_built_during_start():
    source = _code(ChannelTriggerBase.start)
    assert "build_ingress_guard(" in source


# ─────────────────────────────────────────────────────────────────────
# Docstrings that name a caller must have one
#
# This PR shipped `forget()` with "called when a credential is unbound"
# and no caller; the fix renamed it to `forget_agent()`, restated the
# claim more firmly, and STILL had no caller. A method nobody calls is
# dead code; a method nobody calls whose docstring says otherwise makes
# the next person believe a cleanup path exists.
# ─────────────────────────────────────────────────────────────────────

LIFECYCLE_METHODS_THAT_CLAIM_A_CALLER = ["forget_agent", "warm_start", "prune_idle"]


@pytest.mark.parametrize("method", LIFECYCLE_METHODS_THAT_CLAIM_A_CALLER)
def test_guard_lifecycle_hooks_are_actually_called(method):
    from narranexus.platform.channel import ingress_guard as guard_mod

    guard_src = _code_of_module(guard_mod)
    assert f"def {method}(" in guard_src, f"{method} no longer exists — update this list"

    callers = []
    for mod in (
        "narranexus.platform.channel.channel_trigger_base",
        "narranexus.platform.module_system.managed_channel_ingress",
        "narranexus.platform.channel.ingress_guard",
    ):
        import importlib

        src = _code_of_module(importlib.import_module(mod))
        # Skip the definition itself.
        src = src.replace(f"def {method}(", "")
        if f".{method}(" in src or f"self.{method}(" in src:
            callers.append(mod)
    assert callers, (
        f"IngressGuard.{method}() has no caller in src/. Either wire it up "
        f"or delete it — its docstring tells the next person a path exists."
    )


def _code_of_module(mod) -> str:
    import inspect

    lines = inspect.getsource(mod).splitlines()
    return "\n".join(ln for ln in lines if not ln.strip().startswith("#"))

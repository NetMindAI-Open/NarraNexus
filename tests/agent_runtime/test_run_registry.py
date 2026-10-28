"""
@file_name: test_run_registry.py
@author: Bin Liang
@date: 2026-08-21
@description: RunRegistry — the per-process index of live runs by
(agent_id, surface), so a producer can ask "is this agent already running
on THIS surface?" and steer into that run instead of dispatching a new
turn. Surface-scoping is the whole point: a message for one surface must
never route into the same agent's run on a different surface.

RunRegistry is deliberately synchronous (dict ops, no await between read
and write — that atomicity is the design), so these tests are plain ``def``:
no event loop, no ``@pytest.mark.asyncio`` (the project runs
``asyncio_mode = "auto"``, and writing them async would falsely suggest an
async API).
"""

import pytest

from narranexus.platform.agent_runtime.run_registry import RunRegistry


def test_register_then_live_run_returns_the_handle():
    reg = RunRegistry()
    reg.register("agent_a", "team:room1", "run1", steer="handle1")

    live = reg.live_run("agent_a", "team:room1")
    assert live is not None
    assert live.run_id == "run1"
    assert live.steer == "handle1"


def test_a_different_surface_of_the_same_agent_does_not_match():
    # The anti-cross-talk guarantee: agent_a running on team room1 must NOT
    # be a match for a message on team room2 (or its web chat, or a job).
    reg = RunRegistry()
    reg.register("agent_a", "team:room1", "run1", steer="h")

    assert reg.live_run("agent_a", "team:room2") is None
    assert reg.live_run("agent_a", "chat:u1:s1") is None


def test_a_different_agent_does_not_match():
    reg = RunRegistry()
    reg.register("agent_a", "team:room1", "run1", steer="h")
    assert reg.live_run("agent_b", "team:room1") is None


def test_release_removes_the_run():
    reg = RunRegistry()
    reg.register("agent_a", "team:room1", "run1", steer="h")
    reg.release("run1")
    assert reg.live_run("agent_a", "team:room1") is None


def test_concurrent_runs_of_one_agent_on_distinct_surfaces_are_isolated():
    # The multi-team / multi-trigger case: one agent, several live runs, each
    # found only by its own surface — three isolated inboxes downstream.
    reg = RunRegistry()
    reg.register("agent_a", "team:roomA", "run_a", steer="ha")
    reg.register("agent_a", "team:roomB", "run_b", steer="hb")
    reg.register("agent_a", "chat:u1:s1", "run_web", steer="hw")

    assert reg.live_run("agent_a", "team:roomA").run_id == "run_a"
    assert reg.live_run("agent_a", "team:roomB").run_id == "run_b"
    assert reg.live_run("agent_a", "chat:u1:s1").run_id == "run_web"


def test_releasing_one_run_leaves_the_agents_other_runs():
    reg = RunRegistry()
    reg.register("agent_a", "team:roomA", "run_a", steer="ha")
    reg.register("agent_a", "team:roomB", "run_b", steer="hb")

    reg.release("run_a")

    assert reg.live_run("agent_a", "team:roomA") is None
    assert reg.live_run("agent_a", "team:roomB").run_id == "run_b"


def test_re_registering_a_surface_replaces_the_stale_run():
    # A surface holds at most one live run; the newest wins and the stale
    # mapping never shadows it.
    reg = RunRegistry()
    reg.register("agent_a", "team:roomA", "run_old", steer="old")
    reg.register("agent_a", "team:roomA", "run_new", steer="new")

    live = reg.live_run("agent_a", "team:roomA")
    assert live.run_id == "run_new"
    assert live.steer == "new"


def test_releasing_a_superseded_run_does_not_evict_the_current_one():
    # run_old was replaced by run_new on the same surface; a late release of
    # run_old must not clear run_new's mapping. Post-supersede-pop this now
    # travels the ``handle is None`` early-return in release (run_old is already
    # gone from _by_run), which is exactly why the ``== run_id`` surface guard
    # could be dropped — the pop makes the late release a no-op on its own.
    reg = RunRegistry()
    reg.register("agent_a", "team:roomA", "run_old", steer="old")
    reg.register("agent_a", "team:roomA", "run_new", steer="new")

    reg.release("run_old")

    assert reg.live_run("agent_a", "team:roomA").run_id == "run_new"


def test_registered_scope_releases_on_normal_and_exception_exit():
    reg = RunRegistry()
    with reg.registered("agent_a", "team:room1", "run1", steer="h"):
        assert reg.live_run("agent_a", "team:room1").run_id == "run1"
    assert reg.live_run("agent_a", "team:room1") is None  # released on normal exit

    with pytest.raises(ValueError):
        with reg.registered("agent_a", "team:room1", "run2", steer="h"):
            assert reg.live_run("agent_a", "team:room1").run_id == "run2"
            raise ValueError("boom")
    # finally released even though the body raised — a crashed run cannot pin
    # the surface (the deaf-surface guard).
    assert reg.live_run("agent_a", "team:room1") is None


def test_registered_scope_exit_does_not_evict_a_run_that_superseded_it():
    # The real orchestration shape: `registered()` is the main entry, and being
    # superseded mid-scope is a routine event. run1 is registered under scope,
    # run2 supersedes the same surface while the scope body still runs, and the
    # scope's finally-release(run1) must be a no-op — run2 stays live.
    reg = RunRegistry()
    with reg.registered("agent_a", "team:room1", "run1", steer="h1"):
        assert reg.live_run("agent_a", "team:room1").run_id == "run1"
        reg.register("agent_a", "team:room1", "run2", steer="h2")  # supersede
    live = reg.live_run("agent_a", "team:room1")
    assert live is not None
    assert live.run_id == "run2"  # the superseding run survives the scope exit


def test_live_run_sweeps_a_dead_run_so_the_surface_is_not_deaf_forever():
    reg = RunRegistry()
    reg.register("agent_a", "team:room1", "run1", steer="h", is_alive=lambda: False)
    # The run says it is dead → live_run reports no live run. (That the mapping
    # is cleared is proven by test_registering_after_a_sweep, via public API.)
    assert reg.live_run("agent_a", "team:room1") is None


def test_live_run_keeps_a_run_its_probe_reports_alive():
    # The other side of the sweep: an is_alive that says "alive" must not sweep.
    # Reverse the predicate in live_run ("has a probe → treat as dead") and this
    # is the only test that goes red — it pins the positive branch the sweep
    # test cannot.
    reg = RunRegistry()
    reg.register("agent_a", "team:room1", "run1", steer="h", is_alive=lambda: True)
    live = reg.live_run("agent_a", "team:room1")
    assert live is not None
    assert live.run_id == "run1"
    assert live.steer == "h"


def test_registering_after_a_sweep_establishes_a_fresh_mapping():
    # After a dead run is swept the surface is free again: a fresh run
    # registers and routes normally, with no residue from the swept run.
    reg = RunRegistry()
    reg.register("agent_a", "team:room1", "run_dead", steer="h", is_alive=lambda: False)
    assert reg.live_run("agent_a", "team:room1") is None  # swept

    reg.register("agent_a", "team:room1", "run_fresh", steer="h2")
    live = reg.live_run("agent_a", "team:room1")
    assert live is not None
    assert live.run_id == "run_fresh"
    assert live.steer == "h2"


def test_live_run_heals_a_dangling_surface_entry():
    # The one path that leaves a surface pointing at a run_id no longer in
    # _by_run: the same run_id registered on two surfaces, then released
    # (release clears only the surface_key on its handle). live_run on the OTHER
    # surface must report no live run AND clear the stale mapping — the
    # self-heal branch. Break the del's key and this is where it would surface.
    reg = RunRegistry()
    reg.register("agent_a", "team:s1", "run1", steer="h")
    reg.register("agent_a", "team:s2", "run1", steer="h")  # same run_id, 2nd surface
    reg.release("run1")  # clears only s2 (the handle's surface_key)

    assert reg.live_run("agent_a", "team:s1") is None  # heals the dangling entry
    assert reg.live_run("agent_a", "team:s1") is None  # and stays clean


def test_superseding_a_run_does_not_leak_the_old_by_run_entry():
    reg = RunRegistry()
    reg.register("agent_a", "team:roomA", "run_old", steer="old")
    reg.register("agent_a", "team:roomA", "run_new", steer="new")
    # The old run's _by_run entry (and its SteerChannel) must be dropped, not
    # left pinned for the life of the process. Delete the supersede pop and
    # this is 2.
    assert reg.live_run_count() == 1

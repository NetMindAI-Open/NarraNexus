"""
@file_name: test_channel_supervisor.py
@author: NetMind.AI
@date: 2026-07-08
@description: Unit tests for the consolidated channel-trigger supervisor.

Guards the core of ``module/run_channel_triggers.py`` that replaced the old
one-process-per-channel layout: every selected channel is instantiated,
``pre_start``-ed then ``start``-ed in one loop, ``--only`` filters the set, and
a failure in ONE channel (instantiate / pre_start / start) is isolated so the
others still come up.
"""

import pytest

from xyz_agent_context.module.run_channel_triggers import (
    _parse_only,
    start_channel_triggers,
)


def _make_trigger_cls(record: list, *, fail_on: str | None = None):
    """Build a fake trigger class that appends (name, phase) to ``record``.

    ``fail_on`` in {"init","pre_start","start"} makes that phase raise, to
    exercise the supervisor's per-channel isolation.
    """

    class _FakeTrigger:
        def __init__(self, max_workers: int = 3):
            self.max_workers = max_workers
            self.stopped = False
            if fail_on == "init":
                raise RuntimeError("boom-init")

        async def pre_start(self, db):
            if fail_on == "pre_start":
                raise RuntimeError("boom-pre_start")
            record.append((self.name, "pre_start"))

        async def start(self, db):
            if fail_on == "start":
                raise RuntimeError("boom-start")
            record.append((self.name, "start"))

        async def stop(self):
            self.stopped = True

    return _FakeTrigger


async def test_starts_all_channels_pre_start_before_start():
    record: list = []
    a, b = _make_trigger_cls(record), _make_trigger_cls(record)
    a.name = b.name = None  # set per-instance below via map wiring
    # Give each fake its channel name so record entries are identifiable.
    trigger_map = {"alpha": a, "beta": b}
    for name, cls in trigger_map.items():
        cls.name = name  # class-level; only one instance each in this test

    started = await start_channel_triggers(db=None, only=None, trigger_map=trigger_map)

    assert {n for n, _ in started} == {"alpha", "beta"}
    # Every channel: pre_start strictly before start.
    for name in ("alpha", "beta"):
        phases = [p for n, p in record if n == name]
        assert phases == ["pre_start", "start"], phases


async def test_only_filter_starts_subset():
    record: list = []
    a, b, c = (_make_trigger_cls(record) for _ in range(3))
    trigger_map = {"a": a, "b": b, "c": c}
    for name, cls in trigger_map.items():
        cls.name = name

    started = await start_channel_triggers(
        db=None, only={"a", "c"}, trigger_map=trigger_map
    )

    assert {n for n, _ in started} == {"a", "c"}
    assert "b" not in {n for n, _ in started}


async def test_start_failure_in_one_channel_is_isolated():
    record: list = []
    ok1 = _make_trigger_cls(record)
    boom = _make_trigger_cls(record, fail_on="start")
    ok2 = _make_trigger_cls(record)
    trigger_map = {"ok1": ok1, "boom": boom, "ok2": ok2}
    for name, cls in trigger_map.items():
        cls.name = name

    started = await start_channel_triggers(db=None, only=None, trigger_map=trigger_map)

    # The broken channel is skipped; the healthy ones still start.
    assert {n for n, _ in started} == {"ok1", "ok2"}


async def test_pre_start_failure_in_one_channel_is_isolated():
    record: list = []
    ok = _make_trigger_cls(record)
    boom = _make_trigger_cls(record, fail_on="pre_start")
    trigger_map = {"ok": ok, "boom": boom}
    for name, cls in trigger_map.items():
        cls.name = name

    started = await start_channel_triggers(db=None, only=None, trigger_map=trigger_map)

    assert {n for n, _ in started} == {"ok"}
    # boom failed in pre_start, so it never reached start.
    assert ("boom", "start") not in record


async def test_instantiation_failure_is_isolated():
    record: list = []
    ok = _make_trigger_cls(record)
    boom = _make_trigger_cls(record, fail_on="init")
    trigger_map = {"ok": ok, "boom": boom}
    for name, cls in trigger_map.items():
        cls.name = name

    started = await start_channel_triggers(db=None, only=None, trigger_map=trigger_map)

    assert {n for n, _ in started} == {"ok"}


async def test_empty_selection_returns_nothing():
    record: list = []
    a = _make_trigger_cls(record)
    a.name = "a"
    started = await start_channel_triggers(
        db=None, only={"nonexistent"}, trigger_map={"a": a}
    )
    assert started == []


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("lark,slack", {"lark", "slack"}),
        (" lark , slack ", {"lark", "slack"}),
        ("lark", {"lark"}),
        ("", None),
        (None, None),
        (",,", None),
    ],
)
def test_parse_only(raw, expected):
    assert _parse_only(raw) == expected


# ── Aggregated health payload ─────────────────────────────────────────────────

class _FakeQueue:
    def __init__(self, depth):
        self._depth = depth

    def qsize(self):
        return self._depth


class _FakeAuditRepo:
    async def count_by_type(self, since_hours=1):
        return {"ingress_processed": 3}


class _HealthTrigger:
    """Minimal object exposing the base attributes the health payload reads."""

    def __init__(self, *, running, with_audit=True):
        self.running = running
        self._startup_time_ms = 1000 if running else 0
        self._audit_repo = _FakeAuditRepo() if with_audit else None
        self._subscriber_tasks = {"k1": object()}
        self._workers = [object(), object()]
        self._task_queue = _FakeQueue(5)
        self._subscriber_creds = {"k1": object()}


async def test_health_payload_ok_when_all_channels_ok():
    from xyz_agent_context.channel.channel_health_server import build_health_payload

    triggers = [
        ("lark", _HealthTrigger(running=True)),
        ("slack", _HealthTrigger(running=True)),
    ]
    payload = await build_health_payload(triggers)

    assert payload["status"] == "ok"
    assert payload["channel_count"] == 2
    assert payload["channels"]["lark"]["worker_count"] == 2
    assert payload["channels"]["lark"]["queue_depth"] == 5
    assert payload["channels"]["slack"]["recent_event_counts"] == {"ingress_processed": 3}


async def test_health_payload_degraded_when_a_channel_starting():
    from xyz_agent_context.channel.channel_health_server import build_health_payload

    triggers = [
        ("lark", _HealthTrigger(running=True)),
        ("slack", _HealthTrigger(running=False, with_audit=False)),  # still starting
    ]
    payload = await build_health_payload(triggers)

    assert payload["status"] == "degraded"
    assert payload["channels"]["slack"]["status"] == "starting"

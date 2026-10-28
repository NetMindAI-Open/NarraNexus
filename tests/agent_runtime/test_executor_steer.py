"""
@file_name: test_executor_steer.py
@date: 2026-08-24
@description: Cloud live-steering on the executor boundary.

The executor gains a `/steer` HTTP ingress (the twin of the local stdin
`{"steer": …}` line), a `/capabilities` probe, and a new `{"steer_consumed": …}`
NDJSON frame on `/agent-loop` so consumption flows back to the orchestrator.
These pin the server half; the RemoteAgentLoopDriver half is in
tests/agent_framework/test_remote_driver_steer.py.

Delete the run_id wiring and the steer registration and these go red — a steer
POST would 404 (no run registered) and the consumed frame would never leave the
executor.
"""
from __future__ import annotations

import json

from fastapi.testclient import TestClient

from narranexus.platform.agent_runtime import executor_service as svc
from narranexus.platform.agent_runtime.executor_protocol import (
    build_agent_loop_request,
    build_steer_request,
)


# ---------------------------------------------------------------- wire format


def _body(**kw):
    return build_agent_loop_request(
        framework="nexus_power", working_path="/tmp",
        messages=[], mcp_servers={}, extra_env=None, **kw,
    )


def test_run_id_rides_the_body_only_when_supplied():
    assert _body(run_id="abc123")["run_id"] == "abc123"
    # Omitted (not null) so an executor that predates /steer never sees the key
    # and the non-steerable body is byte-for-byte the old one.
    assert "run_id" not in _body()


def test_build_steer_request_wraps_the_provider_message_under_steer():
    msg = {"role": "user", "content": "hi", "_steer_id": "s1"}
    assert build_steer_request(run_id="r1", steer_msg=msg) == {"run_id": "r1", "steer": msg}


# ---------------------------------------------------------------- /steer ingress


def test_steer_feeds_the_registered_runs_queue():
    inbound = svc._InboundSteer()
    svc._STEER_RUNS["r1"] = inbound
    try:
        client = TestClient(svc.app)
        msg = {"role": "user", "content": "the owner adds", "_steer_id": "s9"}
        r = client.post("/steer", json=build_steer_request(run_id="r1", steer_msg=msg))
        assert r.status_code == 200 and r.json() == {"ok": True}
        assert inbound.queue.get_nowait() == msg  # reached the run's inbound queue
    finally:
        svc._STEER_RUNS.pop("r1", None)


def test_steer_for_an_unknown_run_is_404_not_a_silent_200():
    client = TestClient(svc.app)
    # 404 (not 200): the caller MUST learn the injection did not land, so the
    # producer leaves the row un-acked and it resurfaces as a fresh turn.
    r = client.post("/steer", json={"run_id": "nope", "steer": {"role": "user", "content": "x"}})
    assert r.status_code == 404 and r.json()["ok"] is False


def test_steer_rejects_a_non_object_message():
    inbound = svc._InboundSteer()
    svc._STEER_RUNS["r2"] = inbound
    try:
        client = TestClient(svc.app)
        r = client.post("/steer", json={"run_id": "r2", "steer": "not a dict"})
        assert r.status_code == 400
        assert inbound.queue.empty()  # nothing enqueued
    finally:
        svc._STEER_RUNS.pop("r2", None)


# ---------------------------------------------------------------- /capabilities


def test_capabilities_reports_the_in_container_drivers_set():
    client = TestClient(svc.app)
    caps = client.get("/capabilities", params={"framework": "nexus_power"}).json()
    # nexus_power in-process/subprocess CAN steer, so the executor advertises it —
    # this is what lets the orchestrator trust a remote run is steerable.
    assert "steering" in caps["capabilities"]


# -------------------------------------------------- /agent-loop consumed merge


class _FakeDriver:
    """A driver whose agent_loop emits scripted events and, between them, reports
    consumption on the steering channel it was handed — exactly what NexusAgent
    does when the runner drains a steer_inbox row."""

    def __init__(self, script):
        self._script = script

    async def agent_loop(self, *, steering=None, **kwargs):
        for kind, val in self._script:
            if kind == "consume":
                await steering.deliver_consumed(val)
            elif kind == "raise":
                raise RuntimeError(val)
            else:
                yield {"type": "text", "data": val}


def _ndjson(text):
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def test_agent_loop_interleaves_steer_consumed_frames_and_tears_down(monkeypatch):
    script = [("event", "a"), ("consume", ["r-1"]), ("event", "b")]
    monkeypatch.setattr(svc, "get_agent_loop_driver", lambda *a, **k: _FakeDriver(script))
    client = TestClient(svc.app)

    body = _body(run_id="live-1")
    resp = client.post("/agent-loop", json=body)
    assert resp.status_code == 200
    frames = _ndjson(resp.text)

    # The consumed frame rides its OWN frame type between the two events, so the
    # orchestrator's driver can forward it to the real channel and keep the event
    # stream clean.
    assert {"event": {"type": "text", "data": "a"}} in frames
    assert {"steer_consumed": ["r-1"]} in frames
    assert {"event": {"type": "text", "data": "b"}} in frames
    assert frames.index({"steer_consumed": ["r-1"]}) < frames.index(
        {"event": {"type": "text", "data": "b"}}
    )
    # The run handle must not linger once the stream ends (else /steer on a dead
    # run would wrongly succeed and the dict would leak across runs).
    assert "live-1" not in svc._STEER_RUNS


def test_consumed_reported_before_a_raise_is_flushed_ahead_of_the_error_frame(monkeypatch):
    # The whole value of the except-branch drain: the loop reports consumption on
    # its LAST step, then raises — the consumed frame must reach the orchestrator
    # BEFORE the error frame (the orchestrator stops reading at the error), or the
    # producer never learns it was consumed and re-injects it next turn (the
    # "never double" half of the contract breaks). Consume-then-raise (no event
    # after) is exactly the case only the except drain covers.
    script = [("event", "a"), ("consume", ["r-1"]), ("raise", "boom")]
    monkeypatch.setattr(svc, "get_agent_loop_driver", lambda *a, **k: _FakeDriver(script))
    client = TestClient(svc.app)

    resp = client.post("/agent-loop", json=_body(run_id="live-err"))
    frames = _ndjson(resp.text)

    consumed = next(i for i, f in enumerate(frames) if "steer_consumed" in f)
    error = next(i for i, f in enumerate(frames) if "error" in f)
    assert frames[consumed] == {"steer_consumed": ["r-1"]}
    assert consumed < error  # consumed flushed BEFORE the error, not lost
    assert frames[error]["error"]["type"] == "RuntimeError"
    assert "live-err" not in svc._STEER_RUNS  # torn down even on the error path


def test_agent_loop_without_run_id_registers_nothing(monkeypatch):
    script = [("event", "only")]
    monkeypatch.setattr(svc, "get_agent_loop_driver", lambda *a, **k: _FakeDriver(script))
    client = TestClient(svc.app)

    before = dict(svc._STEER_RUNS)
    resp = client.post("/agent-loop", json=_body())  # no run_id → non-steerable
    frames = _ndjson(resp.text)
    assert {"event": {"type": "text", "data": "only"}} in frames
    assert not any("steer_consumed" in f for f in frames)
    assert dict(svc._STEER_RUNS) == before  # registered nothing

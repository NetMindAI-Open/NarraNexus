"""feedback_client unit tests — payload contract, kill switch, failure swallowing."""
import asyncio

import pytest

from xyz_agent_context.services import feedback_client as fc


class StubResponse:
    def __init__(self, status_code=204):
        self.status_code = status_code

    @property
    def is_success(self):
        return 200 <= self.status_code < 300


class StubClient:
    def __init__(self, status_code=204, exc=None):
        self.calls = []
        self._status = status_code
        self._exc = exc

    async def post(self, url, json=None, timeout=None):
        if self._exc:
            raise self._exc
        self.calls.append((url, json))
        return StubResponse(self._status)


def send(**kw):
    stub = kw.pop("stub", StubClient())
    ok = asyncio.run(fc.send_feedback(client=stub, **kw))
    return ok, stub


def test_payload_shape_and_hashing(monkeypatch):
    monkeypatch.delenv("NARRANEXUS_FEEDBACK_DISABLED", raising=False)
    ok, stub = send(category="error", summary="  something broke  ",
                    severity="high", source="agent",
                    agent_id="agent_123", user_id="alice", channel="lark")
    assert ok is True
    url, payload = stub.calls[0]
    assert url == fc.DEFAULT_FEEDBACK_URL
    assert payload["summary"] == "something broke"
    assert payload["severity"] == "high"
    assert payload["agent_hash"] == fc.hash_id("agent_123")
    assert payload["user_hash"] == fc.hash_id("alice")
    assert payload["agent_hash"] != "agent_123" and len(payload["agent_hash"]) == 16
    assert "agent_123" not in str(payload) and "alice" not in str(payload)


def test_kill_switch(monkeypatch):
    monkeypatch.setenv("NARRANEXUS_FEEDBACK_DISABLED", "1")
    ok, stub = send(category="error", summary="x", source="agent")
    assert ok is False
    assert stub.calls == []


def test_url_override(monkeypatch):
    monkeypatch.delenv("NARRANEXUS_FEEDBACK_DISABLED", raising=False)
    monkeypatch.setenv("NARRANEXUS_FEEDBACK_URL", "http://localhost:8100/api/feedback")
    ok, stub = send(category="other", summary="x", source="web_ui")
    assert stub.calls[0][0] == "http://localhost:8100/api/feedback"


def test_bad_category_and_severity_coerced(monkeypatch):
    monkeypatch.delenv("NARRANEXUS_FEEDBACK_DISABLED", raising=False)
    ok, stub = send(category="wat", summary="x", severity="apocalyptic", source="agent")
    _, payload = stub.calls[0]
    assert payload["category"] == "other"
    assert payload["severity"] == "medium"


def test_empty_summary_not_sent(monkeypatch):
    monkeypatch.delenv("NARRANEXUS_FEEDBACK_DISABLED", raising=False)
    ok, stub = send(category="error", summary="   ", source="agent")
    assert ok is False and stub.calls == []


def test_summary_truncated_to_500(monkeypatch):
    monkeypatch.delenv("NARRANEXUS_FEEDBACK_DISABLED", raising=False)
    ok, stub = send(category="error", summary="y" * 900, source="agent")
    assert len(stub.calls[0][1]["summary"]) == 500


def test_exception_swallowed(monkeypatch):
    monkeypatch.delenv("NARRANEXUS_FEEDBACK_DISABLED", raising=False)
    ok, _ = send(category="error", summary="x", source="agent",
                 stub=StubClient(exc=RuntimeError("boom")))
    assert ok is False  # never raises


def test_own_client_branch(monkeypatch):
    """The production path (no injected client) builds its own AsyncClient."""
    monkeypatch.delenv("NARRANEXUS_FEEDBACK_DISABLED", raising=False)
    calls = []

    class FakeAsyncClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None, timeout=None):
            calls.append((url, json, timeout))
            return StubResponse(204)

    monkeypatch.setattr(fc.httpx, "AsyncClient", FakeAsyncClient)
    ok = asyncio.run(fc.send_feedback(category="error", summary="own client path", source="agent"))
    assert ok is True
    assert calls[0][0] == fc.DEFAULT_FEEDBACK_URL
    assert calls[0][2] == 3.0


def test_delivered_accepts_any_2xx():
    stub = StubClient(status_code=200)
    ok, _ = send(category="error", summary="x", source="agent", stub=stub)
    assert ok is True

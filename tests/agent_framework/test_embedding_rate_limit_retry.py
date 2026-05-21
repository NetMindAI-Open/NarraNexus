"""
@file_name: test_embedding_rate_limit_retry.py
@author: Bin Liang
@date: 2026-05-21
@description: Embedding requests retry on rate-limit (429), not just network.

Regression (debug/20260521-embedding-rebuild-retry): on dev a rebuild for a
user hit a wall of `429 Rate limit exceeded` from the embedding provider.
The retry decorator on the embedding request only listed
(ConnectionError, TimeoutError, OSError), so a 429 — a transient,
back-off-able condition — failed the row immediately. Those rows then
stayed permanently "missing" and every subsequent rebuild re-attempted them
into the same wall.

Fix:
  - `with_retry` gains an optional `retry_on` predicate so callers can retry
    on conditions that aren't a fixed exception class (e.g. any provider's
    429, which different OpenAI-compatible aggregators raise differently).
  - embedding.py defines `_is_rate_limit_error` (duck-typed: status 429 /
    RateLimit class name / message) and wires it into both the single and
    batch embedding request retries, with a few-seconds backoff.
"""
from __future__ import annotations

import asyncio

import pytest

from xyz_agent_context.utils.retry import with_retry
from xyz_agent_context.agent_framework.llm_api import embedding as emb_mod


# ── retry_on predicate on with_retry ────────────────────────────────────

@pytest.mark.asyncio
async def test_with_retry_retries_when_predicate_matches():
    calls = {"n": 0}

    class _Boom(Exception):
        pass

    @with_retry(
        max_attempts=4,
        delay=0.01,
        backoff=1.0,
        exceptions=(),  # no type matches — only the predicate should fire
        retry_on=lambda e: isinstance(e, _Boom),
    )
    async def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise _Boom("transient")
        return "ok"

    assert await flaky() == "ok"
    assert calls["n"] == 3  # failed twice, succeeded on the third


@pytest.mark.asyncio
async def test_with_retry_does_not_retry_when_predicate_false():
    calls = {"n": 0}

    @with_retry(
        max_attempts=4,
        delay=0.01,
        exceptions=(),
        retry_on=lambda e: False,
    )
    async def boom():
        calls["n"] += 1
        raise ValueError("nope")

    with pytest.raises(ValueError):
        await boom()
    assert calls["n"] == 1  # propagated immediately, no retries


# ── _is_rate_limit_error duck-typing ────────────────────────────────────

class _FakeRateLimit(Exception):
    """Mimics openai.RateLimitError shape."""
    def __init__(self, msg="Rate limit exceeded"):
        super().__init__(msg)
        self.status_code = 429


def test_rate_limit_detected_by_status_code():
    assert emb_mod._is_rate_limit_error(_FakeRateLimit()) is True


def test_rate_limit_detected_by_class_name():
    class RateLimitError(Exception):
        pass
    assert emb_mod._is_rate_limit_error(RateLimitError("boom")) is True


def test_rate_limit_detected_by_message():
    assert emb_mod._is_rate_limit_error(Exception("Error code: 429 - too many requests")) is True
    assert emb_mod._is_rate_limit_error(Exception("rate limit exceeded")) is True


def test_non_rate_limit_errors_not_matched():
    assert emb_mod._is_rate_limit_error(ValueError("bad input")) is False
    assert emb_mod._is_rate_limit_error(KeyError("missing")) is False


# ── EmbeddingClient.embed retries a 429 then succeeds ───────────────────

class _FakeEmbeddingsAPI:
    """Stand-in for client.embeddings — raises 429 the first N calls."""
    def __init__(self, fail_times: int):
        self.fail_times = fail_times
        self.calls = 0

    async def create(self, **kwargs):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise _FakeRateLimit()

        class _Resp:
            class _D:
                embedding = [0.1, 0.2, 0.3]
            data = [_D()]
            usage = None
        return _Resp()


@pytest.mark.asyncio
async def test_embed_retries_through_rate_limit(monkeypatch):
    client = emb_mod.EmbeddingClient(model="m", api_key="test-key", base_url="", enable_cache=False)

    fake = _FakeEmbeddingsAPI(fail_times=2)
    # Swap the underlying OpenAI embeddings resource.
    monkeypatch.setattr(client._client, "embeddings", fake)

    # Keep the test fast — no real multi-second backoff sleeps. Patch the
    # asyncio.sleep that the retry decorator (utils.retry) actually calls,
    # with a plain no-op coroutine (NOT one that re-calls asyncio.sleep,
    # which would recurse infinitely).
    async def _instant(*_a, **_k):
        return None

    import xyz_agent_context.utils.retry as retry_mod
    monkeypatch.setattr(retry_mod.asyncio, "sleep", _instant)

    vector = await client.embed("hello")
    assert vector == [0.1, 0.2, 0.3]
    assert fake.calls == 3  # two 429s retried, third succeeded

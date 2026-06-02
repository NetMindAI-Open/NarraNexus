"""
@file_name: test_embedding_token_guard.py
@author: Bin Liang
@date: 2026-06-01
@description: Provider-agnostic token-budget guard for embedding requests.

Design: reference/self_notebook/specs/2026-06-01-embedding-anchor-redesign-design.md

The embedding limit is measured in TOKENS, not characters (NarraNexus is
CJK-heavy: char count misleads in both directions). We use tiktoken cl100k as
a conservative, provider-agnostic token counter (cl100k splits CJK finer than
bge-m3's XLM-R, so it is an UPPER bound — truncating to a cl100k budget keeps
bge-m3 safely under its own limit). A reactive `_is_context_length_error`
predicate + truncate-and-retry-once covers any tokenizer disagreement.
"""
from __future__ import annotations

import pytest

from xyz_agent_context.agent_framework.llm_api import embedding as emb_mod


# ── token counting / truncation (pure) ─────────────────────────────────

def test_count_tokens_uses_tiktoken():
    # cl100k_base: "hello world" tokenizes to exactly 2 tokens.
    assert emb_mod._count_tokens("hello world") == 2


def test_truncate_within_budget_is_unchanged():
    s = "a short query"
    assert emb_mod._truncate_to_token_budget(s, 100) == s


def test_truncate_over_budget_cuts_to_token_count():
    s = "word " * 1000  # well over 50 tokens
    out = emb_mod._truncate_to_token_budget(s, 50)
    assert emb_mod._count_tokens(out) <= 50
    assert len(out) < len(s)


def test_truncate_cjk_respects_token_budget():
    s = "中文测试" * 1000  # CJK, many tokens
    out = emb_mod._truncate_to_token_budget(s, 80)
    assert emb_mod._count_tokens(out) <= 80


# ── _is_context_length_error duck-typing ────────────────────────────────

def test_context_length_detected_by_message():
    assert emb_mod._is_context_length_error(
        Exception("This model's maximum context length is 8192 tokens")) is True
    assert emb_mod._is_context_length_error(
        Exception("error code: 400 - context_length_exceeded")) is True
    assert emb_mod._is_context_length_error(
        Exception("Please reduce the length of the input")) is True


def test_context_length_not_matched_for_other_errors():
    # 429 / rate-limit must NOT be treated as a context-length error.
    assert emb_mod._is_context_length_error(Exception("Error code: 429 - rate limit")) is False
    assert emb_mod._is_context_length_error(ValueError("bad input")) is False
    assert emb_mod._is_context_length_error(KeyError("missing")) is False


# ── job write-side text: FULL payload, no [:500] truncation ─────────────

def test_prepare_job_text_keeps_full_payload():
    long_payload = "执行步骤: " + ("x" * 3000)
    out = emb_mod.prepare_job_text_for_embedding("Title", "Desc", long_payload)
    assert long_payload in out  # full payload embedded, not cut to 500


# ── _make_embedding_request: proactive truncate + reactive retry ────────

class _FakeContextLenError(Exception):
    """Mimics a provider's 'maximum context length' 400."""
    def __init__(self):
        super().__init__("This model's maximum context length is 8192 tokens")
        self.status_code = 400


class _RecordingEmbeddingsAPI:
    """Records each call's `input`; optionally raises a context-length 400 once."""
    def __init__(self, fail_context_first: bool = False):
        self.fail_context_first = fail_context_first
        self.inputs: list = []
        self.calls = 0

    async def create(self, **kwargs):
        self.calls += 1
        self.inputs.append(kwargs["input"])
        if self.fail_context_first and self.calls == 1:
            raise _FakeContextLenError()

        class _Resp:
            class _D:
                embedding = [0.1, 0.2, 0.3]
            data = [_D()]
            usage = None
        return _Resp()


@pytest.mark.asyncio
async def test_make_request_truncates_proactively(monkeypatch):
    monkeypatch.setattr(emb_mod, "MAX_TOKENS_PER_REQUEST", 10)
    client = emb_mod.EmbeddingClient(model="m", api_key="k", base_url="", enable_cache=False)
    fake = _RecordingEmbeddingsAPI()
    monkeypatch.setattr(client._client, "embeddings", fake)

    await client._make_embedding_request("word " * 500)

    assert fake.calls == 1
    assert emb_mod._count_tokens(fake.inputs[0]) <= 10  # truncated before send


@pytest.mark.asyncio
async def test_make_request_retries_once_on_context_length(monkeypatch):
    client = emb_mod.EmbeddingClient(model="m", api_key="k", base_url="", enable_cache=False)
    fake = _RecordingEmbeddingsAPI(fail_context_first=True)
    monkeypatch.setattr(client._client, "embeddings", fake)

    vec = await client._make_embedding_request("word " * 200)

    assert vec == [0.1, 0.2, 0.3]
    assert fake.calls == 2  # first 400 → halved → second succeeds
    assert emb_mod._count_tokens(fake.inputs[1]) < emb_mod._count_tokens(fake.inputs[0])


@pytest.mark.asyncio
async def test_batch_request_truncates_each_proactively(monkeypatch):
    monkeypatch.setattr(emb_mod, "MAX_TOKENS_PER_REQUEST", 10)
    client = emb_mod.EmbeddingClient(model="m", api_key="k", base_url="", enable_cache=False)
    captured: dict = {}

    class _BatchAPI:
        async def create(self, **kwargs):
            captured["input"] = kwargs["input"]

            class _Resp:
                data = [type("D", (), {"embedding": [0.1]})() for _ in kwargs["input"]]
                usage = None
            return _Resp()

    monkeypatch.setattr(client._client, "embeddings", _BatchAPI())

    await client._make_batch_embedding_request(["word " * 500, "中文测试" * 500])

    assert all(emb_mod._count_tokens(t) <= 10 for t in captured["input"])

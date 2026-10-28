"""
@file_name: test_nexus_request_identity.py
@date: 2026-09-11
@description: NexusAgent payload — per-agent request identifiers.

Locks:
- anthropic protocol -> litellm ``user`` = agent_id, which litellm's
  anthropic route puts on the wire as ``metadata.user_id``.
- openai protocol -> ``prompt_cache_key`` = agent_id via ``extra_body``,
  only to our own gateway / an explicit official OpenAI base_url; never
  to an arbitrary BYOK host (extra_body bypasses drop_params, so a strict
  host would 400), and never with an empty base_url (litellm then routes
  by model prefix, destination unknown).
- anthropic is deliberately NOT host-gated (metadata.user_id is part of
  the Messages API itself).
- an existing extra_body is merged, not replaced.
- no real agent id -> nothing is added.
- Wire: what the adapter emits really reaches the HTTP body through
  LitellmClient (a plain ``prompt_cache_key`` kwarg is silently dropped
  by litellm 1.94 — the reason extra_body is used).
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from narranexus_plugins.frameworks_nexus_power.adapter.nexus_agent import (
    NexusAgent,
    _merge_llm_extra,
    claude_config,
    codex_config,
)
from narranexus.platform.agent_framework.llm.litellm_client import LitellmClient


@pytest.fixture()
def anthropic_slot(monkeypatch):
    monkeypatch.setattr(claude_config, "model", "deepseek-v4-flash")
    monkeypatch.setattr(claude_config, "api_key", "k")
    monkeypatch.setattr(claude_config, "base_url", "http://llm-gateway:4000")
    monkeypatch.setattr(claude_config, "auth_type", "api_key")
    monkeypatch.setattr(claude_config, "identity_token", "")


@pytest.fixture()
def openai_slot(monkeypatch):
    monkeypatch.setattr(claude_config, "model", "")
    monkeypatch.setattr(codex_config, "model", "deepseek-ai/DeepSeek-V4-Flash")
    monkeypatch.setattr(codex_config, "api_key", "k")
    monkeypatch.setattr(codex_config, "base_url", "http://llm-gateway:4000")
    monkeypatch.setattr(codex_config, "auth_type", "api_key")
    monkeypatch.setattr(codex_config, "identity_token", "")


def _llm_extra(**kwargs):
    agent = NexusAgent(working_path="/tmp")
    payload = agent._build_request_payload(
        messages=[{"role": "user", "content": "hi"}],
        mcp_servers={},
        extra_env=None,
        kwargs=kwargs,
    )
    return payload["options"]["llm_extra"]


def test_anthropic_sends_agent_id_as_user(anthropic_slot):
    extra = _llm_extra(agent_id="agent_abc")
    assert extra["user"] == "agent_abc"
    assert "extra_body" not in extra


def test_openai_own_gateway_sends_prompt_cache_key(openai_slot):
    extra = _llm_extra(agent_id="agent_abc")
    assert extra["extra_body"] == {"prompt_cache_key": "agent_abc"}
    assert "user" not in extra


@pytest.mark.parametrize(
    "base_url", ["https://api.openai.com/v1", "https://api.openai.com/v1/"]
)
def test_openai_official_sends_prompt_cache_key(openai_slot, monkeypatch, base_url):
    monkeypatch.setattr(codex_config, "base_url", base_url)
    extra = _llm_extra(agent_id="agent_abc")
    assert extra["extra_body"] == {"prompt_cache_key": "agent_abc"}


def test_openai_byok_third_party_gets_nothing(openai_slot, monkeypatch):
    monkeypatch.setattr(codex_config, "base_url", "https://some-byok.example/v1")
    extra = _llm_extra(agent_id="agent_abc")
    assert "extra_body" not in extra
    assert "user" not in extra


@pytest.mark.parametrize(
    "model", ["gpt-5", "groq/llama-3.3-70b", "mistral/mistral-large"]
)
def test_openai_empty_base_url_is_fail_closed(openai_slot, monkeypatch, model):
    # Empty base_url: _litellm_model passes the id to litellm's prefix
    # routing, so the request may land on any vendor.
    monkeypatch.setattr(codex_config, "base_url", "")
    monkeypatch.setattr(codex_config, "model", model)
    extra = _llm_extra(agent_id="agent_abc")
    assert "extra_body" not in extra


def test_anthropic_byok_still_sends_user(anthropic_slot, monkeypatch):
    monkeypatch.setattr(claude_config, "base_url", "https://some-byok.example")
    assert _llm_extra(agent_id="agent_abc")["user"] == "agent_abc"


def test_existing_extra_body_is_merged():
    llm_extra = {"extra_body": {"other": 1}, "reasoning_effort": "low"}
    _merge_llm_extra(llm_extra, {"extra_body": {"prompt_cache_key": "a1"}})
    assert llm_extra == {
        "extra_body": {"other": 1, "prompt_cache_key": "a1"},
        "reasoning_effort": "low",
    }


def test_payload_folds_identity_through_merge(openai_slot, monkeypatch):
    # The call site must merge, not llm_extra.update(): no other extra_body
    # producer exists today, so only a spy can tell the two apart.
    from narranexus_plugins.frameworks_nexus_power.adapter import nexus_agent

    calls = []
    real = nexus_agent._merge_llm_extra

    def _spy(llm_extra, params):
        calls.append(dict(params))
        real(llm_extra, params)

    monkeypatch.setattr(nexus_agent, "_merge_llm_extra", _spy)
    extra = _llm_extra(agent_id="agent_abc")
    assert calls == [{"extra_body": {"prompt_cache_key": "agent_abc"}}]
    assert extra["extra_body"] == {"prompt_cache_key": "agent_abc"}


@pytest.mark.parametrize("agent_id", [None, "", "agent"])
def test_no_real_agent_id_adds_nothing(anthropic_slot, agent_id):
    extra = _llm_extra(agent_id=agent_id)
    assert "user" not in extra
    assert "extra_body" not in extra


# -- wire ---------------------------------------------------------------


@pytest.fixture()
def capture_server(monkeypatch):
    # A corporate proxy in the environment would swallow the loopback call.
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
        monkeypatch.delenv(name, raising=False)
        monkeypatch.delenv(name.lower(), raising=False)
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    monkeypatch.setenv("no_proxy", "127.0.0.1")
    bodies: list[dict] = []

    class _Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            length = int(self.headers["Content-Length"])
            bodies.append(json.loads(self.rfile.read(length)))
            self.send_response(500)
            self.end_headers()
            self.wfile.write(b"{}")

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", bodies
    finally:
        server.shutdown()
        server.server_close()


async def _send(model: str, base_url: str, extra: dict) -> None:
    try:
        async for _ in LitellmClient(default_timeout_s=10).stream_chat(
            model=model,
            messages=[{"role": "user", "content": "hi"}],
            api_key="k",
            base_url=base_url,
            extra=extra,
        ):
            pass
    except Exception:
        pass  # the capture server answers 500; only the request body matters


@pytest.mark.asyncio
async def test_wire_openai_body_carries_prompt_cache_key(
    openai_slot, capture_server
):
    url, bodies = capture_server
    extra = _llm_extra(agent_id="agent_abc")
    await _send("openai/deepseek-ai/DeepSeek-V4-Flash", url, extra)
    assert bodies and bodies[-1].get("prompt_cache_key") == "agent_abc"
    assert "metadata" not in bodies[-1]


@pytest.mark.asyncio
async def test_wire_anthropic_body_carries_metadata_user_id(
    anthropic_slot, capture_server
):
    url, bodies = capture_server
    extra = _llm_extra(agent_id="agent_abc")
    await _send("anthropic/deepseek-v4-flash", url, extra)
    assert bodies and bodies[-1].get("metadata") == {"user_id": "agent_abc"}
    # Translated, not passed through: Anthropic rejects unknown top-level keys.
    assert "user" not in bodies[-1]

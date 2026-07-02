"""
@file_name: test_executor_warmup.py
@date: 2026-06-18
@description: A cold-started executor must be waited for (poll /health) before
the agent loop drives it — otherwise the first connection races the container
boot and the run wrongly drops into the fallback path.
"""
import pytest

from xyz_agent_context.agent_framework import broker_client


@pytest.mark.asyncio
async def test_wait_until_ready_polls_until_healthy(monkeypatch):
    calls = {"n": 0}

    async def _fake_healthy(url):
        calls["n"] += 1
        return calls["n"] >= 3  # becomes healthy on the 3rd poll (still booting before)

    monkeypatch.setattr(broker_client, "_executor_healthy", _fake_healthy)
    await broker_client.wait_until_ready("http://nx-exec-x:8020", timeout=5, interval=0.01)
    assert calls["n"] == 3  # waited, didn't give up early


@pytest.mark.asyncio
async def test_wait_until_ready_returns_immediately_when_healthy(monkeypatch):
    async def _fake_healthy(url):
        return True

    monkeypatch.setattr(broker_client, "_executor_healthy", _fake_healthy)
    await broker_client.wait_until_ready("http://nx-exec-x:8020", timeout=5)  # no raise


@pytest.mark.asyncio
async def test_wait_until_ready_raises_when_never_ready(monkeypatch):
    async def _fake_healthy(url):
        return False

    monkeypatch.setattr(broker_client, "_executor_healthy", _fake_healthy)
    with pytest.raises(RuntimeError):
        await broker_client.wait_until_ready(
            "http://nx-exec-x:8020", timeout=0.05, interval=0.01
        )

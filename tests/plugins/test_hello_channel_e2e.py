"""
@file_name: test_hello_channel_e2e.py
@author: Bin Liang
@date: 2026-09-04
@description: Batch 4 exit criterion — a non-builtin channel installs as a plugin and sends/receives: linked through the CLI, booted, bound through the generic route, fed through the webhook endpoint, its trigger runs the agent with its own WorkingSource and its module delivers the reply.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

import backend.routes._ownership as own
from backend.routes.channels import generic as generic_mod
from narranexus.cli.main import main as cli
from narranexus.hosts.boot import boot
from narranexus.kernel.plugins.importer import import_plugin_module, plugin_finder, uninstall_synthetic_package
from narranexus.kernel.plugins.lifecycle import RegistryStore
from narranexus.kernel.plugins.paths import ENV_PLUGIN_HOME, registry_path
from narranexus.kernel.plugins.registries import Registries
from narranexus.platform.channel import credential_codec
from narranexus.platform.module_system.channel_trigger_map import TriggerMapView
from narranexus.kernel.plugins.builtins import load_builtins
from narranexus.platform.module_system.data_access.channel_store import _ChannelSpecs
from narranexus.platform.schema.hook_schema import WorkingSource

PLUGIN = Path(__file__).resolve().parent / "hello_channel"
PID = "acme.hello_channel"


@pytest.fixture
def home(tmp_path: Path, monkeypatch):
    (tmp_path / "home").mkdir()
    monkeypatch.setenv(ENV_PLUGIN_HOME, str(tmp_path / "home"))
    credential_codec.use_key_dir(tmp_path / "keys")
    yield tmp_path / "home"
    uninstall_synthetic_package(PID)
    plugin_finder().unregister_deps(PID)
    credential_codec.use_key_dir(None)


def test_a_channel_plugin_installs_binds_receives_and_replies(home: Path, db_client, monkeypatch):
    assert cli(["plugin", "link", str(PLUGIN)]) == 0
    regs = Registries()
    load_builtins(regs, "backend")
    report = boot("workers", registries=regs, cloud=False, host_version="1.19.0", store=RegistryStore(path=registry_path()))
    assert PID in report.user_plugin_ids and not report.isolated
    # the platform's channel views know the plugin channel
    assert "hello_channel" in TriggerMapView(regs) and "hello_channel" not in _ChannelSpecs(regs)  # no bespoke manager: generic store only
    assert WorkingSource("hello_channel").is_from_human() and WorkingSource.is_channel("hello_channel")
    monkeypatch.setattr("narranexus.kernel.plugins.registries.KERNEL_REGISTRIES", regs)

    # bind through the generic route (owner-gated), receive the one-time webhook secret
    async def _db():
        return db_client

    monkeypatch.setattr(own, "get_db_client", _db)
    monkeypatch.setattr(generic_mod, "_db", _db)

    async def _resolve(self, agent_id):
        return "u1"

    monkeypatch.setattr(own.AgentRepository, "resolve_owner", _resolve)
    app = FastAPI()

    @app.middleware("http")
    async def _identity(request: Request, call_next):
        request.state.user_id = request.headers.get("X-User-Id")
        return await call_next(request)

    app.include_router(generic_mod.router, prefix="/api/channels")
    client = TestClient(app)
    schema = client.get("/api/channels/hello_channel/schema").json()["data"]
    assert [f["name"] for f in schema["fields"]] == ["api_token", "bot_id", "workspace"] and schema["transport"] == "webhook"
    bind = client.post("/api/channels/hello_channel/bind", json={"agent_id": "agent_h", "fields": {"api_token": "tok", "bot_id": "bot-1"}}, headers={"X-User-Id": "u1"}).json()
    assert bind["success"] and bind["data"]["external_id"] == "bot-1"
    secret = bind["data"]["webhook_secret"]

    # the external platform pushes a message
    r = client.post("/api/channels/hello_channel/webhook/agent_h", json={"message_id": "m1", "chat_id": "room-1", "sender_id": "alice", "sender_name": "Alice", "text": "hello agent"}, headers={"X-Webhook-Token": secret})
    assert r.json() == {"success": True, "queued": True}

    # the trigger (workers process) picks the credential up, drains the inbox and runs the agent
    plugin = import_plugin_module(PID)
    trigger_cls = TriggerMapView(regs)["hello_channel"]
    assert trigger_cls is plugin.HelloChannelTrigger
    runs: list[dict] = []

    class FakeClient:
        async def run_and_collect(self, **kwargs):
            runs.append(kwargs)
            return SimpleNamespace(is_error=False, error=None, output_text="hi Alice", raw_items=[])

    monkeypatch.setattr("narranexus.platform.agent_runtime.client.get_agent_runtime_client", lambda: FakeClient())

    async def scenario():
        trigger = trigger_cls(max_workers=1)
        await trigger.pre_start(db_client)
        await trigger.start(db_client)
        try:
            for _ in range(200):
                if runs:
                    break
                await asyncio.sleep(0.05)
        finally:
            await trigger.stop()

    asyncio.run(scenario())
    assert len(runs) == 1, runs
    run = runs[0]
    assert run["agent_id"] == "agent_h" and run["working_source"] == "hello_channel" and "hello agent" in run["input_content"]
    channel_tag = run["trigger_extra_data"].get("channel_tag")
    assert isinstance(channel_tag, dict), f"channel_tag must be a dict, got: {channel_tag!r}"
    assert channel_tag["channel"] == "hello_channel"

    # the module's send tool delivers through the channel
    module = plugin.HelloChannelModule(agent_id="agent_h", user_id="u1", database_client=db_client, instance_id="inst_h")
    asyncio.run(module.send_to_agent("agent_h", "room-1", "hi Alice"))
    assert plugin.SENT[-1] == {"agent_id": "agent_h", "target_id": "room-1", "message": "hi Alice"}
    from narranexus.platform.channel.channel_sender_registry import ChannelSenderRegistry

    assert ChannelSenderRegistry.get_sender("hello_channel") is not None

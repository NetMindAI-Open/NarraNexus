"""Hello World — the plugin that exercises every contribution kind (used by the e2e suite)."""
from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path

from fastapi import APIRouter

from narranexus.sdk import (
    BundleSpec,
    ColumnSpec,
    Contribution,
    McpServerSpec,
    RouterSpec,
    SettingField,
    SettingsSchema,
    SkillSpec,
    TableSpec,
    ToolSpec,
    WorkerSpec,
    hookimpl,
    plugin_route_prefix,
)

_ROOT = Path(__file__).resolve().parent.parent
ACTIVATIONS: list[str] = []
HOOK_CALLS: list[str] = []

router = APIRouter()


@router.get("/hello")
async def hello():
    return {"plugin": "acme.hello_world", "message": "hello"}


webhook = APIRouter()


@webhook.post("/ping")
async def ping():
    return {"ok": True}


ROUTES = (
    Contribution("hello_world_api", lambda: RouterSpec(router, plugin_route_prefix("acme.hello_world"))),
    Contribution("hello_world_webhook", lambda: RouterSpec(webhook, plugin_route_prefix("acme.hello_world") + "/webhook", auth="none")),
)
TABLES = (
    Contribution(
        "greetings",
        lambda: TableSpec(
            "ext_acme_hello_world__greetings",
            (ColumnSpec("id", "INTEGER", "BIGINT UNSIGNED", nullable=False, primary_key=True, auto_increment=True), ColumnSpec("text", "TEXT", "VARCHAR(255)", nullable=False)),
        ),
    ),
)


class _Handle:
    """Runs until stopped, like a real worker: a handle whose ``run`` returns at once
    makes the workers supervisor restart it in a loop when the plugin is linked
    into a real host."""

    def __init__(self) -> None:
        self.stopped = False
        self._stop = asyncio.Event()
        self.run = self._stop.wait()

    def stop(self) -> None:
        self.stopped = True
        self._stop.set()


async def _worker_factory(ctx):
    return _Handle()


WORKERS = (Contribution("hello_world_greeter", lambda: WorkerSpec("hello_world_greeter", _worker_factory, host="workers")),)


@hookimpl("onDidPersistTurn")
async def after_turn(run_id, agent_id):
    HOOK_CALLS.append(f"{agent_id}:{run_id}")


HOOKS = (after_turn,)
SETTINGS = (Contribution("hello_world_settings", lambda: SettingsSchema({"greeting": SettingField("string", default="hi"), "token": SettingField("string", secret=True)})),)


class _Tools:
    def list_tools(self):
        return (ToolSpec("hello_wave", "Wave hello", server="hello_world"), ToolSpec("hello_now", "Always visible", server="hello_world", always_visible=True))


TOOLS = (Contribution("hello_world", _Tools),)
MCP_SERVERS = (Contribution("hello_world", lambda: McpServerSpec("hello_world", "streamable_http", url="https://example.com/mcp")),)


def _bundle() -> BundleSpec:
    path = _ROOT / "bundles" / "hello.nxbundle"
    return BundleSpec("acme.hello_world.team", path, hashlib.sha256(path.read_bytes()).hexdigest(), "Hello team", "demo")


BUNDLES = (Contribution("hello_world_team", _bundle),)
SKILLS = (Contribution("hello_skill", lambda: SkillSpec(_ROOT / "skills" / "hello_skill")),)


# ---- the vertical axis: a Recall strategy + a profile that names it (spec §7.8 scenario 1)
from narranexus.contracts.agent.pipeline import PipelineProfile  # noqa: E402
from narranexus.contracts.agent.stages import Stage  # noqa: E402


class HelloRecall:
    """A stand-in graph recall: no narratives, no LLM."""

    stage = Stage.RECALL

    async def run(self, inputs):
        inputs.ctx.narrative_list = []
        return
        yield  # pragma: no cover


RECALL_STRATEGIES = (Contribution("hello_graph", HelloRecall),)
PROFILES = (Contribution("hello_research", lambda: PipelineProfile(id="hello_research", strategies={Stage.RECALL: "hello_graph"})),)


# ---- the horizontal axis, L2: a context provider (spec §7.8 scenario 2)
class HelloCalendar:
    name = "hello_calendar"
    context_cost_hint = 40

    async def contribute_turn_context(self, ctx_data):
        return "Hello World says: it is a fine day for plugins."


CONTEXT_PROVIDERS = (Contribution("hello_calendar", HelloCalendar),)


def activate(ctx):
    ACTIVATIONS.append(ctx.plugin_id)
    ctx.log.info(f"greeting={ctx.settings.get('greeting')}")


def deactivate(ctx):
    ACTIVATIONS.append("deactivated")

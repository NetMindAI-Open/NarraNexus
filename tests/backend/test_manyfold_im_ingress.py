"""
@file_name: test_manyfold_im_ingress.py
@author: NexusAgent
@date: 2026-08-03
@description: Managed-IM inbound dispatch — build_inbound_run_context mapping
(channel_provider/channel_context → WorkingSource + ChannelTag + extra_data)
and the /v1/chat/completions wiring that hands the mapped context to
BackgroundRun. Design: specs/2026-08-03-manyfold-managed-im-ingress-design.md.
"""

import asyncio

import httpx
import pytest
from fastapi import FastAPI, Request
from httpx import ASGITransport

import backend.routes.manyfold.sync as sync_mod
import backend.routes.openai_compat as compat_mod
from narranexus.platform.schema.channel_tag import ChannelTag
from narranexus.platform.schema.hook_schema import WorkingSource


# ---------------------------------------------------------------------------
# build_inbound_run_context — provider mapping
# ---------------------------------------------------------------------------


def _plain_expected(user_input: str, session_id: str):
    return (
        WorkingSource.MANYFOLD,
        user_input,
        {"trigger_id": session_id, "retrieval_anchor": user_input},
    )


def test_no_provider_is_plain_manyfold_turn():
    got = sync_mod.build_inbound_run_context(
        channel_provider=None,
        channel_context=None,
        user_input="hello",
        session_id="manyfold_ab12cd34",
    )
    assert got == _plain_expected("hello", "manyfold_ab12cd34")


@pytest.mark.parametrize("provider", ["", "matrix", "fake", "unknown-thing"])
def test_unknown_provider_falls_back_to_manyfold(provider):
    got = sync_mod.build_inbound_run_context(
        channel_provider=provider,
        channel_context={"room_id": "r1"},
        user_input="hi",
        session_id="s1",
    )
    assert got == _plain_expected("hi", "s1")


@pytest.mark.parametrize(
    "provider,expected",
    [
        ("lark", WorkingSource.LARK),
        ("slack", WorkingSource.SLACK),
        ("telegram", WorkingSource.TELEGRAM),
        ("discord", WorkingSource.DISCORD),
        ("wechat", WorkingSource.WECHAT),
        ("narramessenger", WorkingSource.NARRAMESSENGER),
    ],
)
def test_known_providers_map_to_working_source(provider, expected):
    ws, _, _ = sync_mod.build_inbound_run_context(
        channel_provider=provider,
        channel_context={},
        user_input="m",
        session_id="s1",
    )
    assert ws is expected


def test_provider_matching_is_case_and_whitespace_insensitive():
    ws, _, _ = sync_mod.build_inbound_run_context(
        channel_provider="  Lark ",
        channel_context={},
        user_input="m",
        session_id="s1",
    )
    assert ws is WorkingSource.LARK


# ---------------------------------------------------------------------------
# build_inbound_run_context — channel turn shape
# ---------------------------------------------------------------------------


def _lark_context():
    return {
        "room_id": "oc_room1",
        "sender_id": "ou_sender1",
        "sender_name": "Alice",
        "source_message_id": "om_msg1",
    }


def test_channel_turn_prefixes_input_with_channel_tag():
    _, run_input, _ = sync_mod.build_inbound_run_context(
        channel_provider="lark",
        channel_context=_lark_context(),
        user_input="what's up",
        session_id="s1",
    )
    tag = ChannelTag(
        channel="lark",
        sender_name="Alice",
        sender_id="ou_sender1",
        room_id="oc_room1",
    )
    assert run_input == f"{tag.format()}\nwhat's up"


def test_channel_turn_extra_data_shape():
    _, _, extra = sync_mod.build_inbound_run_context(
        channel_provider="lark",
        channel_context=_lark_context(),
        user_input="what's up",
        session_id="s1",
    )
    assert extra["channel_tag"] == {
        "channel": "lark",
        "sender_name": "Alice",
        "sender_id": "ou_sender1",
        "room_id": "oc_room1",
    }
    # Native channel triggers anchor retrieval on "[From <name>] <body>".
    assert extra["retrieval_anchor"] == "[From Alice] what's up"
    # trigger_id follows the native f"{channel}_{message_id}" convention.
    assert extra["trigger_id"] == "lark_om_msg1"
    assert extra["source_message_id"] == "om_msg1"


def test_trigger_id_falls_back_to_session_id_without_message_id():
    ctx = _lark_context()
    ctx["source_message_id"] = None
    _, _, extra = sync_mod.build_inbound_run_context(
        channel_provider="lark",
        channel_context=ctx,
        user_input="m",
        session_id="manyfold_zz99",
    )
    assert extra["trigger_id"] == "manyfold_zz99"
    assert extra["source_message_id"] == ""


def test_sender_name_falls_back_to_sender_id_then_user():
    _, run_input, _ = sync_mod.build_inbound_run_context(
        channel_provider="wechat",
        channel_context={"room_id": "wx1", "sender_id": "wx1", "sender_name": None},
        user_input="m",
        session_id="s1",
    )
    assert "wx1" in run_input.splitlines()[0]

    _, run_input2, extra2 = sync_mod.build_inbound_run_context(
        channel_provider="wechat",
        channel_context={},
        user_input="m",
        session_id="s1",
    )
    assert extra2["channel_tag"]["sender_name"] == "user"
    assert run_input2.splitlines()[0].startswith("[Wechat · user")


def test_optional_fields_only_present_when_provided():
    _, _, bare = sync_mod.build_inbound_run_context(
        channel_provider="lark",
        channel_context=_lark_context(),
        user_input="m",
        session_id="s1",
    )
    for key in ("chat_type", "thread_id", "reply_token", "is_mention"):
        assert key not in bare
    assert "manyfold_attachments" not in bare

    ctx = _lark_context() | {
        "chat_type": "group",
        "thread_id": "t1",
        "reply_token": "ctx_tok",
        "is_mention": False,
        "attachments": [{"name": "a.png", "mime": "image/png", "size": 10, "path": "p"}],
    }
    _, _, full = sync_mod.build_inbound_run_context(
        channel_provider="lark",
        channel_context=ctx,
        user_input="m",
        session_id="s1",
    )
    assert full["chat_type"] == "group"
    assert full["thread_id"] == "t1"
    assert full["reply_token"] == "ctx_tok"
    assert full["is_mention"] is False
    assert full["manyfold_attachments"] == ctx["attachments"]


def test_context_values_are_coerced_and_junk_tolerated():
    _, _, extra = sync_mod.build_inbound_run_context(
        channel_provider="telegram",
        channel_context={
            "room_id": -100123,
            "sender_id": 42,
            "source_message_id": 7,
            "thread_id": 9,
            "attachments": "not-a-list",
        },
        user_input="m",
        session_id="s1",
    )
    assert extra["channel_tag"]["room_id"] == "-100123"
    assert extra["channel_tag"]["sender_id"] == "42"
    assert extra["trigger_id"] == "telegram_7"
    assert extra["thread_id"] == "9"
    assert "manyfold_attachments" not in extra


def test_non_dict_context_is_treated_as_empty():
    ws, run_input, extra = sync_mod.build_inbound_run_context(
        channel_provider="discord",
        channel_context="garbage",  # type: ignore[arg-type]
        user_input="m",
        session_id="s1",
    )
    assert ws is WorkingSource.DISCORD
    assert extra["channel_tag"]["sender_name"] == "user"
    assert run_input.endswith("\nm")


# ---------------------------------------------------------------------------
# /v1/chat/completions wiring — mapped context reaches BackgroundRun.drive
# ---------------------------------------------------------------------------


class _FakeBroadcaster:
    def subscribe(self, session_id):
        async def _events():
            for event in list(_FakeBackgroundRun.events):
                yield event

        return _events()

    def unsubscribe(self, session_id):
        pass


class _FakeBackgroundRun:
    instances: list = []
    events: list = []

    def __init__(self, **kwargs):
        self.init_kwargs = kwargs
        self.drive_kwargs = None
        self.task = None
        self.ready_event = asyncio.Event()
        self.ready_event.set()
        self.broadcaster = _FakeBroadcaster()
        _FakeBackgroundRun.instances.append(self)

    async def drive(self, **kwargs):
        self.drive_kwargs = kwargs


@pytest.fixture
def compat_app(monkeypatch):
    _FakeBackgroundRun.instances = []
    _FakeBackgroundRun.events = []

    async def fake_creator(agent_id):
        return "user_1"

    async def fake_db():
        return object()

    monkeypatch.setattr(compat_mod, "_resolve_agent_creator", fake_creator)
    monkeypatch.setattr(compat_mod, "get_db_client", fake_db)
    monkeypatch.setattr(compat_mod, "BackgroundRun", _FakeBackgroundRun)

    app = FastAPI()

    @app.middleware("http")
    async def _authed(request: Request, call_next):
        request.state.manyfold_authed = True
        return await call_next(request)

    app.include_router(compat_mod.router)
    app.state.active_runs = {}
    from backend.run_lifecycle import RunTasks

    app.state.run_tasks = RunTasks()
    return app


async def _post_completions(app, body):
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        return await client.post("/v1/chat/completions", json=body)


async def test_endpoint_plain_turn_stays_manyfold(compat_app):
    resp = await _post_completions(
        compat_app,
        {
            "model": "agent_x",
            "messages": [{"role": "user", "content": "hello"}],
            "stream": False,
        },
    )
    assert resp.status_code == 200
    run = _FakeBackgroundRun.instances[-1]
    assert run.drive_kwargs["working_source"] is WorkingSource.MANYFOLD
    assert run.drive_kwargs["input_content"] == "hello"
    assert run.drive_kwargs["trigger_extra_data"]["retrieval_anchor"] == "hello"


async def test_endpoint_channel_turn_dispatches_working_source(compat_app):
    resp = await _post_completions(
        compat_app,
        {
            "model": "agent_x",
            "messages": [{"role": "user", "content": "hi there"}],
            "stream": False,
            "channel_provider": "lark",
            "channel_context": _lark_context(),
        },
    )
    assert resp.status_code == 200
    run = _FakeBackgroundRun.instances[-1]
    assert run.drive_kwargs["working_source"] is WorkingSource.LARK
    assert run.drive_kwargs["input_content"].startswith("[Lark · Alice")
    extra = run.drive_kwargs["trigger_extra_data"]
    assert extra["channel_tag"]["room_id"] == "oc_room1"
    assert extra["trigger_id"] == "lark_om_msg1"


async def test_endpoint_unknown_channel_provider_is_ignored(compat_app):
    resp = await _post_completions(
        compat_app,
        {
            "model": "agent_x",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": False,
            "channel_provider": "matrix",
            "channel_context": {"room_id": "!r:hs"},
        },
    )
    assert resp.status_code == 200
    run = _FakeBackgroundRun.instances[-1]
    assert run.drive_kwargs["working_source"] is WorkingSource.MANYFOLD
    assert run.drive_kwargs["input_content"] == "hi"


# ---------------------------------------------------------------------------
# Stage B — reply classification via MessageSourceRegistry declaration chain
# ---------------------------------------------------------------------------

from narranexus.platform.channel.message_source_handler import (  # noqa: E402
    MessageSourceHandler,
    MessageSourceRegistry,
)

_DEFAULT = MessageSourceRegistry.get("nonexistent-source")


def _tool_event(tool_name, arguments):
    return {
        "type": "progress",
        "details": {"tool_name": tool_name, "arguments": arguments},
    }


def test_classify_owner_chat_reply_tool_is_content():
    kind, payload = compat_mod._classify_event(
        _tool_event("mcp__chat_module__notify_owner", {"content": "hi"}),
        _DEFAULT,
    )
    assert (kind, payload) == ("content", "hi")


def test_classify_other_tool_is_tool_call_under_default_handler():
    kind, payload = compat_mod._classify_event(
        _tool_event("lark_cli", {"command": "im +messages-send --chat-id c --markdown yo"}),
        _DEFAULT,
    )
    assert kind == "tool_call"


def test_classify_uses_channel_extractor_for_send_and_tool_call_for_rest():
    def _extract(tool_name, args):
        if "cli" in tool_name and "--markdown" in args.get("command", ""):
            return args["command"].split("--markdown ", 1)[1]
        return None

    handler = MessageSourceHandler(
        name="larkish",
        user_reply_tool_names=("lark_cli",),
        extract_reply_fn=_extract,
    )
    kind, payload = compat_mod._classify_event(
        _tool_event("mcp__lark_module__lark_cli", {"command": "im +messages-send --markdown hello!"}),
        handler,
    )
    assert (kind, payload) == ("content", "hello!")

    kind2, _ = compat_mod._classify_event(
        _tool_event("mcp__lark_module__lark_cli", {"command": "im +chat-messages-list"}),
        handler,
    )
    assert kind2 == "tool_call"


def test_classify_non_tool_channels_unchanged_by_handler():
    assert compat_mod._classify_event(
        {"type": "agent_thinking", "thinking_content": "mm"}, _DEFAULT
    ) == ("reasoning", "mm")
    assert compat_mod._classify_event(
        {"type": "agent_response", "delta": "tok"}, _DEFAULT
    ) == ("reasoning", "tok")
    assert compat_mod._classify_event(
        {"type": "agent_tool_output", "details": {"output": "res"}}, _DEFAULT
    ) == ("tool_result", "res")


async def test_stream_fallback_is_neutral_for_channel_turn(compat_app):
    resp = await _post_completions(
        compat_app,
        {
            "model": "agent_x",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": True,
            "channel_provider": "lark",
            "channel_context": _lark_context(),
        },
    )
    body = resp.text
    assert "channel turn completed" in body
    assert "produced no user-visible reply" not in body
    assert "data: [DONE]" in body


async def test_stream_fallback_keeps_legacy_text_for_plain_turn(compat_app):
    resp = await _post_completions(
        compat_app,
        {
            "model": "agent_x",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": True,
        },
    )
    assert "produced no user-visible reply" in resp.text


# ---------------------------------------------------------------------------
# Stage C — managed ingress executor (trigger business hooks)
# ---------------------------------------------------------------------------

from types import SimpleNamespace  # noqa: E402

from narranexus.platform.channel.channel_trigger_base import (  # noqa: E402
    ChannelTriggerBase,
)
from narranexus.platform.module_system import managed_channel_ingress as ingress_mod  # noqa: E402
from narranexus.platform.schema.parsed_message import ChatType  # noqa: E402


def _tagged_extra(**overrides):
    extra = {
        "channel_tag": {
            "channel": "wechat",
            "sender_name": "u",
            "sender_id": "wx9",
            "room_id": "wx9",
        },
        "trigger_id": "wechat_m1",
        "source_message_id": "m1",
    }
    extra.update(overrides)
    return extra


def test_synthesize_managed_message_maps_contract_fields():
    msg = ingress_mod.synthesize_managed_message(
        _tagged_extra(chat_type="group", thread_id="t9", reply_token="ctx_tok"),
        "hello",
    )
    assert msg.message_id == "m1"
    assert msg.chat_id == "wx9"
    assert msg.sender_id == "wx9"
    assert msg.content == "hello"
    assert msg.chat_type is ChatType.GROUP
    assert msg.thread_id == "t9"
    assert msg.raw["context_token"] == "ctx_tok"
    assert msg.raw["managed_ingress"] is True


class _FakeTrigger:
    # These tests exercise the BUSINESS-hook routing (claim / authorize /
    # inbox / error fallback), not the ingress breaker — so the breaker is
    # switched off here rather than half-faked. Its own managed-path
    # coverage is `test_managed_ingress_breaker` below, which uses a real
    # ChannelTriggerBase subclass.
    INGRESS_GUARD_ENABLED = False

    def is_agent_peer(self, message):
        return False

    def __init__(self):
        self.before_calls = []
        self.after_calls = []
        self.audit_calls = []
        self.allow = (True, "")
        self.silent_receipt = "(silent ok)"

    async def managed_before_run(self, **kw):
        self.before_calls.append(kw)
        return self.allow

    async def managed_after_run(self, **kw):
        self.after_calls.append(kw)

    async def managed_silent_ingest(self, **kw):
        return self.silent_receipt

    async def managed_audit(self, db, event_type, **kw):
        self.audit_calls.append((event_type, kw))

    def managed_reply_kwargs(self, trigger_extra_data):
        return {"context_token": trigger_extra_data.get("reply_token", "")}


async def test_coordinator_routes_before_and_after(monkeypatch):
    trig = _FakeTrigger()
    monkeypatch.setitem(ingress_mod.CHANNEL_TRIGGER_MAP, "lark", lambda: trig)
    ingress = ingress_mod.ManagedChannelIngress()
    allow, receipt = await ingress.before_run(
        working_source=WorkingSource.LARK,
        agent_id="a1",
        user_input="m",
        trigger_extra_data=_tagged_extra(),
        db=object(),
    )
    assert (allow, receipt) == (True, "")
    call = trig.before_calls[0]
    assert call["agent_id"] == "a1"
    assert call["is_mention"] is True
    assert call["message"].chat_id == "wx9"

    await ingress.after_run(
        working_source=WorkingSource.LARK,
        agent_id="a1",
        user_input="m",
        trigger_extra_data=_tagged_extra(),
        db=object(),
        reply_text="ok",
        error_text="",
    )
    assert trig.after_calls[0]["reply_text"] == "ok"


async def test_before_run_stamps_turn_envelope_for_dm(monkeypatch):
    """Managed turns never run a context builder, so before_run must stamp
    the #254 envelope itself — without it step_3 reads every managed DM as
    a group room and the no-reply fallback is dead code (the exact defect
    class #254 fixed on the native paths)."""
    trig = _FakeTrigger()
    monkeypatch.setitem(ingress_mod.CHANNEL_TRIGGER_MAP, "lark", lambda: trig)
    ingress = ingress_mod.ManagedChannelIngress()
    ted = _tagged_extra(reply_token="tok-55")  # no chat_type = DM
    await ingress.before_run(
        working_source=WorkingSource.LARK,
        agent_id="a1",
        user_input="m",
        trigger_extra_data=ted,
        db=object(),
    )
    from narranexus.platform.channel.channel_prompts import ROOM_TYPE_DIRECT

    assert ted["channel_room_type"] == ROOM_TYPE_DIRECT
    assert ted["channel_reply_kwargs"] == {"context_token": "tok-55"}


async def test_before_run_stamps_group_room_type(monkeypatch):
    trig = _FakeTrigger()
    monkeypatch.setitem(ingress_mod.CHANNEL_TRIGGER_MAP, "lark", lambda: trig)
    ingress = ingress_mod.ManagedChannelIngress()
    ted = _tagged_extra(chat_type="group")
    await ingress.before_run(
        working_source=WorkingSource.LARK,
        agent_id="a1",
        user_input="m",
        trigger_extra_data=ted,
        db=object(),
    )
    from narranexus.platform.channel.channel_prompts import ROOM_TYPE_GROUP

    assert ted["channel_room_type"] == ROOM_TYPE_GROUP


def test_wechat_trigger_managed_reply_kwargs_carries_reply_token():
    from narranexus_plugins.wechat_module.wechat_trigger import WeChatTrigger

    trigger = WeChatTrigger.__new__(WeChatTrigger)
    assert trigger.managed_reply_kwargs({"reply_token": "ctx-1"}) == {
        "context_token": "ctx-1"
    }
    assert trigger.managed_reply_kwargs({}) == {"context_token": ""}


def test_base_managed_reply_kwargs_defaults_empty():
    from narranexus.platform.channel.channel_trigger_base import ChannelTriggerBase

    # Unbound call — the ABC can't be instantiated bare, and the default
    # implementation reads nothing off self.
    assert ChannelTriggerBase.managed_reply_kwargs(object(), {"reply_token": "x"}) == {}


async def test_coordinator_deny_propagates(monkeypatch):
    trig = _FakeTrigger()
    trig.allow = (False, "nope")
    monkeypatch.setitem(ingress_mod.CHANNEL_TRIGGER_MAP, "lark", lambda: trig)
    ingress = ingress_mod.ManagedChannelIngress()
    allow, receipt = await ingress.before_run(
        working_source=WorkingSource.LARK,
        agent_id="a1",
        user_input="m",
        trigger_extra_data=_tagged_extra(),
        db=object(),
    )
    assert (allow, receipt) == (False, "nope")


async def test_coordinator_failure_semantics(monkeypatch):
    class _Boom:
        async def managed_before_run(self, **kw):
            raise RuntimeError("x")

        async def managed_after_run(self, **kw):
            raise RuntimeError("x")

    boom = _Boom()
    monkeypatch.setitem(ingress_mod.CHANNEL_TRIGGER_MAP, "telegram", lambda: boom)
    monkeypatch.setitem(
        ingress_mod.CHANNEL_TRIGGER_MAP, "narramessenger", lambda: boom
    )
    ingress = ingress_mod.ManagedChannelIngress()
    # Side-effect channel: fail-open.
    allow, _ = await ingress.before_run(
        working_source=WorkingSource.TELEGRAM,
        agent_id="a1",
        user_input="m",
        trigger_extra_data=_tagged_extra(),
        db=object(),
    )
    assert allow is True
    # Authorization channel: fail-closed.
    allow2, _ = await ingress.before_run(
        working_source=WorkingSource.NARRAMESSENGER,
        agent_id="a1",
        user_input="m",
        trigger_extra_data=_tagged_extra(),
        db=object(),
    )
    assert allow2 is False
    # after_run swallow.
    await ingress.after_run(
        working_source=WorkingSource.TELEGRAM,
        agent_id="a1",
        user_input="m",
        trigger_extra_data=_tagged_extra(),
        db=object(),
        reply_text="",
        error_text="e",
    )


async def test_coordinator_missing_trigger_class(monkeypatch):
    monkeypatch.delitem(
        ingress_mod.CHANNEL_TRIGGER_MAP, "narramessenger", raising=False
    )
    monkeypatch.delitem(ingress_mod.CHANNEL_TRIGGER_MAP, "telegram", raising=False)
    ingress = ingress_mod.ManagedChannelIngress()
    allow, _ = await ingress.before_run(
        working_source=WorkingSource.NARRAMESSENGER,
        agent_id="a1",
        user_input="m",
        trigger_extra_data=_tagged_extra(),
        db=object(),
    )
    assert allow is False
    allow2, _ = await ingress.before_run(
        working_source=WorkingSource.TELEGRAM,
        agent_id="a1",
        user_input="m",
        trigger_extra_data=_tagged_extra(),
        db=object(),
    )
    assert allow2 is True


class _DummyTrigger(ChannelTriggerBase):
    channel_name = "dummychan"
    brand_display = "Dummy"
    working_source = WorkingSource.MANYFOLD

    async def connect(self, *a, **k):  # pragma: no cover - unused
        raise NotImplementedError

    def parse_event(self, *a, **k):  # pragma: no cover - unused
        return None

    def is_echo(self, *a, **k):  # pragma: no cover - unused
        return False

    async def resolve_sender_name(self, *a, **k):  # pragma: no cover - unused
        return ""

    def create_context_builder(self, *a, **k):  # pragma: no cover - unused
        raise NotImplementedError

    async def load_active_credentials(self):
        return []


async def test_base_managed_after_run_error_fallback_then_inbox(monkeypatch):
    trig = _DummyTrigger()
    calls = {}

    async def fake_inbox_write(**kw):
        calls["inbox"] = kw

    async def fake_audit(event_type, **kw):
        calls["audit"] = (event_type, kw)

    sent = {}

    async def fake_fallback(credential, message, text, *, already_replied):
        sent["text"] = text

    async def fake_cred(agent_id):
        return object()

    monkeypatch.setattr(trig._inbox_recorder, "record_turn", fake_inbox_write)
    monkeypatch.setattr(trig, "_audit", fake_audit)
    monkeypatch.setattr(trig, "_send_error_fallback", fake_fallback)
    monkeypatch.setattr(trig, "_credential_for_agent", fake_cred)

    msg = ingress_mod.synthesize_managed_message(_tagged_extra(), "hi")
    await trig.managed_after_run(
        agent_id="a1", message=msg, db=object(), reply_text="", error_text="boom"
    )
    assert "text" in sent
    # Was `== CHANNEL_SILENT_SENTINEL` until 2026-08-18. The sentinel reached the
    # recorder, which wrote `(stayed silent)` as an OUTBOUND row attributed to the
    # agent — and Telegram/WeChat read `inbox_thread_messages` back as their
    # conversation memory, so the agent was handed that string as its own previous
    # reply. The recorder's contract is that an empty outbound writes no row at
    # all, which is what the other call site always passed.
    #
    # The panel is not worse off: it showed "(stayed silent)" before and shows no
    # reply row now, both meaning "the agent did not answer". What the user DID
    # receive on this path is the platform's error fallback (asserted just above
    # via `sent`), which is the platform speaking, not the agent — recording it as
    # the agent's words is the confusion this removes.
    assert calls["inbox"]["outbound_text"] == ""
    # The inbound half must still be recorded: a failed turn that loses the
    # incoming message from the panel is the failure this test was written for.
    assert calls["inbox"]["inbound_text"] == "hi"
    assert calls["audit"][0] == "managed_ingress_processed"

    sent.clear()
    await trig.managed_after_run(
        agent_id="a1", message=msg, db=object(), reply_text="done", error_text="boom"
    )
    assert not sent  # agent replied - no fallback
    assert calls["inbox"]["outbound_text"] == "done"


async def test_wechat_managed_before_run_claims_owner(monkeypatch):
    from narranexus_plugins.wechat_module import wechat_trigger as wt

    trig = wt.WeChatTrigger()
    cred = SimpleNamespace(agent_id="a1", owner_wx_id="")

    async def fake_cred(agent_id):
        return cred

    claimed = {}

    class _FakeMgr:
        def __init__(self, db):
            pass

        async def claim_owner(self, agent_id, sender_id):
            claimed["args"] = (agent_id, sender_id)
            return True

    monkeypatch.setattr(trig, "_credential_for_agent", fake_cred)
    monkeypatch.setattr(wt, "WeChatCredentialManager", _FakeMgr)
    msg = ingress_mod.synthesize_managed_message(_tagged_extra(), "hi")
    allow, _ = await trig.managed_before_run(agent_id="a1", message=msg, db=object())
    assert allow is True
    assert claimed["args"] == ("a1", "wx9")
    assert cred.owner_wx_id == "wx9"


async def test_matrix_managed_before_run_paths(monkeypatch):
    from narranexus_plugins.narramessenger_module import matrix_trigger as mt

    trig = mt.MatrixTrigger()
    msg = ingress_mod.synthesize_managed_message(_tagged_extra(), "hi")

    async def none_cred(agent_id):
        return None

    monkeypatch.setattr(trig, "_credential_for_agent", none_cred)
    allow, receipt = await trig.managed_before_run(
        agent_id="a1", message=msg, db=object()
    )
    assert allow is False and "unavailable" in receipt

    cred = SimpleNamespace(
        agent_id="a1", matrix_homeserver_url="https://hs", matrix_access_token="tok"
    )

    async def some_cred(agent_id):
        return cred

    monkeypatch.setattr(trig, "_credential_for_agent", some_cred)

    async def verdict_allow(credential, message, *, mentioned):
        return mt._AuthorizeVerdict(allow=True)

    monkeypatch.setattr(trig, "_authorize_event", verdict_allow)
    allow, _ = await trig.managed_before_run(agent_id="a1", message=msg, db=object())
    assert allow is True

    sent = {}

    async def fake_send(**kw):
        sent.update(kw)
        return "evt1"

    async def verdict_deny(credential, message, *, mentioned):
        return mt._AuthorizeVerdict(allow=False, notice_send=True, notice_text="no")

    monkeypatch.setattr(mt, "matrix_room_send", fake_send)
    monkeypatch.setattr(trig, "_authorize_event", verdict_deny)
    allow, receipt = await trig.managed_before_run(
        agent_id="a1", message=msg, db=object()
    )
    assert allow is False and "denied" in receipt
    assert sent["content"] == {"msgtype": "m.notice", "body": "no"}

    async def boom(*a, **k):
        raise AssertionError("authorize must not run for silent group traffic")

    monkeypatch.setattr(trig, "_authorize_event", boom)
    group_msg = ingress_mod.synthesize_managed_message(
        _tagged_extra(chat_type="group"), "hi"
    )
    allow, _ = await trig.managed_before_run(
        agent_id="a1", message=group_msg, db=object(), is_mention=False
    )
    assert allow is True


# ---------------------------------------------------------------------------
# Stage C — endpoint wiring (gate deny + after_run scheduling)
# ---------------------------------------------------------------------------


class _FakeIngress:
    def __init__(self, allow=(True, "")):
        self.allow = allow
        self.before_calls = []
        self.after_calls = []
        self.convert_calls = []

    async def before_run(self, **kw):
        self.before_calls.append(kw)
        return self.allow

    async def convert_attachments(self, **kw):
        self.convert_calls.append(kw)

    async def after_run(self, **kw):
        self.after_calls.append(kw)


async def test_endpoint_gate_deny_answers_receipt(compat_app, monkeypatch):
    fake = _FakeIngress(allow=(False, "denied-by-test"))
    monkeypatch.setattr(ingress_mod, "get_managed_channel_ingress", lambda: fake)
    n_before = len(_FakeBackgroundRun.instances)
    resp = await _post_completions(
        compat_app,
        {
            "model": "agent_x",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": False,
            "channel_provider": "narramessenger",
            "channel_context": {"room_id": "!r:hs", "sender_id": "@u:hs"},
        },
    )
    assert resp.status_code == 200
    assert resp.json()["choices"][0]["message"]["content"] == "denied-by-test"
    assert len(_FakeBackgroundRun.instances) == n_before  # run never started


async def test_endpoint_schedules_after_run_with_reply(compat_app, monkeypatch):
    fake = _FakeIngress()
    monkeypatch.setattr(ingress_mod, "get_managed_channel_ingress", lambda: fake)
    _FakeBackgroundRun.events = [
        {
            "type": "progress",
            "details": {
                "tool_name": "mcp__chat_module__notify_owner",
                "arguments": {"content": "hey"},
            },
        },
        {"type": "complete"},
    ]
    try:
        resp = await _post_completions(
            compat_app,
            {
                "model": "agent_x",
                "messages": [{"role": "user", "content": "hi"}],
                "stream": False,
                "channel_provider": "lark",
                "channel_context": _lark_context(),
            },
        )
    finally:
        _FakeBackgroundRun.events = []
    assert resp.status_code == 200
    for _ in range(20):
        if fake.after_calls:
            break
        await asyncio.sleep(0.01)
    assert fake.after_calls, "after_run was never scheduled"
    call = fake.after_calls[0]
    assert call["reply_text"] == "hey"
    assert call["error_text"] == ""
    assert fake.before_calls[0]["working_source"] is WorkingSource.LARK
    # Turn facts for the managed_ingress_processed audit row.
    details = call["audit_details"]
    assert details["route"] == "normal"
    assert isinstance(details["duration_ms"], int) and details["duration_ms"] >= 0


async def test_endpoint_plain_turn_skips_managed_gate(compat_app, monkeypatch):
    fake = _FakeIngress()
    monkeypatch.setattr(ingress_mod, "get_managed_channel_ingress", lambda: fake)
    await _post_completions(
        compat_app,
        {
            "model": "agent_x",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": False,
        },
    )
    assert fake.before_calls == []
    assert fake.after_calls == []


# ---------------------------------------------------------------------------
# Stage D — narramessenger managed turn instructs narra_send
# ---------------------------------------------------------------------------

from unittest.mock import MagicMock  # noqa: E402


def _nm_module():
    from narranexus_plugins.narramessenger_module.narramessenger_module import (
        NarramessengerModule,
    )

    return NarramessengerModule(
        agent_id="agent_a", user_id=None, database_client=MagicMock()
    )


def _nm_info(**overrides):
    info = {
        "matrix_user_id": "@bot:hs",
        "owner_matrix_user_id": "@o:hs",
        "owner_name": "O",
        "current_sender_id": "@u:hs",
        "current_room_id": "!r:hs",
        "is_owner_interacting": False,
        "connection_mode": "matrix",
        "enabled": True,
        "managed_ingress": False,
    }
    info.update(overrides)
    return info


async def test_nm_managed_turn_instructs_narra_send():
    module = _nm_module()
    ctx = SimpleNamespace(
        extra_data={module.ctx_data_key: _nm_info(managed_ingress=True)},
        working_source=WorkingSource.NARRAMESSENGER,
    )
    text = await module.contribute_instructions(ctx)
    assert 'narra_send(room_id="!r:hs"' in text
    assert "Do NOT" in text and "narra_reply" in text


async def test_nm_native_turn_keeps_narra_reply():
    module = _nm_module()
    ctx = SimpleNamespace(
        extra_data={module.ctx_data_key: _nm_info()},
        working_source=WorkingSource.NARRAMESSENGER,
    )
    text = await module.contribute_instructions(ctx)
    assert 'narra_reply(text="<your reply>")' in text


def test_channel_turn_extra_data_carries_managed_flag():
    _, _, extra = sync_mod.build_inbound_run_context(
        channel_provider="lark",
        channel_context=_lark_context(),
        user_input="m",
        session_id="s1",
    )
    assert extra["managed_ingress"] is True


# ---------------------------------------------------------------------------
# Stage E — files write endpoint + managed attachment conversion
# ---------------------------------------------------------------------------

import backend.routes.manyfold.files as files_mod  # noqa: E402


@pytest.fixture
def files_app(monkeypatch, tmp_path):
    workspace = tmp_path / "agent_x_u1"
    workspace.mkdir()

    async def fake_root(agent_id):
        return workspace, "u1"

    monkeypatch.setattr(files_mod, "_resolve_workspace_root", fake_root)

    app = FastAPI()

    @app.middleware("http")
    async def _authed(request: Request, call_next):
        request.state.manyfold_authed = True
        return await call_next(request)

    app.include_router(files_mod.router)
    return app, workspace


async def _post_write(app, path, content: bytes, **params):
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        return await client.post(
            "/manyfold/agents/agent_x/files/write",
            params={"path": path, **params},
            content=content,
        )


async def test_files_write_roundtrip(files_app):
    app, workspace = files_app
    resp = await _post_write(app, "chat-attachments/s1/u/cat.png", b"pngbytes")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True and data["size"] == 8
    assert (workspace / "chat-attachments/s1/u/cat.png").read_bytes() == b"pngbytes"


async def test_files_write_rejects_escape_and_root(files_app):
    app, _ = files_app
    assert (await _post_write(app, "../evil.txt", b"x")).status_code == 403
    assert (await _post_write(app, "", b"x")).status_code in (400, 422)


async def test_files_write_overwrite_semantics(files_app):
    app, workspace = files_app
    await _post_write(app, "a/f.txt", b"one")
    # Default denies silent replacement - this is the API's only write door.
    resp = await _post_write(app, "a/f.txt", b"two")
    assert resp.status_code == 409
    resp2 = await _post_write(app, "a/f.txt", b"two", overwrite="true")
    assert resp2.status_code == 200
    assert (workspace / "a/f.txt").read_bytes() == b"two"


async def test_convert_attachments_persists_via_native_store(monkeypatch, tmp_path):
    import narranexus.platform.repository.agent_repository as agent_repo_mod
    import narranexus.platform.utils.attachment_storage as storage_mod
    import narranexus.platform.utils.workspace_paths as wsp_mod

    workspace = tmp_path / "agent_x_u1"
    (workspace / "chat-attachments/s1/u").mkdir(parents=True)
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 32
    (workspace / "chat-attachments/s1/u/cat.png").write_bytes(png)

    class _FakeRepo:
        def __init__(self, db):
            pass

        async def resolve_owner(self, agent_id):
            return "u1"

    monkeypatch.setattr(agent_repo_mod, "AgentRepository", _FakeRepo)
    monkeypatch.setattr(
        wsp_mod, "resolve_existing_workspace", lambda a, u, base=None: workspace
    )
    monkeypatch.setattr(
        storage_mod, "get_workspace_path", lambda a, u: workspace
    )

    ingress = ingress_mod.ManagedChannelIngress()
    extra = _tagged_extra()
    extra["manyfold_attachments"] = [
        {"name": "cat.png", "mime": "image/png", "size": len(png),
         "path": "chat-attachments/s1/u/cat.png"},
        {"name": "evil", "mime": "text/plain", "size": 1, "path": "../../pwd"},
    ]
    await ingress.convert_attachments(
        working_source=WorkingSource.LARK,
        agent_id="agent_x",
        trigger_extra_data=extra,
        db=object(),
    )
    assert "manyfold_attachments" not in extra
    atts = extra["attachments"]
    assert len(atts) == 1  # escape ref degraded, valid one converted
    att = atts[0]
    assert att["file_id"].startswith("att_")
    assert att["original_name"] == "cat.png"
    assert att["mime_type"] == "image/png"
    # Native marker resolution works: file is in the upload store + index.
    resolved = storage_mod.resolve_attachment_path("agent_x", "u1", att["file_id"])
    assert resolved is not None and resolved.exists()


async def test_convert_attachments_never_raises_on_bad_env(monkeypatch):
    import narranexus.platform.repository.agent_repository as agent_repo_mod

    class _BoomRepo:
        def __init__(self, db):
            raise RuntimeError("db down")

    monkeypatch.setattr(agent_repo_mod, "AgentRepository", _BoomRepo)
    ingress = ingress_mod.ManagedChannelIngress()
    extra = _tagged_extra()
    extra["manyfold_attachments"] = [{"name": "a", "mime": "x", "size": 1, "path": "p"}]
    await ingress.convert_attachments(
        working_source=WorkingSource.LARK,
        agent_id="agent_x",
        trigger_extra_data=extra,
        db=object(),
    )
    assert "manyfold_attachments" not in extra
    assert "attachments" not in extra


# ---------------------------------------------------------------------------
# Stage F — non-mention group traffic → silent ingestion (memory-only)
# ---------------------------------------------------------------------------


class _FakeSilentTrigger:
    def __init__(self):
        self.silent_calls = []

    async def managed_silent_ingest(self, *, agent_id, message, db, attachments=None):
        self.silent_calls.append(
            {"agent_id": agent_id, "message": message, "attachments": attachments}
        )
        return "(silent group message ingested to memory - no reply)"


async def test_silent_ingest_runs_native_silent_batch(monkeypatch):
    trig = _FakeSilentTrigger()
    monkeypatch.setitem(
        ingress_mod.CHANNEL_TRIGGER_MAP, "narramessenger", lambda: trig
    )
    ingress = ingress_mod.ManagedChannelIngress()
    extra = _tagged_extra(chat_type="group", is_mention=False)
    receipt = await ingress.silent_ingest(
        working_source=WorkingSource.NARRAMESSENGER,
        agent_id="agent_x",
        user_input="group chatter",
        trigger_extra_data=extra,
        db=object(),
    )
    assert "ingested" in receipt
    call = trig.silent_calls[0]
    assert call["message"].content == "group chatter"
    assert call["agent_id"] == "agent_x"


async def test_silent_ingest_never_raises(monkeypatch):
    class _Boom:
        async def managed_silent_ingest(self, **kw):
            raise RuntimeError("x")

    monkeypatch.setitem(ingress_mod.CHANNEL_TRIGGER_MAP, "lark", lambda: _Boom())
    ingress = ingress_mod.ManagedChannelIngress()
    receipt = await ingress.silent_ingest(
        working_source=WorkingSource.LARK,
        agent_id="agent_x",
        user_input="m",
        trigger_extra_data=_tagged_extra(),
        db=object(),
    )
    assert "dropped" in receipt


async def test_endpoint_silent_group_turn_answers_receipt(compat_app, monkeypatch):
    fake = _FakeIngress()

    async def fake_silent(**kw):
        fake.silent_kw = kw
        return "(silent group message ingested to memory - no reply)"

    fake.silent_ingest = fake_silent
    monkeypatch.setattr(ingress_mod, "get_managed_channel_ingress", lambda: fake)
    n_before = len(_FakeBackgroundRun.instances)
    resp = await _post_completions(
        compat_app,
        {
            "model": "agent_x",
            "messages": [{"role": "user", "content": "chatter"}],
            "stream": False,
            "channel_provider": "narramessenger",
            "channel_context": {
                "room_id": "!r:hs",
                "sender_id": "@u:hs",
                "chat_type": "group",
                "is_mention": False,
            },
        },
    )
    assert resp.status_code == 200
    assert "ingested" in resp.json()["choices"][0]["message"]["content"]
    assert len(_FakeBackgroundRun.instances) == n_before  # no reply run
    assert fake.silent_kw["working_source"] is WorkingSource.NARRAMESSENGER


async def test_endpoint_non_mention_dm_still_runs_normally(compat_app, monkeypatch):
    fake = _FakeIngress()
    monkeypatch.setattr(ingress_mod, "get_managed_channel_ingress", lambda: fake)
    n_before = len(_FakeBackgroundRun.instances)
    resp = await _post_completions(
        compat_app,
        {
            "model": "agent_x",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": False,
            "channel_provider": "telegram",
            "channel_context": {
                "room_id": "c1",
                "sender_id": "u1",
                "is_mention": False,
            },
        },
    )
    assert resp.status_code == 200
    assert len(_FakeBackgroundRun.instances) == n_before + 1  # normal run


async def test_nm_managed_turn_declares_only_narra_send(monkeypatch):
    from unittest.mock import AsyncMock

    module = _nm_module()
    monkeypatch.setattr(module, "is_bound", AsyncMock(return_value=True))
    managed_ctx = SimpleNamespace(
        extra_data={"managed_ingress": True},
        working_source=WorkingSource.NARRAMESSENGER,
    )
    tools = await module.expressive_tools(managed_ctx)
    assert any("narra_send" in t for t in tools)
    assert not any("narra_reply" in t for t in tools)

    native_ctx = SimpleNamespace(
        extra_data={},
        working_source=WorkingSource.NARRAMESSENGER,
    )
    native = await module.expressive_tools(native_ctx)
    assert any("narra_reply" in t for t in native)
    # No-ctx callers (tests, other frameworks) keep the full declaration.
    assert await module.expressive_tools() == native


def test_is_mention_and_chat_type_survive_typescript_stringification():
    """bool("false") is True - a stringified platform flag must not flip a
    non-mention group message into a mentioned one (the agent would barge
    into group small talk), and an upper-cased chat_type must still route
    the silent path."""
    _, _, extra = sync_mod.build_inbound_run_context(
        channel_provider="narramessenger",
        channel_context={
            "room_id": "!r:hs",
            "sender_id": "@u:hs",
            "chat_type": "GROUP",
            "is_mention": "false",
        },
        user_input="m",
        session_id="s1",
    )
    assert extra["chat_type"] == "group"
    assert extra["is_mention"] is False

    _, _, extra2 = sync_mod.build_inbound_run_context(
        channel_provider="narramessenger",
        channel_context={"room_id": "!r:hs", "sender_id": "@u:hs", "is_mention": "true"},
        user_input="m",
        session_id="s1",
    )
    assert extra2["is_mention"] is True


async def test_base_managed_silent_ingest_drives_native_batch(monkeypatch):
    trig = _DummyTrigger()
    captured = {}

    async def fake_cred(agent_id):
        return SimpleNamespace(agent_id=agent_id)

    async def fake_batch(credential, messages, sender_name_by_id=None, *, attachments_by_index=None):
        captured.update(
            messages=messages,
            sender_name_by_id=sender_name_by_id,
            attachments_by_index=attachments_by_index,
        )

    monkeypatch.setattr(trig, "_credential_for_agent", fake_cred)
    monkeypatch.setattr(trig, "_build_and_run_agent_silent_batch", fake_batch)
    msg = ingress_mod.synthesize_managed_message(_tagged_extra(), "quiet chatter")
    receipt = await trig.managed_silent_ingest(
        agent_id="a1", message=msg, db=object(), attachments=None
    )
    assert "ingested" in receipt
    assert captured["messages"][0].content == "quiet chatter"
    assert captured["sender_name_by_id"] == {"wx9": "u"}

    # A pass that did not run must not be receipted as ingested.
    async def failed_batch(credential, messages, sender_name_by_id=None, *, attachments_by_index=None):
        return "runtime_error"

    monkeypatch.setattr(trig, "_build_and_run_agent_silent_batch", failed_batch)
    failed = await trig.managed_silent_ingest(agent_id="a1", message=msg, db=object())
    assert "ingested to memory" not in failed
    assert failed == "(silent group message not ingested - runtime_error)"
    monkeypatch.setattr(trig, "_build_and_run_agent_silent_batch", fake_batch)

    async def no_cred(agent_id):
        return None

    monkeypatch.setattr(trig, "_credential_for_agent", no_cred)
    receipt2 = await trig.managed_silent_ingest(
        agent_id="a1", message=msg, db=object()
    )
    assert "dropped" in receipt2


# ---------------------------------------------------------------------------
# Stage B (batch 2) — managed ingress audit trail
# ---------------------------------------------------------------------------


class _AuditRecorder:
    """Stands in for ChannelTriggerAuditRepository at the coordinator's
    direct-write seam (the coordinator deliberately does NOT audit through
    the trigger: two deny paths fire precisely because the trigger is
    unavailable)."""

    rows: list = []

    def __init__(self, channel, db):
        self._channel = channel

    async def append(self, event_type, **kw):
        _AuditRecorder.rows.append((self._channel, event_type, kw))


@pytest.fixture
def audit_rows(monkeypatch):
    monkeypatch.setattr(
        ingress_mod, "ChannelTriggerAuditRepository", _AuditRecorder
    )
    _AuditRecorder.rows = []
    return _AuditRecorder.rows


async def test_before_run_deny_writes_denied_audit(monkeypatch, audit_rows):
    trig = _FakeTrigger()
    trig.allow = (False, "not authorized")
    monkeypatch.setitem(ingress_mod.CHANNEL_TRIGGER_MAP, "lark", lambda: trig)
    ingress = ingress_mod.ManagedChannelIngress()
    await ingress.before_run(
        working_source=WorkingSource.LARK,
        agent_id="a1",
        user_input="m",
        trigger_extra_data=_tagged_extra(),
        db=object(),
    )
    channel, event, kw = audit_rows[0]
    assert (channel, event) == ("lark", "managed_ingress_denied")
    assert kw["agent_id"] == "a1"
    assert kw["message_id"] == "m1"
    assert kw["chat_id"] == "wx9" and kw["sender_id"] == "wx9"
    assert kw["details"]["reason"] == "not authorized"


async def test_before_run_allow_writes_no_audit(monkeypatch, audit_rows):
    trig = _FakeTrigger()
    monkeypatch.setitem(ingress_mod.CHANNEL_TRIGGER_MAP, "lark", lambda: trig)
    ingress = ingress_mod.ManagedChannelIngress()
    await ingress.before_run(
        working_source=WorkingSource.LARK,
        agent_id="a1",
        user_input="m",
        trigger_extra_data=_tagged_extra(),
        db=object(),
    )
    # The allowed path's row is managed_ingress_processed, written by
    # managed_after_run — no duplicate event here.
    assert audit_rows == []


async def test_deny_without_trigger_still_audits(monkeypatch, audit_rows):
    """Trigger not loadable fails the WHOLE channel — misreading that as
    'the platform never called us' points diagnosis exactly backwards,
    so this deny must leave a row even with no trigger to write through."""
    monkeypatch.delitem(
        ingress_mod.CHANNEL_TRIGGER_MAP, "narramessenger", raising=False
    )
    ingress = ingress_mod.ManagedChannelIngress()
    allow, receipt = await ingress.before_run(
        working_source=WorkingSource.NARRAMESSENGER,
        agent_id="a1",
        user_input="m",
        trigger_extra_data=_tagged_extra(),
        db=object(),
    )
    assert allow is False
    channel, event, kw = audit_rows[0]
    assert (channel, event) == ("narramessenger", "managed_ingress_denied")
    assert "not loadable" in kw["details"]["reason"]


async def test_gate_crash_fail_closed_deny_audits(monkeypatch, audit_rows):
    trig = _FakeTrigger()

    async def boom(**kw):
        raise RuntimeError("authorize backend down")

    trig.managed_before_run = boom
    monkeypatch.setitem(
        ingress_mod.CHANNEL_TRIGGER_MAP, "narramessenger", lambda: trig
    )
    ingress = ingress_mod.ManagedChannelIngress()
    allow, _ = await ingress.before_run(
        working_source=WorkingSource.NARRAMESSENGER,
        agent_id="a1",
        user_input="m",
        trigger_extra_data=_tagged_extra(),
        db=object(),
    )
    assert allow is False
    channel, event, kw = audit_rows[0]
    assert event == "managed_ingress_denied"
    assert "RuntimeError" in kw["details"]["reason"]


async def test_silent_ingest_writes_silent_audit(monkeypatch, audit_rows):
    trig = _FakeTrigger()
    monkeypatch.setitem(ingress_mod.CHANNEL_TRIGGER_MAP, "lark", lambda: trig)
    ingress = ingress_mod.ManagedChannelIngress()
    receipt = await ingress.silent_ingest(
        working_source=WorkingSource.LARK,
        agent_id="a1",
        user_input="m",
        trigger_extra_data=_tagged_extra(),
        db=object(),
    )
    assert receipt == "(silent ok)"
    channel, event, kw = audit_rows[0]
    assert event == "managed_ingress_silent"
    assert kw["details"]["attachments"] == 0
    assert "(silent ok)" in kw["details"]["receipt"]


def _fake_workspace_env(monkeypatch, tmp_path):
    import narranexus.platform.repository.agent_repository as agent_repo_mod
    import narranexus.platform.utils.attachment_storage as storage_mod
    import narranexus.platform.utils.workspace_paths as wsp_mod

    workspace = tmp_path / "agent_x_u1"
    (workspace / "chat-attachments/s1/u").mkdir(parents=True)
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 32
    (workspace / "chat-attachments/s1/u/cat.png").write_bytes(png)

    class _FakeRepo:
        def __init__(self, db):
            pass

        async def resolve_owner(self, agent_id):
            return "u1"

    monkeypatch.setattr(agent_repo_mod, "AgentRepository", _FakeRepo)
    monkeypatch.setattr(
        wsp_mod, "resolve_existing_workspace", lambda a, u, base=None: workspace
    )
    monkeypatch.setattr(storage_mod, "get_workspace_path", lambda a, u: workspace)
    return workspace, png


async def test_convert_attachments_audits_declared_vs_converted(
    monkeypatch, tmp_path, audit_rows
):
    """declared vs converted is the whole diagnosis for 'the agent never
    saw my file' — the converter never raises, so without this row a
    shortfall is invisible outside process logs."""
    _, png = _fake_workspace_env(monkeypatch, tmp_path)
    ingress = ingress_mod.ManagedChannelIngress()
    extra = _tagged_extra()
    extra["manyfold_attachments"] = [
        {"name": "cat.png", "mime": "image/png", "size": len(png),
         "path": "chat-attachments/s1/u/cat.png"},
        {"name": "gone.png", "mime": "image/png", "size": 9,
         "path": "chat-attachments/s1/u/missing.png"},
    ]
    await ingress.convert_attachments(
        working_source=WorkingSource.LARK,
        agent_id="agent_x",
        trigger_extra_data=extra,
        db=object(),
    )
    channel, event, kw = audit_rows[0]
    assert event == "managed_attachments_converted"
    assert kw["details"] == {"declared": 2, "converted": 1}
    # Same identity derivation as the ParsedMessage — no drift.
    assert kw["message_id"] == "m1"
    assert kw["chat_id"] == "wx9" and kw["sender_id"] == "wx9"


async def test_attachments_resolution_failure_still_audits(
    monkeypatch, audit_rows
):
    """Workspace resolution failing loses ALL declared attachments — the
    exact case this row exists for."""
    import narranexus.platform.repository.agent_repository as agent_repo_mod

    class _BoomRepo2:
        def __init__(self, db):
            raise RuntimeError("db down")

    monkeypatch.setattr(agent_repo_mod, "AgentRepository", _BoomRepo2)
    ingress = ingress_mod.ManagedChannelIngress()
    extra = _tagged_extra()
    extra["manyfold_attachments"] = [
        {"name": "a", "mime": "x", "size": 1, "path": "p"}
    ]
    await ingress.convert_attachments(
        working_source=WorkingSource.LARK,
        agent_id="agent_x",
        trigger_extra_data=extra,
        db=object(),
    )
    channel, event, kw = audit_rows[0]
    assert event == "managed_attachments_converted"
    assert kw["details"]["declared"] == 1
    assert kw["details"]["converted"] == 0
    assert kw["details"]["error"].startswith("workspace_resolution")


async def test_coordinator_after_run_threads_audit_details(monkeypatch):
    trig = _FakeTrigger()
    monkeypatch.setitem(ingress_mod.CHANNEL_TRIGGER_MAP, "lark", lambda: trig)
    ingress = ingress_mod.ManagedChannelIngress()
    await ingress.after_run(
        working_source=WorkingSource.LARK,
        agent_id="a1",
        user_input="m",
        trigger_extra_data=_tagged_extra(),
        db=object(),
        reply_text="ok",
        error_text="",
        audit_details={"route": "normal", "duration_ms": 42},
    )
    assert trig.after_calls[0]["audit_details"] == {
        "route": "normal",
        "duration_ms": 42,
    }


async def test_base_managed_after_run_merges_audit_details(monkeypatch):
    trig = _DummyTrigger()
    calls = {}

    async def fake_inbox_write(**kw):
        calls["inbox"] = kw

    async def fake_audit(event_type, **kw):
        calls["audit"] = (event_type, kw)

    monkeypatch.setattr(trig._inbox_recorder, "record_turn", fake_inbox_write)
    monkeypatch.setattr(trig, "_audit", fake_audit)

    msg = ingress_mod.synthesize_managed_message(_tagged_extra(), "hi")
    await trig.managed_after_run(
        agent_id="a1",
        message=msg,
        db=object(),
        reply_text="done",
        audit_details={"route": "normal", "duration_ms": 7, "replied": "spoof"},
    )
    event, kw = calls["audit"]
    assert event == "managed_ingress_processed"
    details = kw["details"]
    assert details["route"] == "normal"
    assert details["duration_ms"] == 7
    # This method's own facts stay authoritative over caller keys.
    assert details["replied"] is True
    assert details["error"] == ""


async def test_files_write_audits_success_and_escape(files_app, monkeypatch):
    app, _ = files_app
    rows = []

    async def fake_audit(agent_id, **kw):
        rows.append((agent_id, kw))

    monkeypatch.setattr(files_mod, "_audit_files_write", fake_audit)

    resp = await _post_write(app, "chat-attachments/s2/u/a.txt", b"hello")
    assert resp.status_code == 200
    assert rows[-1][1]["ok"] is True and rows[-1][1]["size"] == 5

    resp = await _post_write(app, "../evil.txt", b"x")
    assert resp.status_code == 403
    assert rows[-1][1]["ok"] is False
    assert rows[-1][1]["error"].startswith("403")


async def test_files_write_audits_infra_failure(files_app, monkeypatch):
    """OSError on the disk write itself must still leave a row — the
    infrastructure-failure case is exactly what 'did the platform write
    get in?' needs to stay answerable for."""
    app, _ = files_app
    rows = []

    async def fake_audit(agent_id, **kw):
        rows.append((agent_id, kw))

    monkeypatch.setattr(files_mod, "_audit_files_write", fake_audit)

    async def boom_thread(fn, *a, **kw):
        raise OSError("disk full")

    monkeypatch.setattr(files_mod.asyncio, "to_thread", boom_thread)
    # ASGITransport surfaces app exceptions instead of rendering a 500;
    # the audit row must exist by the time the exception escapes.
    with pytest.raises(OSError):
        await _post_write(app, "a/b.txt", b"x")
    assert rows and rows[-1][1]["ok"] is False
    assert rows[-1][1]["error"].startswith("OSError")


async def test_files_write_audit_helper_never_raises(monkeypatch):
    async def boom():
        raise RuntimeError("db down")

    monkeypatch.setattr(files_mod, "get_db_client", boom)
    # Must swallow: losing an audit row must not fail the write itself.
    await files_mod._audit_files_write("agent_x", path="p", ok=True, size=1)


async def test_files_write_audit_cleanup_runs_once_per_day(monkeypatch):
    """channel='manyfold' has no trigger cleanup tick — the write-path
    sweep is its ONLY retention driver, throttled to one run per
    process-day."""
    appended = []
    cleaned = []

    class _Repo:
        def __init__(self, channel, db):
            pass

        async def append(self, event_type, **kw):
            appended.append(event_type)

        async def cleanup_older_than_days(self, days):
            cleaned.append(days)
            return 0

    import narranexus.platform.repository.channel_trigger_audit_repository as repo_mod

    async def fake_db():
        return object()

    monkeypatch.setattr(files_mod, "get_db_client", fake_db)
    monkeypatch.setattr(repo_mod, "ChannelTriggerAuditRepository", _Repo)
    monkeypatch.setattr(files_mod, "_audit_cleanup_next", 0.0)

    await files_mod._audit_files_write("a", path="p1", ok=True, size=1)
    await files_mod._audit_files_write("a", path="p2", ok=True, size=1)
    assert len(appended) == 2
    assert cleaned == [files_mod._AUDIT_RETENTION_DAYS]  # once, not twice


# ---------------------------------------------------------------------------
# The agent-sender marker has to survive the ROUTE, not just the helper
#
# Every other test for this drives `retag_managed_input` directly. None of
# them notices if the route stops calling it — and the route is the only
# place that can, because `run_input` is rendered before the ingress hooks
# know whether the sender is an agent. Delete the call and those tests stay
# green while the marker silently vanishes from what the model reads, which
# is exactly the defect this whole signal was added to close.
# ---------------------------------------------------------------------------


async def test_route_renders_the_agent_marker_into_what_the_model_reads(
    compat_app, monkeypatch
):
    from narranexus.platform.schema.channel_tag import AGENT_PEER_MARKER

    async def _allow(self, **kwargs):
        return True, ""

    from narranexus.platform.module_system.managed_channel_ingress import (
        get_managed_channel_ingress,
    )

    # The process-wide singleton is what the route uses; patching the
    # trigger CLASS covers it whichever instance is cached.
    trigger = get_managed_channel_ingress()._trigger("narramessenger")
    if trigger is None:  # pragma: no cover — optional dependency missing
        pytest.skip("narramessenger trigger not importable")
    monkeypatch.setattr(type(trigger), "managed_before_run", _allow)

    resp = await _post_completions(
        compat_app,
        {
            "model": "agent_x",
            "messages": [{"role": "user", "content": "ping"}],
            "stream": False,
            "channel_provider": "narramessenger",
            "channel_context": {
                "sender_id": "@agent-e7726996:matrix.netmind.chat",
                "sender_name": "Liam",
                "room_id": "!room:h",
                "chat_type": "private",
            },
        },
    )
    assert resp.status_code == 200

    run = _FakeBackgroundRun.instances[-1]
    assert run.drive_kwargs is not None, "the turn never reached a run"
    assert AGENT_PEER_MARKER in run.drive_kwargs["input_content"], (
        "the route dropped the marker — run_input is rendered before the "
        "hooks know the sender is an agent, so the route must re-render it"
    )


async def test_route_leaves_a_human_managed_turn_unmarked(compat_app, monkeypatch):
    from narranexus.platform.schema.channel_tag import AGENT_PEER_MARKER

    async def _allow(self, **kwargs):
        return True, ""

    from narranexus.platform.module_system.managed_channel_ingress import (
        get_managed_channel_ingress,
    )

    # The process-wide singleton is what the route uses; patching the
    # trigger CLASS covers it whichever instance is cached.
    trigger = get_managed_channel_ingress()._trigger("narramessenger")
    if trigger is None:  # pragma: no cover
        pytest.skip("narramessenger trigger not importable")
    monkeypatch.setattr(type(trigger), "managed_before_run", _allow)

    resp = await _post_completions(
        compat_app,
        {
            "model": "agent_x",
            "messages": [{"role": "user", "content": "ping"}],
            "stream": False,
            "channel_provider": "narramessenger",
            "channel_context": {
                "sender_id": "@liam:matrix.netmind.chat",
                "sender_name": "Liam",
                "room_id": "!room:h",
                "chat_type": "private",
            },
        },
    )
    assert resp.status_code == 200
    run = _FakeBackgroundRun.instances[-1]
    assert AGENT_PEER_MARKER not in run.drive_kwargs["input_content"]


# ---------------------------------------------------------------------------
# Managed-path ingress circuit breaker (2026-08-24)
#
# Managed mode bypasses the ENTIRE native receive path — no _subscribe_loop,
# no dedup store, no worker queue, no _process_message — so the base class's
# guard (built in start(), which managed mode never calls) can never fire
# here. Without its own call site the Manyfold surface would be the one
# unprotected way into the pipeline.
# ---------------------------------------------------------------------------


class _GuardedTrigger(ChannelTriggerBase):
    """Real subclass — the guard is built from its own class attributes,
    so this also pins that managed and native share one set of knobs."""

    channel_name = "wechat"
    brand_display = "WeChat"
    working_source = WorkingSource.WECHAT

    INGRESS_RATE_THRESHOLD = 5
    INGRESS_DUP_RATIO_THRESHOLD = 0.5

    async def connect(self, credential):  # pragma: no cover
        yield {}

    def parse_event(self, raw):  # pragma: no cover
        return None

    async def is_echo(self, message, credential):  # pragma: no cover
        return False

    async def resolve_sender_name(self, sender_id, credential):  # pragma: no cover
        return sender_id

    def create_context_builder(self, message, credential, agent_id):  # pragma: no cover
        return None

    async def load_active_credentials(self):  # pragma: no cover
        return []

    async def managed_before_run(self, **kw):
        return True, ""


async def _managed_send(ingress, n, *, text="ping"):
    """Feed n identical managed turns; return how many were admitted."""
    admitted = 0
    for i in range(n):
        allow, _ = await ingress.before_run(
            working_source=WorkingSource.WECHAT,
            agent_id="a1",
            user_input=text,
            trigger_extra_data=_tagged_extra(source_message_id=f"m{i}"),
            db=object(),
        )
        admitted += 1 if allow else 0
    return admitted


async def test_managed_ingress_breaker_stops_a_repeat_storm(monkeypatch):
    trig = _GuardedTrigger()
    monkeypatch.setitem(ingress_mod.CHANNEL_TRIGGER_MAP, "wechat", lambda: trig)
    ingress = ingress_mod.ManagedChannelIngress()

    admitted = await _managed_send(ingress, 12)
    assert admitted < 12, "managed mode must not be the unguarded way in"


async def test_managed_ingress_breaker_leaves_real_conversation_alone(monkeypatch):
    trig = _GuardedTrigger()
    monkeypatch.setitem(ingress_mod.CHANNEL_TRIGGER_MAP, "wechat", lambda: trig)
    ingress = ingress_mod.ManagedChannelIngress()

    admitted = 0
    for i in range(30):
        allow, _ = await ingress.before_run(
            working_source=WorkingSource.WECHAT,
            agent_id="a1",
            user_input=f"a genuinely different message {i}",
            trigger_extra_data=_tagged_extra(source_message_id=f"m{i}"),
            db=object(),
        )
        admitted += 1 if allow else 0
    assert admitted == 30


async def test_managed_deny_answers_with_a_receipt(monkeypatch):
    """A denied managed turn creates no run, so the platform needs an
    answer — silence there reads as the platform never being called."""
    trig = _GuardedTrigger()
    monkeypatch.setitem(ingress_mod.CHANNEL_TRIGGER_MAP, "wechat", lambda: trig)
    ingress = ingress_mod.ManagedChannelIngress()

    receipts = []
    for i in range(12):
        allow, receipt = await ingress.before_run(
            working_source=WorkingSource.WECHAT,
            agent_id="a1",
            user_input="ping",
            trigger_extra_data=_tagged_extra(source_message_id=f"m{i}"),
            db=object(),
        )
        if not allow:
            receipts.append(receipt)
    assert receipts, "expected at least one deny"
    assert all(r for r in receipts), "every deny must carry a receipt"


# ---------------------------------------------------------------------------
# Binding rule #16: a suppressed message must leave a trace
#
# Once the breaker is live, a message judged part of a storm is dropped and
# the person on the other end sees nothing at all — the assistant simply
# stops answering. That is the 0802 symptom, and the 8/14 incident ran 70
# hours precisely because "the bot went quiet" was not answerable from any
# durable record. So every drop writes a row, per message, on purpose.
#
# These pin the row's EXISTENCE and its numbers, on both surfaces. Without
# them the audit call could be deleted, or reduced to one row per trip, and
# every other breaker test would stay green.
# ---------------------------------------------------------------------------


async def test_every_managed_message_the_breaker_drops_leaves_an_audit_row(
    monkeypatch,
):
    from narranexus.platform.channel.channel_audit_events import (
        EVENT_INGRESS_BREAKER_TRIPPED,
        EVENT_INGRESS_DROPPED_BREAKER,
    )

    _AuditRecorder.rows = []
    trig = _GuardedTrigger()
    monkeypatch.setitem(ingress_mod.CHANNEL_TRIGGER_MAP, "wechat", lambda: trig)
    monkeypatch.setattr(ingress_mod, "ChannelTriggerAuditRepository", _AuditRecorder)
    ingress = ingress_mod.ManagedChannelIngress()

    total = 12
    admitted = await _managed_send(ingress, total)
    dropped = total - admitted
    assert dropped > 0, "setup: the storm must have been stopped"

    events = [e for _, e, _ in _AuditRecorder.rows]
    trips = events.count(EVENT_INGRESS_BREAKER_TRIPPED)
    drops = events.count(EVENT_INGRESS_DROPPED_BREAKER)

    assert trips >= 1, "the moment the door closed is not in the DB"
    # The trip itself also suppresses its own message, so it accounts for
    # one of the dropped ones; the rest each get their own row. Per
    # message, not per trip — "how long was it quiet, and how much did it
    # swallow" has to be answerable by counting rows.
    assert trips + drops == dropped, (
        f"{dropped} messages were suppressed but only {trips + drops} rows "
        f"were written: {events}"
    )


async def test_a_dropped_row_carries_the_numbers_that_explain_it(monkeypatch):
    from narranexus.platform.channel.channel_audit_events import (
        EVENT_INGRESS_DROPPED_BREAKER,
    )

    _AuditRecorder.rows = []
    trig = _GuardedTrigger()
    monkeypatch.setitem(ingress_mod.CHANNEL_TRIGGER_MAP, "wechat", lambda: trig)
    monkeypatch.setattr(ingress_mod, "ChannelTriggerAuditRepository", _AuditRecorder)
    ingress = ingress_mod.ManagedChannelIngress()

    await _managed_send(ingress, 12)

    drops = [
        kw for _, e, kw in _AuditRecorder.rows if e == EVENT_INGRESS_DROPPED_BREAKER
    ]
    assert drops, "no drop rows to inspect"
    details = drops[0]["details"]

    # The owner's question is "why did it go quiet, and for how long".
    # A row that only says "dropped" cannot answer either half.
    for field in (
        "session_key", "tier", "reason", "suppressed",
        "cooldown_remaining_seconds",
    ):
        assert field in details, f"{field} missing from {sorted(details)}"
    assert details["tier"] >= 1
    assert details["cooldown_remaining_seconds"] > 0, (
        "a drop row that cannot say how much longer leaves 'until when' "
        "unanswerable, which is half of what these rows are for"
    )


async def test_managed_rows_age_out_without_a_native_trigger(monkeypatch):
    """Managed rows must not depend on a same-named native trigger existing.

    `ChannelTriggerBase._run_cleanup` sweeps only its own `channel_name`,
    and the coordinator has no periodic loop, so without a sweep of its own
    a managed-only deployment would keep these rows forever — the growth
    the retention window exists to stop. The sweep therefore rides the
    ingress path, throttled to once per process-day (same shape as
    `manyfold/files.py`).
    """
    calls = []

    class _Repo:
        def __init__(self, db):
            pass

        async def cleanup_older_than_days(self, days, channel=None):
            calls.append((days, channel))
            return 0

    # Patched on the module, like the sibling `ChannelTriggerAuditRepository`
    # right beside it. If the production import ever moves back inside the
    # function this stops patching anything and the test fails on "never
    # swept" — which reads like the code broke rather than the patch.
    monkeypatch.setattr(ingress_mod, "ChannelIngressBreakerRepository", _Repo)

    class _ShortRetention(_GuardedTrigger):
        # NOT the base class's 30. With the default the assertion below is
        # true whichever value the sweep reads, so it would pass on the
        # implementation this test exists to reject — the same shape as the
        # agent-threshold test that "passed" while the storm never tripped.
        INGRESS_BREAKER_RETENTION_DAYS = 7

    trig = _ShortRetention()
    monkeypatch.setitem(ingress_mod.CHANNEL_TRIGGER_MAP, "wechat", lambda: trig)
    ingress = ingress_mod.ManagedChannelIngress()

    await _managed_send(ingress, 3)

    assert calls, "managed rows are never swept"
    assert calls[0][1] == "wechat", f"the sweep is not channel-scoped: {calls[0]}"
    assert calls[0][0] == 7, (
        "the managed sweep read the base class's window instead of this "
        f"trigger's, so native and managed ticks disagree about the same "
        f"rows: {calls[0]}"
    )
    # Throttled: three turns, one sweep. Sweeping per message would put a
    # full-table read on the hot path.
    assert len(calls) == 1, f"the sweep is not throttled: {len(calls)} calls"


async def test_the_managed_gate_gets_the_agent_peer_answer(monkeypatch):
    """One evaluation feeds both the tag and the breaker.

    The breaker judges an agent peer against its own pair of thresholds —
    that is the whole reason the signal is handed to the gate rather than
    only stamped on the tag. If the gate stopped receiving it, A2A
    conversations would silently fall back to the human thresholds and
    nothing would fail.

    The fake overrides BOTH pairs: the base class's agent thresholds are
    not simply lower, they are a separate pair, so lowering only the human
    ones (as the shared `_GuardedTrigger` does) leaves an agent peer
    judged at the default 20 and this storm never trips at all.
    """
    from narranexus.platform.channel.channel_audit_events import (
        EVENT_INGRESS_BREAKER_TRIPPED,
    )

    class _AgentPeerTrigger(_GuardedTrigger):
        INGRESS_AGENT_RATE_THRESHOLD = 5
        INGRESS_AGENT_DUP_RATIO_THRESHOLD = 0.5

        def is_agent_peer(self, message):
            return True

    _AuditRecorder.rows = []
    trig = _AgentPeerTrigger()
    monkeypatch.setitem(ingress_mod.CHANNEL_TRIGGER_MAP, "wechat", lambda: trig)
    monkeypatch.setattr(ingress_mod, "ChannelTriggerAuditRepository", _AuditRecorder)
    ingress = ingress_mod.ManagedChannelIngress()

    await _managed_send(ingress, 12)

    trips = [
        kw for _, e, kw in _AuditRecorder.rows if e == EVENT_INGRESS_BREAKER_TRIPPED
    ]
    assert trips, "the storm never tripped"
    assert trips[0]["details"]["is_agent_peer"] is True, (
        "the gate judged an agent peer on the human thresholds — the signal "
        "never reached it"
    )


async def test_a_channel_with_the_breaker_off_is_not_swept(monkeypatch):
    """The sweep runs after the guard check, not before it.

    A channel with `INGRESS_GUARD_ENABLED` off never writes a breaker row,
    so scanning that table for it once per process-day is a query that can
    only ever return nothing — and it would sit on the ingress path of a
    user's message to do it.
    """
    calls = []

    class _Repo:
        def __init__(self, db):
            pass

        async def cleanup_older_than_days(self, days, channel=None):
            calls.append((days, channel))
            return 0

    monkeypatch.setattr(ingress_mod, "ChannelIngressBreakerRepository", _Repo)

    class _GuardOff(_GuardedTrigger):
        INGRESS_GUARD_ENABLED = False

    trig = _GuardOff()
    monkeypatch.setitem(ingress_mod.CHANNEL_TRIGGER_MAP, "wechat", lambda: trig)
    ingress = ingress_mod.ManagedChannelIngress()

    admitted = await _managed_send(ingress, 12)

    assert admitted == 12, "with the breaker off nothing may be suppressed"
    assert not calls, (
        f"swept a table this channel never writes to: {calls}"
    )

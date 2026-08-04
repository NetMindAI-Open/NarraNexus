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
from xyz_agent_context.schema.channel_tag import ChannelTag
from xyz_agent_context.schema.hook_schema import WorkingSource


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

from xyz_agent_context.channel.message_source_handler import (  # noqa: E402
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
        _tool_event("mcp__chat_module__send_message_to_user_directly", {"content": "hi"}),
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

from xyz_agent_context.channel.channel_trigger_base import (  # noqa: E402
    CHANNEL_SILENT_SENTINEL,
    ChannelTriggerBase,
)
from xyz_agent_context.module import managed_channel_ingress as ingress_mod  # noqa: E402
from xyz_agent_context.schema.parsed_message import ChatType  # noqa: E402


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
    def __init__(self):
        self.before_calls = []
        self.after_calls = []
        self.allow = (True, "")

    async def managed_before_run(self, **kw):
        self.before_calls.append(kw)
        return self.allow

    async def managed_after_run(self, **kw):
        self.after_calls.append(kw)


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

    monkeypatch.setattr(trig._inbox_writer, "write", fake_inbox_write)
    monkeypatch.setattr(trig, "_audit", fake_audit)
    monkeypatch.setattr(trig, "_send_error_fallback", fake_fallback)
    monkeypatch.setattr(trig, "_credential_for_agent", fake_cred)

    msg = ingress_mod.synthesize_managed_message(_tagged_extra(), "hi")
    await trig.managed_after_run(
        agent_id="a1", message=msg, db=object(), reply_text="", error_text="boom"
    )
    assert "text" in sent
    assert calls["inbox"]["agent_response"] == CHANNEL_SILENT_SENTINEL
    assert calls["inbox"]["original_message"] == "hi"
    assert calls["audit"][0] == "managed_ingress_processed"

    sent.clear()
    await trig.managed_after_run(
        agent_id="a1", message=msg, db=object(), reply_text="done", error_text="boom"
    )
    assert not sent  # agent replied - no fallback
    assert calls["inbox"]["agent_response"] == "done"


async def test_wechat_managed_before_run_claims_owner(monkeypatch):
    from xyz_agent_context.module.wechat_module import wechat_trigger as wt

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
    from xyz_agent_context.module.narramessenger_module import matrix_trigger as mt

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
                "tool_name": "mcp__chat_module__send_message_to_user_directly",
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
    from xyz_agent_context.module.narramessenger_module.narramessenger_module import (
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
    text = await module.get_instructions(ctx)
    assert 'narra_send(room_id="!r:hs"' in text
    assert "Do NOT" in text and "narra_reply" in text


async def test_nm_native_turn_keeps_narra_reply():
    module = _nm_module()
    ctx = SimpleNamespace(
        extra_data={module.ctx_data_key: _nm_info()},
        working_source=WorkingSource.NARRAMESSENGER,
    )
    text = await module.get_instructions(ctx)
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
    import xyz_agent_context.repository.agent_repository as agent_repo_mod
    import xyz_agent_context.utils.attachment_storage as storage_mod
    import xyz_agent_context.utils.workspace_paths as wsp_mod

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
    import xyz_agent_context.repository.agent_repository as agent_repo_mod

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
    tools = await module.get_expressive_tools(managed_ctx)
    assert any("narra_send" in t for t in tools)
    assert not any("narra_reply" in t for t in tools)

    native_ctx = SimpleNamespace(
        extra_data={},
        working_source=WorkingSource.NARRAMESSENGER,
    )
    native = await module.get_expressive_tools(native_ctx)
    assert any("narra_reply" in t for t in native)
    # No-ctx callers (tests, other frameworks) keep the full declaration.
    assert await module.get_expressive_tools() == native


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

    async def no_cred(agent_id):
        return None

    monkeypatch.setattr(trig, "_credential_for_agent", no_cred)
    receipt2 = await trig.managed_silent_ingest(
        agent_id="a1", message=msg, db=object()
    )
    assert "dropped" in receipt2

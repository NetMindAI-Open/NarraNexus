"""
@file_name: test_channel_circuit_breaker_gate.py
@author: NarraNexus
@date: 2026-09-10
@description: Every IM-channel / A2A turn entry passes the agent
circuit-breaker gate (#394 second review I-3).

Before this, ``ChannelTriggerBase._build_and_run_agent`` (every channel that
does not override it), ``LarkTrigger``'s wholesale override, NarraMessenger's
streaming path and the A2A server started a real turn for every inbound
message with no breaker check at all: a hard-PAUSED agent with a dead key
re-ran a doomed turn per message. Each entry now calls ``admit_turn`` right
before its runtime call, tells the sender why when refused, hands a won
probe token to the runtime call (which settles it) and releases it on the
way out. The silent memory batch is not gated at all (it makes no agent LLM
call) and never takes the probe slot. The real breaker runs on sqlite; only
the runtime is faked.
"""
from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import narranexus.platform.agent_framework.loop.circuit_breaker as cb
from narranexus.platform.agent_runtime.run_collector import RunCollection
from narranexus.platform.channel.channel_trigger_base import (
    SILENT_BATCH_EMPTY,
    SILENT_BATCH_RUNTIME_RAISED,
)
from narranexus.platform.repository.agent_circuit_breaker_repository import (
    AgentCircuitBreakerRepository,
)
from narranexus.platform.schema import CbStatus, ErrorCategory, PausedReason
from narranexus.platform.schema.parsed_message import ChatType, ParsedMessage
from narranexus.platform.utils.timezone import utc_now
from tests.channel.test_mock_channel_trigger_integration import (
    _FakeCredential,
    _FakeTrigger,
)

AGENT = "agent_a"


class _Client:
    """Runtime client double: records every call's kwargs and the breaker
    row as the turn saw it. It never settles a probe itself, so after the
    call the entry's exit belt must have handed a claim back."""

    def __init__(self, db=None, raises: BaseException | None = None):
        self.calls: list[dict] = []
        self.rows_at_call: list = []
        self._db = db
        self._raises = raises

    async def _snapshot(self):
        if self._db is not None:
            self.rows_at_call.append(await AgentCircuitBreakerRepository(self._db).get(AGENT))

    async def run_and_collect(self, **kw):
        self.calls.append(kw)
        await self._snapshot()
        if self._raises is not None:
            raise self._raises
        return RunCollection(output_text="hello")

    def run_stream(self, **kw):
        self.calls.append(kw)

        async def _gen():
            await self._snapshot()
            if False:  # pragma: no cover — makes this an async generator
                yield None
        return _gen()


class _Trigger(_FakeTrigger):
    def __init__(self, fail_sends: int = 0):
        super().__init__([], _FakeCredential(agent_id=AGENT))
        self.sent: list[str] = []
        self.failed: list[str] = []
        self._fail_sends = fail_sends

    async def send_channel_reply(self, credential, message, text) -> None:
        if self._fail_sends:
            self._fail_sends -= 1
            self.failed.append(text)
            raise RuntimeError("platform send failed")
        self.sent.append(text)


def _msg(chat_type: ChatType = ChatType.PRIVATE, chat_id: str = "C1") -> ParsedMessage:
    return ParsedMessage(
        message_id="m1", chat_id=chat_id, sender_id="u1",
        sender_name="Alice", content="hi", timestamp_ms=1,
        chat_type=chat_type,
    )


@pytest.fixture(autouse=True)
def _fresh_refusal_windows():
    from narranexus.platform.channel.channel_trigger_base import ChannelTriggerBase

    ChannelTriggerBase._circuit_refusal_windows.clear()
    yield
    ChannelTriggerBase._circuit_refusal_windows.clear()


@pytest.fixture
def client(monkeypatch, db_client):
    async def _db():
        return db_client
    monkeypatch.setattr(cb, "get_db_client", _db)
    monkeypatch.setattr("narranexus.platform.utils.db.db_factory.get_db_client", _db)
    holder = {"client": _Client(db_client)}
    monkeypatch.setattr(
        "narranexus.platform.agent_runtime.client.get_agent_runtime_client",
        lambda: holder["client"],
    )
    # The A2A server binds the factory at import time.
    monkeypatch.setattr(
        "narranexus_plugins.chat_module.chat_trigger.get_agent_runtime_client",
        lambda: holder["client"],
    )
    return holder


async def _set(db, status: CbStatus, *, due: bool) -> None:
    await AgentCircuitBreakerRepository(db).upsert_state(AGENT, {
        "cb_status": status.value,
        "paused_reason": PausedReason.AUTH.value if status == CbStatus.PAUSED else None,
        "failure_category": ErrorCategory.AUTH.value,
        "consecutive_failure_count": 3,
        "cooldown_until": utc_now() + (timedelta(seconds=-1) if due else timedelta(minutes=5)),
    })


async def _row(db):
    return await AgentCircuitBreakerRepository(db).get(AGENT)


async def _run_base(trigger, db, msg: ParsedMessage | None = None):
    trigger._db = db
    return await trigger._build_and_run_agent(
        trigger._credential, msg or _msg(), "Alice", attachments=[]
    )


# ── ChannelTriggerBase._build_and_run_agent ─────────────────────────────


@pytest.mark.asyncio
async def test_a_paused_agent_runs_no_turn_and_the_sender_is_told(client, db_client):
    await _set(db_client, CbStatus.PAUSED, due=False)
    trigger = _Trigger()
    out = await _run_base(trigger, db_client)
    assert client["client"].calls == []
    assert trigger.sent == [out]
    assert "paused" in out.lower()


@pytest.mark.asyncio
async def test_a_cooling_agent_asks_to_try_again(client, db_client):
    await _set(db_client, CbStatus.COOLING, due=False)
    trigger = _Trigger()
    out = await _run_base(trigger, db_client)
    assert client["client"].calls == []
    assert "try again shortly" in out.lower()


@pytest.mark.asyncio
async def test_an_open_half_open_window_claims_and_hands_the_token_on(client, db_client):
    await _set(db_client, CbStatus.PAUSED, due=True)
    trigger = _Trigger()
    await _run_base(trigger, db_client)
    (call,) = client["client"].calls
    (seen,) = client["client"].rows_at_call
    assert seen.cb_status == CbStatus.PROBING.value
    assert call["probe_token"] == seen.probe_token and seen.probe_token
    assert trigger.sent == []
    # The double never settled; the exit belt handed the claim back.
    after = await _row(db_client)
    assert after.cb_status == CbStatus.PAUSED.value and after.probe_token is None


@pytest.mark.asyncio
async def test_a_healthy_agent_runs_without_a_token(client, db_client):
    trigger = _Trigger()
    out = await _run_base(trigger, db_client)
    (call,) = client["client"].calls
    assert call["probe_token"] is None
    assert out  # the agent's own output, not a refusal
    assert await _row(db_client) is None


@pytest.mark.asyncio
async def test_a_probe_whose_runtime_call_raises_is_handed_back(client, db_client):
    """The runtime call never reached its own settlement: the exit belt
    returns the claim instead of leaving every entry refused until the
    grant expires."""
    await _set(db_client, CbStatus.PAUSED, due=True)
    client["client"] = _Client(db_client, raises=RuntimeError("runtime construction failed"))
    trigger = _Trigger()
    await _run_base(trigger, db_client)
    row = await _row(db_client)
    assert row.cb_status == CbStatus.PAUSED.value
    assert row.probe_token is None
    assert row.consecutive_failure_count == 3


# ── refusal notice throttling (#394 fourth review I-C) ──────────────────


@pytest.mark.asyncio
async def test_a_group_hears_the_refusal_once_per_breaker_window(client, db_client):
    await _set(db_client, CbStatus.PAUSED, due=False)
    trigger = _Trigger()
    first = await _run_base(trigger, db_client, _msg(ChatType.GROUP))
    second = await _run_base(trigger, db_client, _msg(ChatType.GROUP))
    assert client["client"].calls == []
    # Only the send is throttled: both turns still return the refusal
    # as their output, so the inbox records each of them.
    assert first == second and "paused" in first.lower()
    assert trigger.sent == [first]

    # Another group of the same agent is a different conversation.
    await _run_base(trigger, db_client, _msg(ChatType.GROUP, chat_id="C2"))
    assert len(trigger.sent) == 2

    # A new window (a failed probe doubled the backoff) is told again.
    await AgentCircuitBreakerRepository(db_client).upsert_state(AGENT, {
        "cooldown_until": utc_now() + timedelta(minutes=10),
    })
    await _run_base(trigger, db_client, _msg(ChatType.GROUP))
    assert len(trigger.sent) == 3


@pytest.mark.asyncio
async def test_a_failed_group_refusal_send_hands_the_window_back(client, db_client):
    """#394 fifth review M-1: the window is claimed before the send and
    rolled back when the send fails, so the room is not left untold for
    the whole pause."""
    await _set(db_client, CbStatus.PAUSED, due=False)
    trigger = _Trigger(fail_sends=1)
    first = await _run_base(trigger, db_client, _msg(ChatType.GROUP))
    assert trigger.failed == [first] and trigger.sent == []
    await _run_base(trigger, db_client, _msg(ChatType.GROUP))
    assert trigger.sent == [first]
    # Delivered now: the window holds again.
    await _run_base(trigger, db_client, _msg(ChatType.GROUP))
    assert trigger.sent == [first]


@pytest.mark.asyncio
async def test_the_refusal_window_comes_from_the_gate_read(client, db_client, monkeypatch):
    """#394 fifth review M-2: the throttle keys on the window of the same
    row read that refused the turn — one breaker read per refused turn."""
    await _set(db_client, CbStatus.PAUSED, due=False)
    row = await _row(db_client)
    admission = await cb.admit_turn(AGENT, db=db_client)
    assert admission.window == f"{row.cb_status}|{row.cooldown_until}"

    reads = []
    real_get = AgentCircuitBreakerRepository.get

    async def counting_get(self, agent_id):
        reads.append(agent_id)
        return await real_get(self, agent_id)

    monkeypatch.setattr(AgentCircuitBreakerRepository, "get", counting_get)
    trigger = _Trigger()
    await _run_base(trigger, db_client, _msg(ChatType.GROUP))
    assert reads == [AGENT]
    assert len(trigger.sent) == 1


@pytest.mark.asyncio
async def test_a_private_chat_is_told_every_time(client, db_client):
    await _set(db_client, CbStatus.PAUSED, due=False)
    trigger = _Trigger()
    await _run_base(trigger, db_client, _msg(ChatType.PRIVATE))
    await _run_base(trigger, db_client, _msg(ChatType.PRIVATE))
    assert len(trigger.sent) == 2


# ── silent memory batch: not gated, never claims ────────────────────────


@pytest.mark.asyncio
async def test_the_silent_batch_never_claims_the_probe(client, db_client):
    trigger = _Trigger()
    trigger._db = db_client
    await _set(db_client, CbStatus.PAUSED, due=True)
    assert await trigger._build_and_run_agent_silent_batch(
        trigger._credential, [_msg()]
    ) is None
    (call,) = client["client"].calls
    assert call["silent"] is True
    assert "probe_token" not in call
    row = await _row(db_client)
    assert row.cb_status == CbStatus.PAUSED.value and row.probe_token is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "due"),
    [
        (CbStatus.COOLING, False),
        (CbStatus.PAUSED, False),
        (CbStatus.PROBING, False),
    ],
)
async def test_the_silent_batch_runs_whatever_the_breaker_holds(
    client, db_client, status, due
):
    """#394 fifth review N-1 / sixth review I-2: ``silent=True`` runs
    SilentAct, which starts no agent-slot turn (zero agent LLM calls) — the
    kind of turn the breaker guards — and the batch has no retry queue:
    skipping it would lose the room from memory for good."""
    trigger = _Trigger()
    trigger._db = db_client
    await _set(db_client, status, due=due)
    await trigger._build_and_run_agent_silent_batch(trigger._credential, [_msg()])
    assert len(client["client"].calls) == 1
    assert (await _row(db_client)).cb_status == status.value


@pytest.mark.asyncio
async def test_the_silent_batch_reports_a_pass_that_did_not_run(client, db_client):
    """#394 fifth review N-2: the managed receipt must not claim "ingested"
    for a pass that never ran."""
    trigger = _Trigger()
    trigger._db = db_client
    client["client"] = _Client(db_client, raises=RuntimeError("boom"))
    why = await trigger._build_and_run_agent_silent_batch(trigger._credential, [_msg()])
    # A stable code (#394 sixth review M-3): the exception class stays in
    # the log, never in the platform-facing receipt.
    assert why == SILENT_BATCH_RUNTIME_RAISED == "runtime_raised"
    assert await trigger._build_and_run_agent_silent_batch(
        trigger._credential, []
    ) == SILENT_BATCH_EMPTY


# ── LarkTrigger overrides _build_and_run_agent wholesale ────────────────


class _LarkCred:
    agent_id = AGENT
    app_id = "cli_test"
    app_secret_ref = "ref"
    brand = "lark"
    profile_name = "p"


class _LarkBuilder:
    def __init__(self, *a, **k):
        pass

    async def build_prompt(self, _history_config):
        return "prompt"

    async def build_retrieval_anchor(self):
        return None


async def _run_lark(monkeypatch, db):
    from narranexus_plugins.lark_module import lark_trigger as lt_mod
    from narranexus_plugins.lark_module.lark_trigger import LarkTrigger

    monkeypatch.setattr(lt_mod, "LarkContextBuilder", _LarkBuilder)
    trigger = LarkTrigger()
    trigger._db = db
    trigger._cli = SimpleNamespace(send_message=AsyncMock())
    out = await trigger._build_and_run_agent(
        cred=_LarkCred(), event={"chat_type": "p2p"}, chat_id="oc", sender_id="ou",
        sender_name="Alice", text="hi", message_id="lm1",
    )
    return trigger, out


@pytest.mark.asyncio
async def test_lark_refuses_a_paused_agent(client, db_client, monkeypatch):
    await _set(db_client, CbStatus.PAUSED, due=False)
    trigger, out = await _run_lark(monkeypatch, db_client)
    assert client["client"].calls == []
    trigger._cli.send_message.assert_awaited_once()
    assert trigger._cli.send_message.await_args.kwargs["text"] == out


@pytest.mark.asyncio
async def test_lark_claims_an_open_window(client, db_client, monkeypatch):
    await _set(db_client, CbStatus.PAUSED, due=True)
    await _run_lark(monkeypatch, db_client)
    (call,) = client["client"].calls
    (seen,) = client["client"].rows_at_call
    assert call["probe_token"] and call["probe_token"] == seen.probe_token
    assert (await _row(db_client)).probe_token is None


# ── NarraMessenger streaming path ───────────────────────────────────────


async def _run_matrix(
    monkeypatch, db, *, chat_type=ChatType.PRIVATE, times=1, atomic=False
):
    from narranexus_plugins.narramessenger_module.matrix_trigger import MatrixTrigger

    trigger = MatrixTrigger()
    if atomic:
        monkeypatch.setattr(trigger, "STREAMING_ENABLED", False)
        # The agent's answer as this channel extracts it from the run.
        monkeypatch.setattr(trigger, "resolve_agent_response", lambda *a: "the answer")
    trigger._db = db
    monkeypatch.setattr(trigger, "create_context_builder", lambda *a, **k: _LarkBuilder())
    monkeypatch.setattr(trigger, "_resolve_agent_owner", AsyncMock(return_value="owner"))
    sends = AsyncMock()
    monkeypatch.setattr(trigger, "_send_matrix_reply", sends)
    cred = SimpleNamespace(agent_id=AGENT)
    msg = SimpleNamespace(
        chat_id="!r", message_id="$e", sender_id="@u:h", content="hi",
        raw={}, timestamp_ms=0, sender_name="U", chat_type=chat_type,
    )
    out = None
    for _ in range(times):
        if atomic:
            # The dispatcher: STREAMING_ENABLED=False routes to the atomic path.
            out = await trigger._build_and_run_agent(cred, msg, "U", attachments=None)
        else:
            out = await trigger._build_and_run_agent_streaming(
                cred, msg, "U", attachments=None
            )
    return sends, out


@pytest.mark.asyncio
async def test_matrix_streaming_refuses_a_paused_agent(client, db_client, monkeypatch):
    await _set(db_client, CbStatus.PAUSED, due=False)
    sends, out = await _run_matrix(monkeypatch, db_client)
    assert client["client"].calls == []
    sends.assert_awaited_once()
    assert sends.await_args.args[2] == out and "paused" in out.lower()


@pytest.mark.asyncio
async def test_matrix_group_refusal_is_sent_once_per_window(client, db_client, monkeypatch):
    await _set(db_client, CbStatus.PAUSED, due=False)
    sends, out = await _run_matrix(monkeypatch, db_client, chat_type=ChatType.GROUP, times=2)
    assert client["client"].calls == []
    sends.assert_awaited_once()
    assert "paused" in out.lower()


@pytest.mark.asyncio
async def test_matrix_atomic_sends_a_refusal_once(client, db_client, monkeypatch):
    """#394 fifth review N-3: the atomic path (streaming kill switch off)
    must not re-send the refusal the base gate already delivered."""
    await _set(db_client, CbStatus.PAUSED, due=False)
    sends, out = await _run_matrix(monkeypatch, db_client, atomic=True)
    assert client["client"].calls == []
    sends.assert_awaited_once()
    assert sends.await_args.args[2] == out and "paused" in out.lower()


@pytest.mark.asyncio
async def test_matrix_atomic_group_refusal_is_throttled(client, db_client, monkeypatch):
    await _set(db_client, CbStatus.PAUSED, due=False)
    sends, _ = await _run_matrix(
        monkeypatch, db_client, chat_type=ChatType.GROUP, times=3, atomic=True
    )
    sends.assert_awaited_once()


@pytest.mark.asyncio
async def test_matrix_atomic_still_sends_the_agent_answer(client, db_client, monkeypatch):
    sends, out = await _run_matrix(monkeypatch, db_client, atomic=True)
    (call,) = client["client"].calls
    assert call["probe_token"] is None
    sends.assert_awaited_once()
    assert sends.await_args.args[2] == out == "the answer"


@pytest.mark.asyncio
async def test_matrix_streaming_claims_an_open_window(client, db_client, monkeypatch):
    await _set(db_client, CbStatus.PAUSED, due=True)
    await _run_matrix(monkeypatch, db_client)
    (call,) = client["client"].calls
    (seen,) = client["client"].rows_at_call
    assert call["probe_token"] and call["probe_token"] == seen.probe_token
    assert (await _row(db_client)).probe_token is None


# ── A2A server (tasks/send) ─────────────────────────────────────────────


def _a2a_params() -> dict:
    return {"message": {
        "role": "user",
        "parts": [{"type": "text", "text": "hi"}],
        "metadata": {"agent_id": AGENT, "user_id": "u"},
    }}


async def _a2a_send(db):
    from narranexus_plugins.chat_module.chat_trigger import A2AServer

    server = A2AServer(database_client=db)
    return await server._handle_tasks_send("1", _a2a_params())


@pytest.mark.asyncio
async def test_a2a_send_refuses_a_paused_agent(client, db_client):
    await _set(db_client, CbStatus.PAUSED, due=False)
    task = await _a2a_send(db_client)
    assert client["client"].calls == []
    assert task["status"]["state"] == "failed"
    assert "paused:auth" in str(task["status"]["message"])


@pytest.mark.asyncio
async def test_a2a_send_claims_an_open_window_and_runs_healthy(client, db_client):
    await _set(db_client, CbStatus.PAUSED, due=True)
    await _a2a_send(db_client)
    (call,) = client["client"].calls
    (seen,) = client["client"].rows_at_call
    assert call["probe_token"] and call["probe_token"] == seen.probe_token
    assert (await _row(db_client)).probe_token is None

    await AgentCircuitBreakerRepository(db_client).upsert_state(AGENT, cb._CLEAN_STATE)
    await _a2a_send(db_client)
    assert len(client["client"].calls) == 2
    assert client["client"].calls[-1]["probe_token"] is None


# ── A2A server (tasks/sendSubscribe, SSE) ───────────────────────────────


class _DeltaClient(_Client):
    def run_stream(self, **kw):
        self.calls.append(kw)

        async def _gen():
            await self._snapshot()
            yield SimpleNamespace(delta="he")
            yield SimpleNamespace(delta="llo")
        return _gen()


async def _a2a_subscribe(db):
    from narranexus_plugins.chat_module.chat_trigger import A2AServer

    server = A2AServer(database_client=db)
    resp = await server._handle_tasks_send_subscribe("1", _a2a_params(), None)
    return resp.body_iterator


@pytest.mark.asyncio
async def test_a2a_subscribe_refuses_a_paused_agent(client, db_client):
    import json

    await _set(db_client, CbStatus.PAUSED, due=False)
    before = await _row(db_client)
    frames = [f async for f in await _a2a_subscribe(db_client)]
    assert client["client"].calls == []
    last = json.loads(frames[-1]["data"])
    assert last["final"] is True and last["status"]["state"] == "failed"
    after = await _row(db_client)
    assert (after.cb_status, after.probe_token, after.cooldown_until) == (
        before.cb_status, before.probe_token, before.cooldown_until,
    )


@pytest.mark.asyncio
async def test_a2a_subscribe_closed_mid_stream_hands_the_claim_back(client, db_client):
    """The client disconnects after the gate claimed the probe and before
    the stream ended: the generator's exit belt returns the claim instead
    of leaving every entry refused until the grant expires."""
    await _set(db_client, CbStatus.PAUSED, due=True)
    client["client"] = _DeltaClient(db_client)
    frames = await _a2a_subscribe(db_client)
    seen = []
    async for frame in frames:
        seen.append(frame)
        if frame["event"] == "taskArtifactUpdate":
            break
    (row_at_call,) = client["client"].rows_at_call
    assert row_at_call.cb_status == CbStatus.PROBING.value
    await frames.aclose()
    after = await _row(db_client)
    assert after.cb_status == CbStatus.PAUSED.value and after.probe_token is None


# ── Who may consume _build_and_run_agent's bare text ────────────────────

# Callers of ``_build_and_run_agent`` (which returns the text only and so
# drops ``ChannelTurnOutput.refused``), each with its call count and what it
# does with the text. Baseline counted on 2026-09-11 with ``_CALL`` below
# over plugins/**/src and src/narranexus/platform/channel (#394 sixth review
# M-1): the base ``_handle_message`` and Lark's ``_handle_message`` both
# only write the text to the inbox. A caller that SENDS the text to the chat
# must call ``_run_agent_turn`` instead and skip the send when ``refused``
# (the gate already delivered or throttled the refusal) — the Matrix atomic
# path double-sent before it did (fifth review N-3).
_BARE_TEXT_CALLERS = {
    "src/narranexus/platform/channel/channel_trigger_base.py": (1, "inbox only"),
    "plugins/builtin.channels.lark/src/narranexus_plugins/lark_module/lark_trigger.py": (
        1, "inbox only"),
}
# Overrides of ``_build_and_run_agent`` (same baseline and scope): an
# override replaces the gated base body, so it must gate its own turn (Lark,
# via ``_circuit_admission``) or delegate to ``_run_agent_turn`` (Matrix).
_OVERRIDES = {
    "src/narranexus/platform/channel/channel_trigger_base.py": 1,
    "plugins/builtin.channels.lark/src/narranexus_plugins/lark_module/lark_trigger.py": 1,
    "plugins/builtin.channels.narramessenger/src/narranexus_plugins/narramessenger_module/matrix_trigger.py": 1,
}
_CALL = r"await self\._build_and_run_agent\("
_DEF = r"async def _build_and_run_agent\("


def test_only_registered_channels_consume_the_bare_turn_text():
    """A new caller or override of ``_build_and_run_agent`` fails until the
    author confirms it does not send the returned text itself (or switches
    to ``_run_agent_turn``) and registers it above."""
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    files = [p for p in root.joinpath("plugins").rglob("*.py") if "/src/" in p.as_posix()]
    files += list(root.joinpath("src/narranexus/platform/channel").rglob("*.py"))
    calls, overrides = {}, {}
    for path in files:
        text = path.read_text(encoding="utf-8")
        rel = path.relative_to(root).as_posix()
        if n := len(re.findall(_CALL, text)):
            calls[rel] = n
        if n := len(re.findall(_DEF, text)):
            overrides[rel] = n
    assert calls == {rel: n for rel, (n, _what) in _BARE_TEXT_CALLERS.items()}
    assert overrides == _OVERRIDES
    for rel in _OVERRIDES:
        if rel.endswith("channel_trigger_base.py"):
            continue
        text = root.joinpath(rel).read_text(encoding="utf-8")
        assert "_circuit_admission(" in text or "self._run_agent_turn(" in text, rel

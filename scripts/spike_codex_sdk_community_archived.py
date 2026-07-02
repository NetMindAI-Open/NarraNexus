"""
@file_name: spike_codex_sdk.py
@author: NarraNexus
@date: 2026-06-01
@description: Codex SDK migration capability spike.

Targets the community-published ``openai-codex-sdk`` 0.1.11
(import name ``openai_codex_sdk``) verified installed in the
NarraNexus dev venv on 2026-06-01. Its exports include:

  Codex, Thread, StreamedTurn, Turn
  ThreadOptions, TurnOptions
  SandboxMode, ApprovalMode, ModelReasoningEffort
  AbortController, AbortSignal, AbortError
  TextInput, LocalImageInput, UserInput
  ItemStartedEvent, ItemCompletedEvent, ItemUpdatedEvent
  ThreadStartedEvent, ThreadErrorEvent, ThreadEvent
  TurnStartedEvent, TurnCompletedEvent, TurnFailedEvent
  AgentMessageItem, ReasoningItem, CommandExecutionItem,
  McpToolCallItem, FileChangeItem, WebSearchItem, ErrorItem,
  TodoItem, TodoListItem
  login_with_auth_json, login_with_device_code

This spike answers three questions before we commit to migrating
``xyz_codex_cli_sdk.py``:

  0. SIGNATURE DISCOVERY — what do ``Codex.__init__``,
     ``codex.start_thread``, ``thread.run_streamed``, and
     ``ThreadOptions`` actually accept? We don't know yet, and
     guessing is how the migration breaks. The preamble uses
     ``inspect.signature`` so we get authoritative answers.

  A. MCP wiring — can ``ThreadOptions`` (or some equivalent) accept
     ``mcp_servers`` directly so we drop our hand-written
     ``$CODEX_HOME/config.toml``? Test A runs the agent with one
     MCP server passed via SDK config and watches for a
     ``McpToolCallItem`` in the event stream.

  B. Cancellation — does ``AbortController().abort()`` (the JS-style
     cancel surfaced in this SDK) release the streamed-turn awaiter
     within a few seconds? Test B starts a long counting prompt,
     aborts after 3s, measures the unblock latency.

Prerequisites
-------------
* ``uv pip install openai-codex-sdk`` (already done — Audit OK).
* ``codex login`` ran previously (so ``~/.codex/auth.json`` exists).
* HTTPS_PROXY / HTTP_PROXY exported (CN/HK network).
* Test A only: ``bash run.sh`` running so lark_module MCP is up at
  http://localhost:7831/mcp. Otherwise the agent has nothing to call
  and Test A is inconclusive.

Usage
-----
    python scripts/spike_codex_sdk.py

Paste the output back. Output shape:
  Section 0 → signatures (authoritative API contract)
  Section A → MCP transcript (PASS / FAIL / ERROR)
  Section B → cancellation timing (PASS / FAIL / ERROR)
"""
from __future__ import annotations

import asyncio
import inspect
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path


# Tunable probe parameters — adjust if your local NarraNexus is
# bound to different ports / a different model.
MCP_PROBE_NAME = "lark_module"
MCP_PROBE_URL = "http://localhost:7831/mcp"
PROBE_MODEL = "gpt-5.4-mini"  # ChatGPT-OAuth tier (verified 2026-06-01)
TEST_A_TIMEOUT_S = 60.0
TEST_B_RUN_BEFORE_CANCEL_S = 3.0
TEST_B_MAX_UNBLOCK_S = 5.0


def _stage_auth(codex_home: Path) -> bool:
    src = Path.home() / ".codex" / "auth.json"
    if not src.exists():
        return False
    shutil.copy(src, codex_home / "auth.json")
    return True


# ---------------- Section 0: API discovery --------------------------


def _print_signatures() -> object | None:
    """Import the SDK and print the signatures of the symbols Test
    A and B want to call. If any expected symbol is missing, return
    None so main() short-circuits with a useful diagnostic."""
    print("=" * 64)
    print("Section 0: SDK signature discovery")
    print("=" * 64)
    try:
        import openai_codex_sdk as oc  # type: ignore[import-not-found]
    except ImportError:
        print("  openai_codex_sdk: NOT importable")
        print("  Install: uv pip install openai-codex-sdk")
        return None

    print(f"  package version: {getattr(oc, '__version__', '(no __version__)')}")
    print()

    def _sig(target: object, label: str) -> None:
        try:
            sig = inspect.signature(target)
        except (TypeError, ValueError) as e:
            print(f"  {label}: <not introspectable: {e}>")
            return
        print(f"  {label}{sig}")

    # Constructors / factories the migration path will lean on.
    candidates = [
        ("Codex.__init__", getattr(oc.Codex, "__init__", None)),
        ("CodexOptions", getattr(oc, "CodexOptions", None)),
        ("Codex.start_thread", getattr(oc.Codex, "start_thread", None)),
        ("Thread.run", getattr(oc.Thread, "run", None)),
        ("Thread.run_streamed", getattr(oc.Thread, "run_streamed", None)),
        ("ThreadOptions", oc.ThreadOptions),
        ("TurnOptions", oc.TurnOptions),
        ("AbortController.__init__", getattr(oc.AbortController, "__init__", None)),
        ("AbortController.abort", getattr(oc.AbortController, "abort", None)),
        ("TextInput", getattr(oc, "TextInput", None)),
        ("UserInput", getattr(oc, "UserInput", None)),
    ]
    for label, target in candidates:
        if target is None:
            print(f"  {label}: <not exported>")
        else:
            _sig(target, label)

    # ``SandboxMode``, ``ApprovalMode``, ``ModelReasoningEffort`` are
    # ``typing.Literal[...]`` aliases here, NOT ``enum.Enum`` classes
    # — iterating them with ``for m in cls`` raises AttributeError.
    # The valid values live in the ``__args__`` tuple on the typing
    # alias instead.
    print()
    for type_name in ("SandboxMode", "ApprovalMode", "ModelReasoningEffort"):
        type_obj = getattr(oc, type_name, None)
        if type_obj is None:
            print(f"  {type_name}: <not exported>")
            continue
        if hasattr(type_obj, "__args__"):
            print(f"  {type_name} values: {list(type_obj.__args__)}")
        else:
            try:
                print(f"  {type_name} values: "
                      f"{[m.name for m in type_obj]}")  # type: ignore[attr-defined]
            except (TypeError, AttributeError):
                print(f"  {type_name}: {type_obj!r}")

    print()
    return oc


# ---------------- Section A: MCP wiring -----------------------------


async def test_a_mcp_wiring(oc: object) -> bool:
    """Verify the SDK respects ``$CODEX_HOME/config.toml`` for MCP
    server config, since the discovered ``ThreadOptions`` API has no
    ``mcp_servers`` field. We write a minimal config.toml the way
    ``xyz_codex_cli_sdk._codex_config_toml_builder`` does today,
    then drive ``Thread.run_streamed`` and watch for an
    ``McpToolCallItem`` in the event stream.

    PASS = at least one ``McpToolCallItem`` observed within
    TEST_A_TIMEOUT_S. PASS proves the migration path:

        keep config.toml writing (no API for mcp_servers)
        drop subprocess spawn (use SDK)

    FAIL means the SDK ignores config.toml and we'd have to keep
    spawning ``codex exec`` ourselves — the migration loses most
    of its value.
    """
    print("=" * 64)
    print("Section A: MCP wiring via $CODEX_HOME/config.toml + SDK")
    print("=" * 64)

    with tempfile.TemporaryDirectory(prefix="spike_codex_a_") as home_str:
        codex_home = Path(home_str)

        # 1. instructions.md — model_instructions_file points here.
        instructions = codex_home / "instructions.md"
        instructions.write_text(
            "You are a probe. You have MCP tools available from the "
            f"'{MCP_PROBE_NAME}' server. When asked what MCP tools "
            "you have, CALL one of them (any will do) to confirm. "
            "Do NOT use Bash; do NOT speculate from memory.\n"
        )

        # 2. config.toml — mcp_servers + model_instructions_file.
        config_toml = (
            f'model_instructions_file = "{instructions}"\n'
            f"\n"
            f"[mcp_servers.{MCP_PROBE_NAME}]\n"
            f'url = "{MCP_PROBE_URL}"\n'
        )
        (codex_home / "config.toml").write_text(config_toml)

        if not _stage_auth(codex_home):
            print("  WARN: ~/.codex/auth.json missing — Test A will likely fail auth")

        # 3. CODEX_HOME → env. Codex CLI (and therefore the SDK that
        # spawns it) reads $CODEX_HOME/config.toml on startup.
        os.environ["CODEX_HOME"] = str(codex_home)

        print(f"  CODEX_HOME    = {codex_home}")
        print(f"  config.toml   = (written, {len(config_toml)} bytes)")
        print(f"  mcp probe url = {MCP_PROBE_URL}")
        print()

        try:
            codex_inst = oc.Codex()  # type: ignore[attr-defined]
            thread_options = oc.ThreadOptions(  # type: ignore[attr-defined]
                workingDirectory=str(codex_home),
                skipGitRepoCheck=True,
                sandboxMode="workspace-write",
                model=PROBE_MODEL,
                modelReasoningEffort="low",
                approvalPolicy="never",
            )
            thread = codex_inst.start_thread(thread_options)
        except Exception as e:  # noqa: BLE001
            print(f"  setup failed: {type(e).__name__}: {e}")
            return False

        try:
            text_input_cls = oc.TextInput  # type: ignore[attr-defined]
            # The SDK iterates ``input_`` expecting each entry to be
            # dict-like (Input = list[InputItem]). A single pydantic
            # ``TextInput`` model gets iterated as ``(field, value)``
            # tuples and fails validation. Wrap in a one-element list.
            input_list = [
                text_input_cls(
                    type="text",
                    text="What MCP tools do you have? Call one to confirm.",
                )
            ]
            # ``Thread.run_streamed`` is async — must await even though
            # inspect.signature doesn't surface the ``async def`` keyword.
            streamed = await thread.run_streamed(input_list)
        except Exception as e:  # noqa: BLE001
            print(f"  run_streamed failed: {type(e).__name__}: {e}")
            return False

        async def _aiter(obj: object):
            if hasattr(obj, "events"):
                async for ev in obj.events:  # type: ignore[attr-defined]
                    yield ev
            elif hasattr(obj, "__aiter__"):
                async for ev in obj:  # type: ignore[misc]
                    yield ev
            else:
                raise RuntimeError(f"Cannot iterate {type(obj).__name__}")

        mcp_seen = False
        t0 = time.time()
        try:
            async for event in _aiter(streamed):
                dt = time.time() - t0
                cls = type(event).__name__
                item = getattr(event, "item", None)
                item_cls = type(item).__name__ if item is not None else "—"
                print(f"  [{dt:5.1f}s] {cls:24s} item={item_cls}")
                if "McpToolCall" in item_cls:
                    mcp_seen = True
                if dt > TEST_A_TIMEOUT_S:
                    print(f"  [TIMEOUT after {TEST_A_TIMEOUT_S:.0f}s]")
                    break
        except Exception as e:  # noqa: BLE001
            print(f"  event iteration errored: {type(e).__name__}: {e}")
            return False

        print()
        if mcp_seen:
            print("  RESULT: PASS — McpToolCallItem observed; SDK respects "
                  "$CODEX_HOME/config.toml")
            return True
        else:
            print("  RESULT: FAIL/AMBIGUOUS — no McpToolCallItem in stream")
            print("    Either: agent declined to call a tool,")
            print("    or:     SDK did not read config.toml from CODEX_HOME.")
            return False


# ---------------- Section B: cancellation ---------------------------


async def test_b_cancellation(oc: object) -> bool:
    """Start a long counting turn, ``AbortController.abort()`` it
    after 3s via ``TurnOptions(signal=controller.signal)``, measure
    how long until the awaiter releases. PASS = under
    TEST_B_MAX_UNBLOCK_S seconds.

    Discovered API: signal is passed as part of TurnOptions (second
    positional arg to ``run_streamed``), NOT as a kwarg directly.
    """
    print("=" * 64)
    print("Section B: cancellation via TurnOptions(signal=...)")
    print("=" * 64)

    with tempfile.TemporaryDirectory(prefix="spike_codex_b_") as home_str:
        codex_home = Path(home_str)
        _stage_auth(codex_home)
        os.environ["CODEX_HOME"] = str(codex_home)

        try:
            codex_inst = oc.Codex()  # type: ignore[attr-defined]
            thread_options = oc.ThreadOptions(  # type: ignore[attr-defined]
                workingDirectory=str(codex_home),
                skipGitRepoCheck=True,
                sandboxMode="workspace-write",
                model=PROBE_MODEL,
                modelReasoningEffort="low",
                approvalPolicy="never",
            )
            thread = codex_inst.start_thread(thread_options)
        except Exception as e:  # noqa: BLE001
            print(f"  setup failed: {type(e).__name__}: {e}")
            return False

        controller = oc.AbortController()  # type: ignore[attr-defined]
        print(f"  AbortController.signal type: {type(controller.signal).__name__}")

        try:
            text_input_cls = oc.TextInput  # type: ignore[attr-defined]
            # Same input-list convention as Test A.
            input_list = [
                text_input_cls(
                    type="text",
                    text=(
                        "Count slowly from 1 to 50. Use a full sentence per "
                        "number with explanation. Do not stop early."
                    ),
                )
            ]
            turn_options = oc.TurnOptions(  # type: ignore[attr-defined]
                signal=controller.signal,
            )
            # async — must await (same as Test A).
            streamed = await thread.run_streamed(input_list, turn_options)
            print("  signal wired via TurnOptions ✓")
        except Exception as e:  # noqa: BLE001
            print(f"  run_streamed setup failed: {type(e).__name__}: {e}")
            return False

        event_count = 0

        async def _aiter(obj: object):
            if hasattr(obj, "events"):
                async for ev in obj.events:  # type: ignore[attr-defined]
                    yield ev
            else:
                async for ev in obj:  # type: ignore[misc]
                    yield ev

        async def consume() -> None:
            nonlocal event_count
            try:
                async for _ in _aiter(streamed):
                    event_count += 1
            except asyncio.CancelledError:
                print(f"  consume() caught CancelledError after {event_count} events")
                raise
            except Exception as e:  # noqa: BLE001  AbortError etc.
                print(f"  consume() caught {type(e).__name__} after "
                      f"{event_count} events: {e}")
                raise

        task = asyncio.create_task(consume())
        await asyncio.sleep(TEST_B_RUN_BEFORE_CANCEL_S)

        cancel_at = time.time()
        print(f"  calling controller.abort() after {TEST_B_RUN_BEFORE_CANCEL_S:.1f}s...")
        controller.abort()

        try:
            await asyncio.wait_for(task, timeout=TEST_B_MAX_UNBLOCK_S + 5)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass
        except Exception as e:  # noqa: BLE001
            print(f"  await task raised {type(e).__name__}: {e}")
        dt = time.time() - cancel_at
        print(f"  awaiter released {dt:.2f}s after abort()")
        print(f"  events received before abort: {event_count}")
        print()
        # Guard against false PASS: if consume() exited before any
        # events arrived, the await chain bailed on a setup error
        # (e.g. unawaited coroutine) and the abort path was never
        # actually exercised. A real PASS needs both: events flowed
        # AND the abort released the awaiter under threshold.
        if event_count == 0:
            print("  RESULT: FAIL — consume() bailed before any events; "
                  "abort path never exercised")
            return False
        if dt < TEST_B_MAX_UNBLOCK_S:
            print(f"  RESULT: PASS — released in under "
                  f"{TEST_B_MAX_UNBLOCK_S:.0f}s via AbortController")
            return True
        else:
            print(f"  RESULT: FAIL — took {dt:.1f}s "
                  f"(threshold {TEST_B_MAX_UNBLOCK_S:.0f}s)")
            return False


# ---------------- main ----------------------------------------------


async def main() -> None:
    print(f"  Python:       {sys.version.split()[0]}")
    print(f"  codex CLI:    {shutil.which('codex') or '(not on PATH)'}")
    auth = Path.home() / ".codex" / "auth.json"
    print(f"  auth.json:    {'present' if auth.exists() else 'MISSING — run `codex login`'}")
    # Env vars are case-sensitive on Unix; libraries that respect
    # proxies typically read both spellings, but show whichever is set.
    _proxy = (
        os.environ.get("HTTPS_PROXY")
        or os.environ.get("https_proxy")
        or "(unset)"
    )
    print(f"  HTTPS_PROXY:  {_proxy}")
    print()

    oc = _print_signatures()
    if oc is None:
        return
    print()

    a = await test_a_mcp_wiring(oc)
    print()
    b = await test_b_cancellation(oc)
    print()
    print("=" * 64)
    print(f"Summary: Section A {'PASS' if a else 'FAIL'} | "
          f"Section B {'PASS' if b else 'FAIL'}")
    print("=" * 64)


if __name__ == "__main__":
    asyncio.run(main())

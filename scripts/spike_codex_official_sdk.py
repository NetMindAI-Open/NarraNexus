"""
@file_name: spike_codex_official_sdk.py
@author: NarraNexus
@date: 2026-06-04
@description: Capability spike for OpenAI's OFFICIAL Codex Python SDK.

Tests the package published by OpenAI as ``openai-codex`` (import name
``openai_codex``), sourced from
``github.com/openai/codex/tree/main/sdk/python``. This is the
authoritative SDK — distinct from the third-party ``openai-codex-sdk``
package we accidentally targeted earlier in this session (see
``spike_codex_sdk_community_archived.py``).

What's different from the community SDK we explored before
--------------------------------------------------------------
* Install: ``pip install openai-codex``  (not ``openai-codex-sdk``)
* Import:  ``from openai_codex import Codex``
* Pattern: ``with Codex() as codex: thread = codex.thread_start()``
  → note ``thread_start()``, NOT ``start_thread()``.
* Prompts: ``thread.run("plain string")`` — no ``TextInput`` wrapper
* Login:   ``codex.login_chatgpt() / login_chatgpt_device_code() /
            login_api_key(key)`` are first-class methods.
* Both sync (``Codex``) AND async (``AsyncCodex``) clients exist.

Questions this spike must answer before we commit to a CodexSDKv2
-----------------------------------------------------------------
0. SIGNATURE DISCOVERY — what do ``Codex``, ``AsyncCodex``,
   ``Thread``, ``AsyncThread``, ``Codex.thread_start``,
   ``Thread.run_streamed`` actually accept? (Different package =
   different API surface from the community spike. Don't guess.)
1. MCP WIRING — does the official SDK expose ``mcp_servers`` as an
   API option (``thread_start(mcp_servers=...)`` or
   ``Codex(config=...)``), or do we still need to write
   ``$CODEX_HOME/config.toml`` ourselves like the CLI path? We KNOW
   the file path works because codex CLI reads it; question is
   whether the SDK gives us a cleaner option.
2. CANCELLATION — how does this SDK surface cancellation? Plain
   ``asyncio.CancelledError`` on the consume task? An
   AbortController-like primitive? A ``CancellationToken``? We test
   what's documented; if undocumented, ``task.cancel()`` is the
   Python-idiomatic fallback.

Prerequisites
-------------
* ``uv pip install --python .venv/bin/python3 openai-codex``
  (the OFFICIAL one — uninstall any prior ``openai-codex-sdk`` first
  to avoid namespace ambiguity).
* ``codex login`` previously completed (auth.json on disk).
* HTTPS_PROXY / HTTP_PROXY exported in current shell (CN/HK).
* For Section A only: ``bash run.sh`` running so the lark_module MCP
  server is reachable at ``http://localhost:7831/mcp``. The PROBE
  query asks the agent to call a tool, so a real MCP server has to
  be there to respond.

Usage
-----
    python scripts/spike_codex_official_sdk.py

Then paste the output back. Outcome matrix:
* MCP works AND cancellation works
    → migrate to CodexSDKv2 as planned (coexist with v1 via the
      ``agent_loop_driver`` registry).
* MCP requires $CODEX_HOME/config.toml route
    → CodexSDKv2 keeps writing config.toml (same as v1) but uses
      SDK for subprocess lifecycle. Migration still worthwhile.
* Cancellation needs custom plumbing
    → CodexSDKv2 wraps SDK in a CancellationToken adapter. More
      work but tractable.
* Both fail
    → unlikely; tells us the official SDK is too young. Defer.
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


# Probe parameters — change if your local NarraNexus uses different
# ports / model. lark_module's MCP is at 7831 per current
# module_runner.py port assignments.
MCP_PROBE_NAME = "lark_module"
MCP_PROBE_URL = "http://localhost:7831/mcp"
PROBE_MODEL = "gpt-5.4-mini"  # ChatGPT-OAuth-tier curated
TEST_A_TIMEOUT_S = 60.0
TEST_B_RUN_BEFORE_CANCEL_S = 3.0
TEST_B_MAX_UNBLOCK_S = 5.0


def _stage_auth(codex_home: Path) -> bool:
    """Copy the host ``codex login`` credential into a per-run
    CODEX_HOME — mirrors what our production CodexSDK wrapper does."""
    src = Path.home() / ".codex" / "auth.json"
    if not src.exists():
        return False
    shutil.copy(src, codex_home / "auth.json")
    return True


# ---------------- Section 0: API discovery --------------------------


def _resolve_sdk() -> object | None:
    """Import the official ``openai_codex`` package, or fail loudly.

    If the community ``openai_codex_sdk`` is still installed it will
    also import — we explicitly target the official name here so the
    test isn't accidentally polluted by leftover community-package
    state.
    """
    try:
        import openai_codex
    except ImportError as e:
        print(f"  openai_codex: NOT importable ({e})")
        print(f"  Install the OFFICIAL SDK first:")
        print(f"    uv pip uninstall openai-codex-sdk   # remove community fork if present")
        print(f"    uv pip install --python .venv/bin/python3 openai-codex")
        return None
    return openai_codex


def _print_signatures(oc: object) -> None:
    print("=" * 64)
    print("Section 0: official openai_codex SDK signature discovery")
    print("=" * 64)
    print(f"  package version: {getattr(oc, '__version__', '(no __version__)')}")
    exports = [x for x in dir(oc) if not x.startswith("_")]
    print(f"  top-level exports ({len(exports)}):")
    for chunk_start in range(0, len(exports), 6):
        print("    " + ", ".join(exports[chunk_start:chunk_start + 6]))
    print()

    def _sig(label: str, target: object) -> None:
        if target is None:
            print(f"  {label}: <not exported>")
            return
        try:
            print(f"  {label}{inspect.signature(target)}")
        except (TypeError, ValueError) as e:
            print(f"  {label}: <not introspectable: {e}>")

    # Sync client
    Codex_cls = getattr(oc, "Codex", None)
    _sig("Codex.__init__", getattr(Codex_cls, "__init__", None) if Codex_cls else None)
    _sig("Codex.thread_start", getattr(Codex_cls, "thread_start", None) if Codex_cls else None)
    _sig("Codex.login_chatgpt", getattr(Codex_cls, "login_chatgpt", None) if Codex_cls else None)
    _sig("Codex.login_api_key", getattr(Codex_cls, "login_api_key", None) if Codex_cls else None)

    Thread_cls = getattr(oc, "Thread", None)
    _sig("Thread.run", getattr(Thread_cls, "run", None) if Thread_cls else None)
    _sig("Thread.run_streamed", getattr(Thread_cls, "run_streamed", None) if Thread_cls else None)

    # Async client (preferred for our async backend)
    print()
    print("  --- async surface ---")
    AsyncCodex_cls = getattr(oc, "AsyncCodex", None)
    _sig("AsyncCodex.__init__", getattr(AsyncCodex_cls, "__init__", None) if AsyncCodex_cls else None)
    _sig("AsyncCodex.thread_start", getattr(AsyncCodex_cls, "thread_start", None) if AsyncCodex_cls else None)

    AsyncThread_cls = getattr(oc, "AsyncThread", None)
    _sig("AsyncThread.run", getattr(AsyncThread_cls, "run", None) if AsyncThread_cls else None)
    _sig("AsyncThread.run_streamed", getattr(AsyncThread_cls, "run_streamed", None) if AsyncThread_cls else None)

    # Config / options types — the spike for the community SDK
    # showed a ``CodexConfig`` and ``ThreadOptions`` shape with
    # camelCase fields. The official SDK may differ.
    print()
    print("  --- config/options types ---")
    for name in ("CodexConfig", "ThreadOptions", "TurnOptions", "Sandbox",
                 "ApprovalMode", "Input", "InputItem", "TextInput"):
        symbol = getattr(oc, name, None)
        if symbol is None:
            print(f"  {name}: <not exported>")
            continue
        if hasattr(symbol, "__args__"):  # typing alias (Literal)
            print(f"  {name}: Literal{list(symbol.__args__)}")
        elif inspect.isclass(symbol):
            try:
                print(f"  {name}{inspect.signature(symbol)}")
            except (TypeError, ValueError):
                print(f"  {name}: <class, not introspectable>")
        else:
            print(f"  {name}: {type(symbol).__name__} = {symbol!r}")

    # Cancellation primitives — what does this SDK expose?
    print()
    print("  --- cancellation primitives ---")
    for name in ("AbortController", "AbortSignal", "AbortError",
                 "CancellationToken", "Cancelled"):
        symbol = getattr(oc, name, None)
        print(f"  {name}: {'present' if symbol is not None else '<not exported>'}")

    print()


# ---------------- Section A: MCP wiring -----------------------------


async def _aiter(streamed: object):
    """Defensive event iterator — tries common shapes."""
    if hasattr(streamed, "events"):
        async for ev in streamed.events:  # type: ignore[attr-defined]
            yield ev
    elif hasattr(streamed, "__aiter__"):
        async for ev in streamed:  # type: ignore[misc]
            yield ev
    else:
        raise RuntimeError(f"Cannot iterate {type(streamed).__name__}")


async def test_a_mcp_wiring(oc: object) -> bool:
    """Pre-write ``$CODEX_HOME/config.toml`` with mcp_servers (the
    way our production wrapper does), then drive the SDK and check
    for an MCP tool call in the event stream.

    This validates the SAFE path: SDK respects $CODEX_HOME just like
    raw codex CLI does. A future spike round can test whether the
    SDK has a direct ``mcp_servers=...`` API to skip the file step.
    """
    print("=" * 64)
    print("Section A: MCP via $CODEX_HOME/config.toml + official SDK")
    print("=" * 64)

    AsyncCodex_cls = getattr(oc, "AsyncCodex", None)
    Codex_cls = getattr(oc, "Codex", None)
    if AsyncCodex_cls is None and Codex_cls is None:
        print("  ERROR: neither Codex nor AsyncCodex exported. SDK shape unexpected.")
        return False

    with tempfile.TemporaryDirectory(prefix="spike_off_codex_a_") as home_str:
        codex_home = Path(home_str)
        instructions = codex_home / "instructions.md"
        instructions.write_text(
            "You are a probe. You have MCP tools available from the "
            f"'{MCP_PROBE_NAME}' server. When asked what MCP tools you "
            "have, CALL one of them (any will do) to confirm. Do NOT "
            "use Bash; do NOT speculate from memory.\n"
        )
        # config.toml — mcp + reasoning_summary + sandbox
        # (matches what _codex_config_toml_builder produces in prod)
        (codex_home / "config.toml").write_text(
            f'model_instructions_file = "{instructions}"\n'
            f'sandbox_mode = "danger-full-access"\n'
            f'model_reasoning_summary = "detailed"\n'
            f"\n"
            f"[mcp_servers.{MCP_PROBE_NAME}]\n"
            f'url = "{MCP_PROBE_URL}"\n'
        )
        if not _stage_auth(codex_home):
            print("  WARN: ~/.codex/auth.json missing — auth will likely fail")

        os.environ["CODEX_HOME"] = str(codex_home)
        print(f"  CODEX_HOME = {codex_home}")
        print(f"  config.toml has [mcp_servers.{MCP_PROBE_NAME}] url={MCP_PROBE_URL}")
        print()

        # Prefer async client when present.
        use_async = AsyncCodex_cls is not None
        print(f"  using {'AsyncCodex' if use_async else 'Codex (sync)'}")

        try:
            if use_async:
                codex = AsyncCodex_cls()  # type: ignore[misc]
                # Try the most likely thread_start signatures in order.
                thread = None
                for label, build in [
                    ("thread_start()", lambda: codex.thread_start()),
                    ("thread_start(working_directory=...)",
                     lambda: codex.thread_start(working_directory=str(codex_home))),
                    ("thread_start(working_directory=..., skip_git_repo_check=True)",
                     lambda: codex.thread_start(
                         working_directory=str(codex_home),
                         skip_git_repo_check=True,
                     )),
                ]:
                    try:
                        candidate = build()
                        # thread_start may be async
                        if inspect.iscoroutine(candidate):
                            thread = await candidate
                        else:
                            thread = candidate
                        print(f"  thread_start OK via: {label}")
                        break
                    except TypeError as e:
                        print(f"  rejected {label}: {e}")
                    except Exception as e:  # noqa: BLE001
                        print(f"  errored {label}: {type(e).__name__}: {e}")
                if thread is None:
                    print("  RESULT: ERROR — no thread_start signature accepted")
                    return False

                prompt = "What MCP tools do you have? Call one to confirm."
                streamed_call = thread.run_streamed(prompt)
                streamed = await streamed_call if inspect.iscoroutine(streamed_call) else streamed_call
            else:
                # Sync fallback — wrap in to_thread.
                def _run_sync():
                    with Codex_cls() as codex:  # type: ignore[misc]
                        try:
                            thread = codex.thread_start(working_directory=str(codex_home),
                                                       skip_git_repo_check=True)
                        except TypeError:
                            thread = codex.thread_start()
                        return list(thread.run_streamed(
                            "What MCP tools do you have? Call one to confirm."
                        ))
                events = await asyncio.to_thread(_run_sync)
                async def _gen():
                    for e in events:
                        yield e
                streamed = _gen()

            mcp_seen = False
            t0 = time.time()
            async for event in _aiter(streamed):
                dt = time.time() - t0
                cls = type(event).__name__
                item = getattr(event, "item", None)
                item_cls = type(item).__name__ if item is not None else "—"
                print(f"  [{dt:5.1f}s] {cls:30s} item={item_cls}")
                if "McpToolCall" in item_cls or "mcp" in cls.lower():
                    mcp_seen = True
                if dt > TEST_A_TIMEOUT_S:
                    print(f"  [TIMEOUT after {TEST_A_TIMEOUT_S:.0f}s]")
                    break
        except Exception as e:  # noqa: BLE001
            print(f"  ERROR during run: {type(e).__name__}: {e}")
            return False

        print()
        if mcp_seen:
            print("  RESULT: PASS — McpToolCallItem observed via $CODEX_HOME/config.toml + official SDK")
            return True
        print("  RESULT: FAIL/AMBIGUOUS — no MCP item in stream")
        return False


# ---------------- Section B: cancellation ---------------------------


async def test_b_cancellation(oc: object) -> bool:
    """Spawn a long counting turn, ``task.cancel()`` it after 3s,
    measure unblock latency. If the SDK exposes an explicit
    AbortController-style mechanism that's faster than task.cancel,
    that's a bonus we discover in Section 0.
    """
    print("=" * 64)
    print("Section B: cancellation via task.cancel()")
    print("=" * 64)

    AsyncCodex_cls = getattr(oc, "AsyncCodex", None)
    if AsyncCodex_cls is None:
        print("  AsyncCodex not exported — can't easily test async cancel here.")
        print("  Skipping. If only sync Codex is available we'd need to wrap")
        print("  it in asyncio.to_thread + manual cancellation plumbing for v2.")
        return False

    with tempfile.TemporaryDirectory(prefix="spike_off_codex_b_") as home_str:
        codex_home = Path(home_str)
        (codex_home / "config.toml").write_text(
            'sandbox_mode = "danger-full-access"\n'
            'model_reasoning_summary = "detailed"\n'
        )
        _stage_auth(codex_home)
        os.environ["CODEX_HOME"] = str(codex_home)

        try:
            codex = AsyncCodex_cls()  # type: ignore[misc]
            try:
                thread_call = codex.thread_start(
                    working_directory=str(codex_home),
                    skip_git_repo_check=True,
                )
                thread = await thread_call if inspect.iscoroutine(thread_call) else thread_call
            except TypeError:
                thread_call = codex.thread_start()
                thread = await thread_call if inspect.iscoroutine(thread_call) else thread_call

            streamed_call = thread.run_streamed(
                "Count slowly from 1 to 50. Use a full sentence per number "
                "with explanation. Do not stop early."
            )
            streamed = await streamed_call if inspect.iscoroutine(streamed_call) else streamed_call
        except Exception as e:  # noqa: BLE001
            print(f"  setup failed: {type(e).__name__}: {e}")
            return False

        event_count = 0

        async def consume() -> None:
            nonlocal event_count
            try:
                async for _ in _aiter(streamed):
                    event_count += 1
            except asyncio.CancelledError:
                print(f"  consume() caught CancelledError after {event_count} events")
                raise
            except Exception as e:  # noqa: BLE001
                print(f"  consume() caught {type(e).__name__} after "
                      f"{event_count} events: {e}")
                raise

        task = asyncio.create_task(consume())
        await asyncio.sleep(TEST_B_RUN_BEFORE_CANCEL_S)
        cancel_at = time.time()
        print(f"  cancelling task after {TEST_B_RUN_BEFORE_CANCEL_S:.1f}s...")
        task.cancel()
        try:
            await asyncio.wait_for(task, timeout=TEST_B_MAX_UNBLOCK_S + 5)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass
        except Exception as e:  # noqa: BLE001
            print(f"  await task raised {type(e).__name__}: {e}")
        dt = time.time() - cancel_at
        print(f"  awaiter released {dt:.2f}s after cancel()")
        print(f"  events received before cancel: {event_count}")
        print()
        if event_count == 0:
            print("  RESULT: FAIL — consume() bailed before any events; "
                  "abort path never exercised")
            return False
        if dt < TEST_B_MAX_UNBLOCK_S:
            print(f"  RESULT: PASS — released in under "
                  f"{TEST_B_MAX_UNBLOCK_S:.0f}s via task.cancel()")
            return True
        print(f"  RESULT: FAIL — took {dt:.1f}s "
              f"(threshold {TEST_B_MAX_UNBLOCK_S:.0f}s)")
        return False


# ---------------- main ----------------------------------------------


async def main() -> None:
    print(f"  Python:       {sys.version.split()[0]}")
    print(f"  codex CLI:    {shutil.which('codex') or '(not on PATH)'}")
    print(f"  auth.json:    {'present' if (Path.home() / '.codex' / 'auth.json').exists() else 'MISSING'}")
    proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy") or "(unset)"
    print(f"  HTTPS_PROXY:  {proxy}")
    print()

    oc = _resolve_sdk()
    if oc is None:
        return
    _print_signatures(oc)
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

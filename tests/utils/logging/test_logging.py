"""
@file_name: test_logging.py
@author: Bin Liang
@date: 2026-04-28
@description: Tests for xyz_agent_context.utils.logging public API.

Covers the four exported names — setup_logging, bind_event, timed,
redact — plus the InterceptHandler bridge. Each test isolates loguru
state via a fixture that resets handlers and the module-level
initialization cache so cases do not leak handlers between runs.
"""
from __future__ import annotations

import asyncio
import json
import logging as stdlib_logging
import os
from pathlib import Path
from typing import Any

import pytest
from loguru import logger

from xyz_agent_context.utils.logging import (
    bind_event,
    redact,
    setup_logging,
    timed,
)
from xyz_agent_context.utils.logging._setup import _reset_for_tests


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_logger() -> Any:
    """Reset loguru + setup cache between tests so handlers don't stack."""
    logger.remove()
    # Drop any process-default extra previously set via
    # logger.configure(extra=...). Otherwise tests that bind/unbind
    # event_id will see the default placeholder leftover from earlier
    # setup_logging calls and false-fail on "key not in extra" asserts.
    logger.configure(extra={})
    _reset_for_tests()
    yield
    logger.remove()
    logger.configure(extra={})
    _reset_for_tests()


@pytest.fixture
def captured() -> list[dict[str, Any]]:
    """Attach a list sink that captures the loguru record dict per call."""
    sink: list[dict[str, Any]] = []

    def _grab(message: Any) -> None:
        sink.append({
            "level": message.record["level"].name,
            "message": message.record["message"],
            "extra": dict(message.record["extra"]),
            "exception": message.record["exception"],
        })

    handler_id = logger.add(_grab, level="TRACE", format="{message}")
    yield sink
    logger.remove(handler_id)


# ---------------------------------------------------------------------------
# redact
# ---------------------------------------------------------------------------

class TestRedact:
    def test_dict_sensitive_key_masked(self) -> None:
        out = redact({"password": "hunter2", "user": "alice"})
        assert out == {"password": "***", "user": "alice"}

    def test_case_insensitive_key(self) -> None:
        out = redact({"API_KEY": "abc", "Authorization": "Bearer xyz"})
        assert out == {"API_KEY": "***", "Authorization": "***"}

    def test_nested_dict_recurses(self) -> None:
        out = redact({"outer": {"token": "t", "data": "ok"}})
        assert out == {"outer": {"token": "***", "data": "ok"}}

    def test_list_recurses(self) -> None:
        out = redact([{"token": "t"}, {"x": 1}])
        assert out == [{"token": "***"}, {"x": 1}]

    def test_tuple_preserves_type(self) -> None:
        out = redact(({"token": "t"},))
        assert isinstance(out, tuple)
        assert out == ({"token": "***"},)

    def test_jwt_truncated(self) -> None:
        jwt = "eyJhbGciOi.eyJzdWIiOiI.SflKxwRJSMeKKF"
        assert redact(jwt) == "eyJhbGci..."

    def test_short_dotted_string_not_jwt(self) -> None:
        # "a.b.c" is not a JWT — too short, should pass through.
        assert redact("a.b.c") == "a.b.c"

    def test_non_sensitive_value_unchanged(self) -> None:
        assert redact(42) == 42
        assert redact(None) is None
        assert redact("plain text") == "plain text"

    def test_does_not_mutate_input(self) -> None:
        src = {"token": "t", "user": "alice"}
        redact(src)
        assert src == {"token": "t", "user": "alice"}


# ---------------------------------------------------------------------------
# bind_event
# ---------------------------------------------------------------------------

class TestBindEvent:
    def test_extra_visible_inside_block(self, captured: list[dict[str, Any]]) -> None:
        with bind_event(event_id="evt_1", run_id="run_a"):
            logger.info("hello")
        assert len(captured) == 1
        assert captured[0]["extra"]["event_id"] == "evt_1"
        assert captured[0]["extra"]["run_id"] == "run_a"

    def test_extra_cleared_after_block(self, captured: list[dict[str, Any]]) -> None:
        with bind_event(event_id="evt_1"):
            pass
        logger.info("after")
        assert "event_id" not in captured[0]["extra"]

    def test_nested_inner_overrides_outer(self, captured: list[dict[str, Any]]) -> None:
        with bind_event(event_id="outer", run_id="r1"):
            with bind_event(event_id="inner"):
                logger.info("nested")
        assert captured[0]["extra"]["event_id"] == "inner"
        # outer key not in inner scope must still be visible
        assert captured[0]["extra"]["run_id"] == "r1"

    def test_outer_restored_after_inner_exits(
        self, captured: list[dict[str, Any]]
    ) -> None:
        with bind_event(event_id="outer"):
            with bind_event(event_id="inner"):
                pass
            logger.info("back to outer")
        assert captured[0]["extra"]["event_id"] == "outer"


# ---------------------------------------------------------------------------
# timed
# ---------------------------------------------------------------------------

class TestTimed:
    def test_context_manager_emits_ok(
        self, captured: list[dict[str, Any]]
    ) -> None:
        with timed("op.fast"):
            pass
        assert len(captured) == 1
        assert captured[0]["level"] == "INFO"
        assert "[TIMED] op.fast ok elapsed_ms=" in captured[0]["message"]

    def test_decorator_sync(self, captured: list[dict[str, Any]]) -> None:
        @timed("sync.fn")
        def f(x: int) -> int:
            return x * 2

        assert f(3) == 6
        assert any("sync.fn ok" in c["message"] for c in captured)

    def test_decorator_async(self, captured: list[dict[str, Any]]) -> None:
        @timed("async.fn")
        async def f(x: int) -> int:
            await asyncio.sleep(0)
            return x + 1

        result = asyncio.run(f(10))
        assert result == 11
        assert any("async.fn ok" in c["message"] for c in captured)

    def test_decorator_async_generator(
        self, captured: list[dict[str, Any]]
    ) -> None:
        @timed("asyncgen.fn")
        async def f(n: int):
            for i in range(n):
                await asyncio.sleep(0)
                yield i

        async def consume() -> list[int]:
            return [v async for v in f(3)]

        result = asyncio.run(consume())
        assert result == [0, 1, 2]
        assert any("asyncgen.fn ok" in c["message"] for c in captured)

    def test_decorator_sync_generator(
        self, captured: list[dict[str, Any]]
    ) -> None:
        @timed("gen.fn")
        def f(n: int):
            for i in range(n):
                yield i

        assert list(f(3)) == [0, 1, 2]
        assert any("gen.fn ok" in c["message"] for c in captured)

    def test_exception_propagates_and_logs(
        self, captured: list[dict[str, Any]]
    ) -> None:
        with pytest.raises(RuntimeError, match="boom"):
            with timed("op.fail"):
                raise RuntimeError("boom")
        # one ERROR-level [TIMED] failed line with exception attached
        failed = [c for c in captured if "failed" in c["message"]]
        assert len(failed) == 1
        assert failed[0]["level"] == "ERROR"
        assert failed[0]["exception"] is not None

    def test_slow_threshold_escalates_to_warning(
        self, captured: list[dict[str, Any]]
    ) -> None:
        # threshold 0 → every call counts as slow
        with timed("op.slow", slow_threshold_ms=0):
            pass
        assert captured[0]["level"] == "WARNING"

    def test_concurrent_calls_do_not_share_clock(
        self, captured: list[dict[str, Any]]
    ) -> None:
        @timed("concurrent.fn")
        async def f(delay: float) -> None:
            await asyncio.sleep(delay)

        async def runner() -> None:
            await asyncio.gather(f(0.0), f(0.0), f(0.0))

        asyncio.run(runner())
        ok_lines = [c for c in captured if "concurrent.fn ok" in c["message"]]
        assert len(ok_lines) == 3


# ---------------------------------------------------------------------------
# setup_logging
# ---------------------------------------------------------------------------

class TestSetupLogging:
    def test_creates_log_dir(self, tmp_path: Path) -> None:
        result = setup_logging("svc_a", log_dir=tmp_path)
        assert result == tmp_path / "svc_a"
        assert (tmp_path / "svc_a").is_dir()

    def test_idempotent_for_same_service(self, tmp_path: Path) -> None:
        a = setup_logging("svc_b", log_dir=tmp_path)
        b = setup_logging("svc_b", log_dir=tmp_path)
        assert a == b
        # two services in the same process = two distinct dirs, both
        # cached
        c = setup_logging("svc_c", log_dir=tmp_path)
        assert c != a

    def test_writes_to_file(self, tmp_path: Path) -> None:
        setup_logging("svc_file", log_dir=tmp_path, level="INFO")
        logger.info("file probe message")
        # Force pending records to flush before reading
        logger.complete()
        files = list((tmp_path / "svc_file").glob("svc_file_*.log"))
        assert files, "expected a daily log file"
        content = files[0].read_text(encoding="utf-8")
        assert "file probe message" in content

    def test_text_format_has_trace_placeholders(
        self, tmp_path: Path
    ) -> None:
        setup_logging("svc_fmt", log_dir=tmp_path)
        logger.info("hello")
        logger.complete()
        files = list((tmp_path / "svc_fmt").glob("svc_fmt_*.log"))
        line = files[0].read_text(encoding="utf-8")
        # When no bind_event is in scope, both placeholders render
        assert "--------" in line  # run_id placeholder
        assert "--------------" in line  # event_id placeholder

    def test_bind_event_visible_in_file(self, tmp_path: Path) -> None:
        setup_logging("svc_bind", log_dir=tmp_path)
        with bind_event(event_id="evt_xyz", run_id="run_abc"):
            logger.info("bound message")
        logger.complete()
        files = list((tmp_path / "svc_bind").glob("svc_bind_*.log"))
        line = files[0].read_text(encoding="utf-8")
        assert "evt_xyz" in line
        assert "run_abc" in line
        assert "bound message" in line

    def test_json_format(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Use env to exercise the env path, not the function arg
        monkeypatch.setenv("NEXUS_LOG_FORMAT", "json")
        setup_logging("svc_json", log_dir=tmp_path)
        with bind_event(event_id="evt_json"):
            logger.info("json probe")
        logger.complete()
        files = list((tmp_path / "svc_json").glob("svc_json_*.log"))
        line = files[0].read_text(encoding="utf-8").splitlines()[-1]
        record = json.loads(line)
        # serialize=True emits {"text": ..., "record": {...}}
        assert "record" in record
        assert record["record"]["extra"]["event_id"] == "evt_json"

    def test_audit_level_registered(self, tmp_path: Path) -> None:
        setup_logging("svc_audit", log_dir=tmp_path)
        logger.log("AUDIT", "audit probe")
        logger.complete()
        files = list((tmp_path / "svc_audit").glob("svc_audit_*.log"))
        content = files[0].read_text(encoding="utf-8")
        assert "AUDIT" in content
        assert "audit probe" in content

    def test_env_log_dir(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("NEXUS_LOG_DIR", str(tmp_path))
        result = setup_logging("svc_env_dir")
        assert result == tmp_path / "svc_env_dir"


# ---------------------------------------------------------------------------
# stdlib bridge
# ---------------------------------------------------------------------------

class TestInterceptHandler:
    def test_stdlib_logger_routes_to_loguru(
        self, tmp_path: Path
    ) -> None:
        setup_logging("svc_intercept", log_dir=tmp_path)
        stdlib_logging.getLogger("custom_third_party").warning("from stdlib")
        logger.complete()
        files = list((tmp_path / "svc_intercept").glob("svc_intercept_*.log"))
        content = files[0].read_text(encoding="utf-8")
        assert "from stdlib" in content

    def test_noisy_logger_clamped_to_warning(
        self, tmp_path: Path
    ) -> None:
        setup_logging("svc_clamp", log_dir=tmp_path)
        noisy = stdlib_logging.getLogger("uvicorn.access")
        noisy.info("uvicorn-info-line")  # below WARNING — must not appear
        noisy.warning("uvicorn-warn-line")
        logger.complete()
        files = list((tmp_path / "svc_clamp").glob("svc_clamp_*.log"))
        content = files[0].read_text(encoding="utf-8")
        assert "uvicorn-info-line" not in content
        assert "uvicorn-warn-line" in content

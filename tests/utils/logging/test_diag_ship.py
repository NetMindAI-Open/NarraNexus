"""
@file_name: test_diag_ship.py
@author:
@date: 2026-08-10
@description: Network ship sink (observability push half) — env gating,
batch payload shape (gzip ndjson + envelope), drop-on-failure, backfill.
"""
from __future__ import annotations

import gzip
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from narranexus.platform.utils.logging import _ship


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch, tmp_path):
    for key in (
        "NEXUS_DIAG_SHIP",
        "NEXUS_DIAG_SHIP_URL",
        "NEXUS_DIAG_SHIP_TOKEN",
        "NEXUS_DIAG_ENV",
        "NEXUS_DIAG_DISCOVERY_URL",
        "MANYFOLD_RUNTIME_ID",
        "MANYFOLD_SYNC_WEBHOOK_URL",
        "NARRANEXUS_DEPLOYMENT_MODE",
        "DATABASE_URL",
        "DB_HOST",
        "NEXUS_DIAG_DEFAULT_SHIP",
        "NEXUS_DIAG_OPTOUT_FILE",
        "NARRA_SURFACE",
    ):
        monkeypatch.delenv(key, raising=False)
    # Isolate from any real opt-out marker on the developer machine.
    monkeypatch.setattr(_ship, "_OPTOUT_FILE", tmp_path / "telemetry_optout")


class _Recorder:
    def __init__(self, status: int = 200):
        self.requests: list[httpx.Request] = []
        self._status = status

    def transport(self) -> httpx.MockTransport:
        def _handle(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            return httpx.Response(self._status, json={"ok": True})

        return httpx.MockTransport(_handle)

    def batches(self) -> list[list[dict]]:
        out = []
        for req in self.requests:
            body = gzip.decompress(req.content).decode()
            out.append([json.loads(ln) for ln in body.splitlines()])
        return out


def _message(text="hello", level="INFO", run_id="run1"):
    return SimpleNamespace(
        record={
            "time": datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc),
            "level": SimpleNamespace(name=level),
            "extra": {"run_id": run_id, "event_id": "evt1"},
            "name": "mod",
            "function": "fn",
            "line": 7,
            "message": text,
        }
    )


def _config(**over):
    base = {
        "mode": "full",
        "url": "https://collect.example.com/v1/ingest",
        "token": "tok-1",
        "env": "manyfold-staging",
        "runtime_id": "rt_x",
    }
    base.update(over)
    return base  # url=None in overrides exercises the discovery path


class TestConsentGating:
    def test_default_is_meta_now_that_consent_basis_ships(self):
        """The default and its consent basis land TOGETHER: this PR
        ships the first-run disclosure + settings toggle, and flips
        the default from off in the same change. The level is META,
        not full: full ships INFO bodies verbatim, and production INFO
        includes entire user messages (agent_runtime logs
        input_content) — content the disclosure copy does not cover.
        full stays an explicit deployment knob."""
        config = _ship.ship_config()
        assert config is not None
        assert config["mode"] == "meta"
        assert config["url"] is None  # discovery path, resolved lazily

    def test_env_off_silences(self, monkeypatch):
        monkeypatch.setenv("NEXUS_DIAG_SHIP", "off")
        assert _ship.ship_config() is None

    def test_optout_file_silences_default(self):
        _ship._OPTOUT_FILE.write_text("")
        assert _ship.ship_config() is None

    def test_env_override_beats_optout_file(self, monkeypatch):
        # Explicit env is the dev/self-host knob and outranks the UI
        # marker in both directions.
        _ship._OPTOUT_FILE.write_text("")
        monkeypatch.setenv("NEXUS_DIAG_SHIP", "meta")
        config = _ship.ship_config()
        assert config is not None and config["mode"] == "meta"

    def test_consent_accessor_reports_deciding_layer(self, monkeypatch):
        """The settings UI needs to know WHICH layer decided the mode:
        only "optout"/"default" are user-controllable — an env override
        is the deployment's decision, not the user's."""
        assert _ship.telemetry_consent() == {"mode": "meta", "source": "default"}
        _ship._OPTOUT_FILE.write_text("")
        assert _ship.telemetry_consent() == {"mode": "off", "source": "optout"}
        monkeypatch.setenv("NEXUS_DIAG_SHIP", "meta")
        assert _ship.telemetry_consent() == {"mode": "meta", "source": "env"}

    def test_set_telemetry_optout_roundtrip(self):
        _ship.set_telemetry_optout(True)
        assert _ship._OPTOUT_FILE.exists()
        assert _ship.ship_config() is None
        _ship.set_telemetry_optout(False)
        assert not _ship._OPTOUT_FILE.exists()
        assert _ship.ship_config() is not None
        _ship.set_telemetry_optout(False)  # idempotent on a missing file

    def test_optout_path_env_override_resolves_per_call(self, monkeypatch, tmp_path):
        """NEXUS_DIAG_OPTOUT_FILE points every service at one shared
        mounted path — without it, a containerized self-host's opt-out
        written by the backend container silences only itself and dies
        on recreate. Resolution is per call, so no child interpreter is
        needed and the _OPTOUT_FILE-repointing fixtures keep working."""
        shared = tmp_path / "shared" / "optout"
        monkeypatch.setenv("NEXUS_DIAG_OPTOUT_FILE", str(shared))
        assert _ship._optout_file() == shared
        _ship.set_telemetry_optout(True)
        assert shared.exists()
        assert _ship.telemetry_consent()["source"] == "optout"
        monkeypatch.delenv("NEXUS_DIAG_OPTOUT_FILE")
        assert _ship._optout_file() == _ship._OPTOUT_FILE  # fixture path

    def test_managed_default_is_a_default_not_an_override(self, monkeypatch):
        """NEXUS_DIAG_DEFAULT_SHIP changes what applies when the user
        has expressed NOTHING — the opt-out marker still wins and the
        source stays "default" (toggle live). This is the layer run.sh
        uses for manyfold sandboxes: full by default, switch intact."""
        monkeypatch.setenv("NEXUS_DIAG_DEFAULT_SHIP", "full")
        assert _ship.telemetry_consent() == {"mode": "full", "source": "default"}
        _ship.set_telemetry_optout(True)
        assert _ship.telemetry_consent() == {"mode": "off", "source": "optout"}
        _ship.set_telemetry_optout(False)
        monkeypatch.setenv("NEXUS_DIAG_SHIP", "meta")
        assert _ship.telemetry_consent() == {"mode": "meta", "source": "env"}

    def test_managed_default_rejects_off_and_garbage(self, monkeypatch):
        # A deployment that wants silence sets the OVERRIDE
        # (NEXUS_DIAG_SHIP=off); "default off" would render a toggle
        # whose on-position is unreachable. Garbage falls through.
        monkeypatch.setenv("NEXUS_DIAG_DEFAULT_SHIP", "off")
        assert _ship.telemetry_consent() == {"mode": "meta", "source": "default"}
        monkeypatch.setenv("NEXUS_DIAG_DEFAULT_SHIP", "everything")
        assert _ship.telemetry_consent() == {"mode": "meta", "source": "default"}

    def test_package_reexports_consent_api(self):
        from narranexus.platform.utils import logging as pkg

        assert pkg.telemetry_consent() == {"mode": "meta", "source": "default"}
        pkg.set_telemetry_optout(True)
        assert _ship.telemetry_consent()["source"] == "optout"

    def test_optout_takes_effect_at_next_flush_without_restart(self, monkeypatch):
        """Consent withdrawal must not wait for a process restart: the
        sink is registered at startup, but _send re-checks consent —
        an opt-out written mid-run silences shipping within one flush
        interval. Re-enabling still waits for the next start (the sink
        was never registered), an asymmetry in privacy's favor."""
        rec = _Recorder()
        monkeypatch.setattr(_ship, "_transport_for_tests", rec.transport())
        sink = _ship.ShipSink("backend", _config())
        sink(_message("before optout"))
        sink.flush()
        assert len(rec.requests) == 1
        _ship.set_telemetry_optout(True)
        sink(_message("after optout"))
        sink.flush()
        assert len(rec.requests) == 1  # dropped at the door, not sent
        _ship.set_telemetry_optout(False)
        sink(_message("after optin"))
        sink.flush()
        assert len(rec.requests) == 2  # resumes without restart too

    def test_unknown_env_value_falls_to_default(self, monkeypatch):
        # A typo'd override neither silences nor forces a level — it
        # falls through the whole chain to the default (meta).
        monkeypatch.setenv("NEXUS_DIAG_SHIP", "everything")
        config = _ship.ship_config()
        assert config is not None and config["mode"] == "meta"

    def test_env_label_no_longer_sniffs_manyfold(self, monkeypatch):
        """Deployment detection moved to run.sh (which injects
        NEXUS_DIAG_ENV); the generic logging utility must not read
        another integration's env vars. Absent all labels it falls to
        OUR deployment contract (get_deployment_mode → "local" here),
        never to the manyfold URL."""
        monkeypatch.setenv("NEXUS_DIAG_SHIP", "full")
        monkeypatch.setenv(
            "MANYFOLD_SYNC_WEBHOOK_URL", "https://api-staging.manyfold.ai/x"
        )
        assert _ship.ship_config()["env"] == "local"

    def test_env_label_explicit_and_surface_fallback(self, monkeypatch):
        monkeypatch.setenv("NEXUS_DIAG_SHIP", "full")
        monkeypatch.setenv("NARRA_SURFACE", "local")
        assert _ship.ship_config()["env"] == "local"
        monkeypatch.setenv("NEXUS_DIAG_ENV", "canary")
        assert _ship.ship_config()["env"] == "canary"

    def test_env_label_falls_back_to_deployment_mode(self, monkeypatch):
        """Cloud stack containers are started by compose directly (no
        run.sh, no NARRA_SURFACE) — their label must come from the
        deployment-mode contract (NARRANEXUS_DEPLOYMENT_MODE, baked in
        the cloud image), or every cloud record would carry "unknown"
        and land in the collector's stranger bucket — the partition
        that drains FIRST."""
        monkeypatch.setenv("NEXUS_DIAG_SHIP", "full")
        monkeypatch.setenv("NARRANEXUS_DEPLOYMENT_MODE", "cloud")
        assert _ship.ship_config()["env"] == "cloud"

    def test_meta_level_is_audit_and_up(self):
        assert _ship.ship_sink_level("meta") == 25
        assert _ship.ship_sink_level("meta", "DEBUG") == 25

    def test_full_level_follows_file_sink(self):
        # "full ships what the file sink sees" — DEBUG turned on for an
        # incident must reach the collector, not just the local file.
        assert _ship.ship_sink_level("full", "DEBUG") == "DEBUG"
        assert _ship.ship_sink_level("full") == "INFO"


class TestShipSink:
    def test_batch_carries_envelope_and_auth(self, monkeypatch):
        rec = _Recorder()
        monkeypatch.setattr(_ship, "_transport_for_tests", rec.transport())
        sink = _ship.ShipSink("backend", _config())
        sink(_message("line one"))
        sink(_message("line two", level="WARNING"))
        sink.flush()

        assert len(rec.requests) == 1
        req = rec.requests[0]
        assert req.headers["authorization"] == "Bearer tok-1"
        assert req.headers["content-encoding"] == "gzip"
        (batch,) = rec.batches()
        assert [e["message"] for e in batch] == ["line one", "line two"]
        first = batch[0]
        assert first["env"] == "manyfold-staging"
        assert first["runtime_id"] == "rt_x"
        assert first["service"] == "backend"
        assert first["run_id"] == "run1"
        assert first["level"] == "INFO"

    def test_size_triggered_flush(self, monkeypatch):
        rec = _Recorder()
        monkeypatch.setattr(_ship, "_transport_for_tests", rec.transport())
        sink = _ship.ShipSink("backend", _config())
        for i in range(_ship._BATCH_MAX):
            sink(_message(f"m{i}"))
        assert len(rec.requests) == 1  # flushed at the cap without waiting
        assert len(rec.batches()[0]) == _ship._BATCH_MAX

    def test_send_failure_drops_batch_and_never_raises(self, monkeypatch):
        rec = _Recorder(status=503)
        monkeypatch.setattr(_ship, "_transport_for_tests", rec.transport())
        sink = _ship.ShipSink("backend", _config())
        sink(_message("doomed"))
        sink.flush()  # must not raise
        assert len(rec.requests) == 1
        sink(_message("next"))
        sink.flush()
        # Buffer was not poisoned by the failure; next batch still sends.
        assert len(rec.requests) == 2

    def test_network_error_never_raises(self, monkeypatch):
        def _boom(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("down", request=request)

        monkeypatch.setattr(
            _ship, "_transport_for_tests", httpx.MockTransport(_boom)
        )
        sink = _ship.ShipSink("backend", _config())
        sink(_message("x"))
        sink.flush()  # silence is the contract

    def test_backfill_ships_tail_flagged(self, monkeypatch, tmp_path):
        rec = _Recorder()
        monkeypatch.setattr(_ship, "_transport_for_tests", rec.transport())
        log = tmp_path / "backend_20260810.log"
        log.write_text("\n".join(f"old line {i}" for i in range(300)))
        sink = _ship.ShipSink("backend", _config())
        sink.backfill_from(log)
        (batch,) = rec.batches()
        assert len(batch) == _ship._BACKFILL_LINES  # tail only
        assert batch[0]["backfill"] is True
        assert batch[-1]["message"] == "old line 299"

    def test_backfill_missing_file_is_noop(self, monkeypatch, tmp_path):
        rec = _Recorder()
        monkeypatch.setattr(_ship, "_transport_for_tests", rec.transport())
        sink = _ship.ShipSink("backend", _config())
        sink.backfill_from(tmp_path / "ghost.log")
        assert rec.requests == []


class TestAuditMirrorLine:
    @pytest.mark.asyncio
    async def test_audit_append_mirrors_one_log_line(self):
        """Every audit row emits one AUDIT-level log line so lifecycle
        events ride the push sink (the sink ships log records, not DB
        rows). Falls back to INFO when the custom level is absent."""
        from loguru import logger

        from narranexus.platform.repository.channel_trigger_audit_repository import (
            ChannelTriggerAuditRepository,
        )

        class _Db:
            async def insert(self, table, row):
                pass

        captured: list = []
        handler_id = logger.add(
            lambda m: captured.append(m.record), level=0, format="{message}"
        )
        try:
            repo = ChannelTriggerAuditRepository("wechat", _Db())
            await repo.append(
                "managed_ingress_denied",
                agent_id="a1",
                message_id="m1",
                details={"reason": "nope"},
            )
        finally:
            logger.remove(handler_id)
        lines = [r for r in captured if "[audit]" in r["message"]]
        assert len(lines) == 1
        msg = lines[0]["message"]
        assert "channel=wechat" in msg
        assert "event=managed_ingress_denied" in msg
        assert "agent=a1" in msg
        assert lines[0]["level"].name in ("AUDIT", "INFO")


class TestCircuitBreaker:
    def test_opens_after_threshold_and_drops_at_the_door(self, monkeypatch):
        """A dead collector must not turn the sink into a memory leak:
        after the threshold the breaker drops records without buffering
        or send attempts."""
        def _boom(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("dead", request=request)

        transport = httpx.MockTransport(_boom)
        calls = {"n": 0}

        class _CountingTransport(httpx.BaseTransport):
            def handle_request(self, request):
                calls["n"] += 1
                return transport.handle_request(request)

        monkeypatch.setattr(_ship, "_transport_for_tests", _CountingTransport())
        sink = _ship.ShipSink("backend", _config())
        for _ in range(_ship._BREAKER_THRESHOLD):
            sink(_message("x"))
            sink.flush()
        assert calls["n"] == _ship._BREAKER_THRESHOLD
        assert sink._cooldown_until > 0  # breaker open

        # While open: records dropped at the door, no buffering, no sends.
        sink(_message("dropped"))
        sink.flush()
        assert calls["n"] == _ship._BREAKER_THRESHOLD
        assert sink._buf == []
        assert sink._dropped_in_cooldown >= 1

    def test_half_open_after_cooldown_resumes(self, monkeypatch):
        rec = _Recorder()
        monkeypatch.setattr(_ship, "_transport_for_tests", rec.transport())
        sink = _ship.ShipSink("backend", _config())
        sink._cooldown_until = 1.0  # already elapsed on the monotonic clock
        sink._dropped_in_cooldown = 3
        sink(_message("after cooldown"))
        sink.flush()
        assert len(rec.requests) == 1
        assert sink._cooldown_until == 0.0


class TestBreakerHalfOpen:
    def test_probe_failure_reopens_immediately(self, monkeypatch):
        """Half-open means ONE failed probe re-opens — not re-earning the
        threshold (the doc-only version wasted 5 batches × 2s timeout per
        cooldown cycle against a long-dead collector)."""
        calls = {"n": 0}

        class _Counting(httpx.BaseTransport):
            def handle_request(self, request):
                calls["n"] += 1
                raise httpx.ConnectError("dead", request=request)

        monkeypatch.setattr(_ship, "_transport_for_tests", _Counting())
        sink = _ship.ShipSink("backend", _config())
        sink._cooldown_until = 1.0  # elapsed → next record enters half-open
        sink(_message("probe"))
        sink.flush()
        assert calls["n"] == 1
        assert sink._cooldown_until > 1.0  # re-opened by ONE failure
        assert sink._half_open is False

    def test_probe_success_closes_fully(self, monkeypatch):
        rec = _Recorder()
        monkeypatch.setattr(_ship, "_transport_for_tests", rec.transport())
        sink = _ship.ShipSink("backend", _config())
        sink._cooldown_until = 1.0
        sink(_message("probe"))
        sink.flush()
        assert len(rec.requests) == 1
        assert sink._cooldown_until == 0.0
        assert sink._half_open is False


class TestPermanentRejection:
    def test_4xx_drops_without_breaker_accounting(self, monkeypatch):
        """413 means 'this batch is unacceptable', not 'collector down' —
        it must never open the breaker."""
        rec = _Recorder(status=413)
        monkeypatch.setattr(_ship, "_transport_for_tests", rec.transport())
        sink = _ship.ShipSink("backend", _config())
        for _ in range(_ship._BREAKER_THRESHOLD * 2):
            sink(_message("big"))
            sink.flush()
        assert sink._fail_streak == 0
        assert sink._cooldown_until == 0.0  # breaker never opened
        assert len(rec.requests) == _ship._BREAKER_THRESHOLD * 2


class TestByteBatching:
    def test_flushes_on_serialized_bytes(self, monkeypatch):
        rec = _Recorder()
        monkeypatch.setattr(_ship, "_transport_for_tests", rec.transport())
        monkeypatch.setattr(_ship, "_BATCH_MAX_BYTES", 1000)
        sink = _ship.ShipSink("backend", _config())
        sink(_message("x" * 600))
        assert rec.requests == []  # under the byte cap, buffered
        sink(_message("y" * 600))
        assert len(rec.requests) == 1  # byte cap crossed → flushed early
        assert len(rec.batches()[0]) == 2


class TestAtexitSweep:
    def test_module_level_handler_flushes_live_sinks(self, monkeypatch):
        rec = _Recorder()
        monkeypatch.setattr(_ship, "_transport_for_tests", rec.transport())
        sink = _ship.ShipSink("backend", _config())
        sink(_message("tail line"))
        assert rec.requests == []
        _ship._flush_all_at_exit()
        assert len(rec.requests) == 1
        assert rec.batches()[0][0]["message"] == "tail line"

    def test_exit_sweep_drains_loguru_queue_before_flush(self, monkeypatch):
        """atexit is LIFO and this module's handler registers at import
        time — AFTER loguru registers its own remove — so it runs while
        records may still sit in loguru's enqueue worker. The handler
        must drain that queue FIRST (logger.complete()), then flush,
        then close; any other order sends the final batch minus its
        tail, with close() guaranteeing the leftovers never go out."""
        import loguru

        order: list = []
        monkeypatch.setattr(
            loguru.logger, "complete", lambda *a, **k: order.append("drain")
        )
        rec = _Recorder()
        monkeypatch.setattr(_ship, "_transport_for_tests", rec.transport())
        sink = _ship.ShipSink("backend", _config())
        monkeypatch.setattr(sink, "flush", lambda: order.append("flush"))
        monkeypatch.setattr(sink._client, "close", lambda: order.append("close"))
        _ship._flush_all_at_exit()
        assert order == ["drain", "flush", "close"]


class TestDiscovery:
    def _routing_transport(self, mapping, hits):
        def _handle(request: httpx.Request) -> httpx.Response:
            if request.method == "GET":
                hits.append(str(request.url))
                return httpx.Response(200, json={"ingest": mapping})
            hits.append("POST")
            return httpx.Response(200, json={"ok": True})

        return httpx.MockTransport(_handle)

    def test_resolves_by_env_label_and_caches(self, monkeypatch):
        hits: list = []
        mapping = {
            "default": "https://agent.narra.nexus/telemetry/v1/ingest",
            "staging": "https://dev-agent.narra.nexus/telemetry/v1/ingest",
        }
        monkeypatch.setattr(
            _ship, "_transport_for_tests", self._routing_transport(mapping, hits)
        )
        sink = _ship.ShipSink("backend", _config(url=None, env="staging"))
        sink(_message("a"))
        sink.flush()
        sink(_message("b"))
        sink.flush()
        # One discovery GET (TTL-cached), two ingest POSTs.
        gets = [h for h in hits if h != "POST"]
        assert len(gets) == 1
        assert hits.count("POST") == 2
        assert sink._resolved_url == (
            "https://dev-agent.narra.nexus/telemetry/v1/ingest"
        )

    @pytest.mark.parametrize(
        ("label", "expected"),
        [
            ("staging", "https://dev-agent.narra.nexus/telemetry/v1/ingest"),
            ("dev", "https://dev-agent.narra.nexus/telemetry/v1/ingest"),
            # absent from the map → silent fallback to "default"
            ("local", "https://agent.narra.nexus/telemetry/v1/ingest"),
        ],
    )
    def test_label_routing_follows_map_keys(self, monkeypatch, label, expected):
        """Resolution rule: mapping.get(label) or mapping.get("default").
        HONEST SCOPE: this guards the rule against an injected map; the
        real third side of the label contract — whether the live
        DIAG_COLLECT_CONFIG_JSON carries the right keys — is ops
        configuration code cannot reach. The example ops copy from is
        guarded by test_env_example_discovery_example_covers_own_host_labels."""
        hits: list = []
        mapping = {
            "default": "https://agent.narra.nexus/telemetry/v1/ingest",
            "staging": "https://dev-agent.narra.nexus/telemetry/v1/ingest",
            "dev": "https://dev-agent.narra.nexus/telemetry/v1/ingest",
        }
        monkeypatch.setattr(
            _ship, "_transport_for_tests", self._routing_transport(mapping, hits)
        )
        sink = _ship.ShipSink("backend", _config(url=None, env=label))
        sink(_message("x"))
        sink.flush()
        assert sink._resolved_url == expected

    def test_env_example_discovery_example_covers_own_host_labels(self):
        """Executable version of ".env.example configured as-is will
        not send dev traffic to prod": every label that needs its own
        receiving host (staging = manyfold sandboxes, dev = our dev
        EC2 stack — different sources) must appear in the example
        discovery document's ingest keys, alongside "default"."""
        text = (
            Path(__file__).resolve().parents[3] / ".env.example"
        ).read_text(encoding="utf-8")
        keys = set(
            re.findall(r'"(\w+)":"https://[^"]+/telemetry/v1/ingest"', text)
        )
        assert {"default", "staging", "dev", "sprite"} <= keys

    def test_unresolvable_discovery_drops_quietly(self, monkeypatch):
        def _dead(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("no dns", request=request)

        monkeypatch.setattr(
            _ship, "_transport_for_tests", httpx.MockTransport(_dead)
        )
        sink = _ship.ShipSink("backend", _config(url=None))
        sink(_message("x"))
        sink.flush()  # must not raise; batch dropped, breaker untouched
        assert sink._fail_streak == 0
        assert sink._cooldown_until == 0.0

    def test_env_url_override_skips_discovery(self, monkeypatch):
        rec = _Recorder()
        monkeypatch.setattr(_ship, "_transport_for_tests", rec.transport())
        sink = _ship.ShipSink("backend", _config())  # url set in _config
        sink(_message("x"))
        sink.flush()
        assert len(rec.requests) == 1  # straight POST, no GET
        assert rec.requests[0].method == "POST"


    def test_bad_shape_document_keeps_stale_url(self, monkeypatch):
        """Valid JSON that is not {"ingest": {...}} (e.g. a list) is a
        failed refresh, not a crash: the previously resolved URL
        survives (stale-if-error) and batches keep flowing on it."""
        good = {
            "ingest": {"default": "https://agent.narra.nexus/telemetry/v1/ingest"}
        }
        state: dict = {"doc": good}
        posts: list = []

        def _handle(request: httpx.Request) -> httpx.Response:
            if request.method == "GET":
                return httpx.Response(200, json=state["doc"])
            posts.append(request)
            return httpx.Response(200, json={"ok": True})

        monkeypatch.setattr(
            _ship, "_transport_for_tests", httpx.MockTransport(_handle)
        )
        sink = _ship.ShipSink("backend", _config(url=None))
        sink(_message("a"))
        sink.flush()
        assert sink._resolved_url == good["ingest"]["default"]
        state["doc"] = ["not", "a", "mapping"]  # next refresh serves garbage
        sink._url_expires = 0.0
        sink._discovery_next = 0.0
        sink(_message("b"))
        sink.flush()
        assert sink._resolved_url == good["ingest"]["default"]
        assert len(posts) == 2

    def test_html_answer_backs_off_a_full_ttl_not_60s(self, monkeypatch):
        """A 200 whose body is not a discovery document (e.g. an SPA
        serving index.html for every path) means "this deployment has
        no telemetry service" — a fleet of idle installs must not
        beacon the vendor every 60s for a service that is not there.
        Next probe waits a full TTL; network errors keep the short
        retry (a broken network is transient, a wrong endpoint isn't)."""
        def _spa(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text="<!doctype html><html>app</html>")

        monkeypatch.setattr(
            _ship, "_transport_for_tests", httpx.MockTransport(_spa)
        )
        sink = _ship.ShipSink("backend", _config(url=None))
        sink(_message("x"))
        sink.flush()
        assert sink._resolved_url is None
        assert (
            sink._discovery_next - time.monotonic()
            > _ship._DISCOVERY_RETRY_S * 10
        )

    def test_404_discovery_backs_off_a_full_ttl_not_60s(self, monkeypatch):
        """A 404 (the collector has no DIAG_COLLECT_CONFIG_JSON) is a
        DEFINITE "no discovery document here" — same class as the
        not-a-document 200: back off a full TTL, not the 60s transient
        cadence. This is exactly the hole staging->dev opened: redirect
        to dev-agent, dev collector unconfigured -> 404 -> every python
        process GETs it every 60s forever. 5xx / network stay short."""
        def _not_found(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404, json={"detail": "no discovery config"})

        monkeypatch.setattr(
            _ship, "_transport_for_tests", httpx.MockTransport(_not_found)
        )
        sink = _ship.ShipSink("backend", _config(url=None))
        sink(_message("x"))
        sink.flush()
        assert sink._resolved_url is None
        assert (
            sink._discovery_next - time.monotonic()
            > _ship._DISCOVERY_RETRY_S * 10
        )

    def test_3xx_discovery_backs_off_a_full_ttl_not_60s(self, monkeypatch):
        """A 3xx redirect we don't follow (our collectors serve the doc
        directly at 200) is a definite non-transient "nothing usable
        here" — grouped with 4xx into the TTL backoff, NOT the 60s
        transient cadence."""
        def _redirect(request: httpx.Request) -> httpx.Response:
            return httpx.Response(302, headers={"location": "https://x/y"})

        monkeypatch.setattr(
            _ship, "_transport_for_tests", httpx.MockTransport(_redirect)
        )
        sink = _ship.ShipSink("backend", _config(url=None))
        sink(_message("x"))
        sink.flush()
        assert sink._resolved_url is None
        assert (
            sink._discovery_next - time.monotonic()
            > _ship._DISCOVERY_RETRY_S * 10
        )

    def test_5xx_discovery_keeps_the_short_retry(self, monkeypatch):
        """A 5xx is the collector transiently failing — NOT "no service
        here". It must stay on the 60s retry so recovery is quick, the
        same stance as a network error (test_unresolvable_...). Do not
        let the 4xx TTL-backoff widen onto it."""
        def _boom(request: httpx.Request) -> httpx.Response:
            return httpx.Response(503, text="upstream unavailable")

        monkeypatch.setattr(
            _ship, "_transport_for_tests", httpx.MockTransport(_boom)
        )
        sink = _ship.ShipSink("backend", _config(url=None))
        sink(_message("x"))
        sink.flush()
        assert sink._resolved_url is None
        # short retry: within ~60s, well under a TTL
        assert (
            sink._discovery_next - time.monotonic()
            < _ship._DISCOVERY_TTL_S / 2
        )

    def test_bad_shape_document_with_no_stale_drops_quietly(self, monkeypatch):
        def _garbage(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=["not", "a", "mapping"])

        monkeypatch.setattr(
            _ship, "_transport_for_tests", httpx.MockTransport(_garbage)
        )
        sink = _ship.ShipSink("backend", _config(url=None))
        sink(_message("x"))
        sink.flush()  # must not raise; nothing resolved, batch dropped
        assert sink._resolved_url is None
        assert sink._fail_streak == 0  # discovery failure ≠ breaker food

    def test_disallowed_discovery_url_rejected(self, monkeypatch):
        """The discovery document decides where user logs go and is
        served from a public endpoint — https + *.narra.nexus only; a
        hijacked or misconfigured document must not redirect telemetry."""
        hits: list = []
        mapping = {"default": "https://evil.example.com/v1/ingest"}
        monkeypatch.setattr(
            _ship, "_transport_for_tests", self._routing_transport(mapping, hits)
        )
        sink = _ship.ShipSink("backend", _config(url=None))
        sink(_message("x"))
        sink.flush()
        assert sink._resolved_url is None
        assert hits.count("POST") == 0

    def test_allowlist_shapes(self):
        ok = _ship._allowed_ingest_url
        assert ok("https://agent.narra.nexus/telemetry/v1/ingest")
        assert ok("https://dev-agent.narra.nexus/x")
        assert not ok("http://agent.narra.nexus/x")  # https only
        assert not ok("https://narra.nexus.evil.com/x")  # suffix trick
        assert not ok("https://evil.com/narra.nexus")


def test_run_sh_staging_sandbox_discovers_from_dev_collector():
    """run.sh redirects STAGING sandboxes' telemetry discovery to the
    DEV collector — the invariant that lets staging validation proceed
    without the prod collector. It lives as one `if` in a 145-line
    function with no other alarm surface in container mode; a later
    edit that removes it or flips the case would keep CI green while
    telemetry silently goes dark. Lives here (not the channel-guard
    file) because it is a telemetry contract; reuses that file's
    run.sh-text-assertion approach via a local REPO_ROOT read.

    Asserts the redirect and its _is_manyfold_sandbox guard CO-OCCUR in
    one if-condition — a whole-file substring check would pass even
    after the guard line is deleted (the token appears elsewhere), the
    exact false-assurance the reviewer flagged. Anchors from the export
    line back to ITS governing `if ... then` (not a split of every
    2-space if...fi block, which a future single-line `...; fi` earlier
    in the file could merge), so it is immune to indentation and
    line-continuation reformatting."""
    run_sh = (Path(__file__).resolve().parents[3] / "run.sh").read_text(
        encoding="utf-8"
    )
    export_line = (
        'export NEXUS_DIAG_DISCOVERY_URL='
        '"https://dev-agent.narra.nexus/telemetry/v1/config"'
    )
    idx = run_sh.find(export_line)
    assert idx != -1, (
        "run.sh no longer redirects a staging label to the dev collector"
    )
    # The redirect's governing condition = text from the nearest `if `
    # up to the `then` that opens the block this export sits in.
    then_idx = run_sh.rfind("then", 0, idx)
    assert then_idx != -1, "redirect export is not inside an if...then block"
    if_idx = run_sh.rfind("\n  if ", 0, then_idx)
    assert if_idx != -1, "cannot locate the redirect's governing if"
    condition = run_sh[if_idx:then_idx]
    # The export must be the FIRST statement of that block — nothing
    # between `then` and the export but whitespace.
    assert run_sh[then_idx + len("then"):idx].strip() == "", (
        "the dev-collector redirect is not the guarded block's first "
        "statement — its governing condition may not apply to it"
    )
    assert '"staging"' in condition, (
        "dev-collector redirect is no longer keyed on the staging label"
    )
    assert "_is_manyfold_sandbox" in condition, (
        "dev-collector redirect lost its _is_manyfold_sandbox guard — a "
        "hand-set NEXUS_DIAG_ENV=staging on a personal install would leak "
        "logs to our dev collector (the whole point of last round's guard)"
    )


class _FakeClock:
    """Monotonic clock the test advances by hand, so a simulated day of
    flushes runs in milliseconds without touching real time."""

    def __init__(self, start: float = 1_000.0) -> None:
        self.now = start

    def monotonic(self) -> float:
        return self.now


class TestMissingCollectorBackoff:
    """B-39: prod has no diag-collector, so /telemetry/v1/config answers
    502 and every shipping process used to re-probe it every 60s forever
    (~1.4k GETs per process per day). A 5xx / network failure must back
    off exponentially, with jitter, up to a long cap; the same holds for
    a dead ingest (the breaker cooldown)."""

    _DAY_S = 24 * 3600
    _STEP_S = 30

    def _simulate_day(self, monkeypatch, handler, config) -> list:
        clock = _FakeClock()
        monkeypatch.setattr(
            _ship, "time", SimpleNamespace(monotonic=clock.monotonic, sleep=time.sleep)
        )
        hits: list = []

        def _handle(request: httpx.Request) -> httpx.Response:
            hits.append(request.method)
            return handler(request)

        monkeypatch.setattr(
            _ship, "_transport_for_tests", httpx.MockTransport(_handle)
        )
        sink = _ship.ShipSink("backend", config)
        start = clock.now
        while clock.now - start < self._DAY_S:
            sink(_message("tick"))
            sink.flush()
            clock.now += self._STEP_S
        return hits

    def test_502_discovery_probes_a_bounded_number_of_times_per_day(self, monkeypatch):
        hits = self._simulate_day(
            monkeypatch,
            lambda r: httpx.Response(502, text="bad gateway"),
            _config(url=None),
        )
        gets = hits.count("GET")
        # 60s fixed retry = 1440/day; exponential to a 6h cap is ~15.
        assert 3 <= gets <= 20, gets
        assert hits.count("POST") == 0

    def test_network_error_discovery_probes_a_bounded_number_of_times(self, monkeypatch):
        def _dead(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("refused", request=request)

        hits = self._simulate_day(monkeypatch, _dead, _config(url=None))
        assert 3 <= hits.count("GET") <= 20, hits.count("GET")

    def test_404_discovery_also_grows_past_one_ttl(self, monkeypatch):
        hits = self._simulate_day(
            monkeypatch,
            lambda r: httpx.Response(404, json={"detail": "no config"}),
            _config(url=None),
        )
        # Fixed 1h TTL = 24/day; doubling from 1h to the cap is far fewer.
        assert 1 <= hits.count("GET") <= 8, hits.count("GET")

    def test_dead_ingest_breaker_cooldown_grows(self, monkeypatch):
        hits = self._simulate_day(
            monkeypatch,
            lambda r: httpx.Response(502, text="bad gateway"),
            _config(),  # direct URL: every request is an ingest POST
        )
        posts = hits.count("POST")
        # Fixed 60s cooldown = threshold + ~1440 probes/day.
        assert posts <= _ship._BREAKER_THRESHOLD + 20, posts

    def test_success_resets_the_discovery_backoff(self, monkeypatch):
        """Positive case: once the collector exists again, the sink
        resolves and the failure streak is forgotten, so the NEXT outage
        starts again from the short retry instead of the cap."""
        clock = _FakeClock()
        monkeypatch.setattr(
            _ship, "time", SimpleNamespace(monotonic=clock.monotonic, sleep=time.sleep)
        )
        state = {"status": 502}
        mapping = {"default": "https://agent.narra.nexus/telemetry/v1/ingest"}

        def _handle(request: httpx.Request) -> httpx.Response:
            if request.method == "GET" and state["status"] == 502:
                return httpx.Response(502, text="bad gateway")
            if request.method == "GET":
                return httpx.Response(200, json={"ingest": mapping})
            return httpx.Response(200, json={"ok": True})

        monkeypatch.setattr(
            _ship, "_transport_for_tests", httpx.MockTransport(_handle)
        )
        sink = _ship.ShipSink("backend", _config(url=None))
        for _ in range(6):  # several consecutive failures grow the delay
            clock.now = max(clock.now, sink._discovery_next) + 1
            sink(_message("x"))
            sink.flush()
        assert sink._discovery_next - clock.now > _ship._DISCOVERY_RETRY_S * 10
        state["status"] = 200
        clock.now = sink._discovery_next + 1
        sink(_message("y"))
        sink.flush()
        assert sink._resolved_url == mapping["default"]
        # Next outage: back to the short first step.
        state["status"] = 502
        clock.now = sink._url_expires + 1
        sink(_message("z"))
        sink.flush()
        assert sink._discovery_next - clock.now <= _ship._DISCOVERY_RETRY_S

    @pytest.mark.parametrize("status", [502, 200])
    def test_concurrent_threads_spend_a_single_probe(self, monkeypatch, status):
        """The timer thread and the enqueue worker both call _ingest_url;
        the claim (read _discovery_next + write the hold) must be atomic
        so only ONE of them sends the discovery GET. Reading the hold is
        made to rendezvous all threads: without the lock every thread
        reads the old value before anyone writes the hold (N GETs); with
        it, the first reader times the barrier out while the rest wait on
        the lock, then see the hold (1 GET)."""
        import threading

        n = 6
        barrier = threading.Barrier(n)

        class _RendezvousSink(_ship.ShipSink):
            @property
            def _discovery_next(self):
                seen = self.__dict__.get("_dn", 0.0)
                try:
                    barrier.wait(timeout=0.3)
                except threading.BrokenBarrierError:
                    pass
                return seen

            @_discovery_next.setter
            def _discovery_next(self, value):
                self.__dict__["_dn"] = value

        mapping = {"default": "https://agent.narra.nexus/telemetry/v1/ingest"}
        gets: list = []
        gets_lock = threading.Lock()

        def _handle(request: httpx.Request) -> httpx.Response:
            with gets_lock:
                gets.append(request.method)
            if status == 200:
                return httpx.Response(200, json={"ingest": mapping})
            return httpx.Response(502, text="bad gateway")

        monkeypatch.setattr(
            _ship, "_transport_for_tests", httpx.MockTransport(_handle)
        )
        sink = _RendezvousSink("backend", _config(url=None))
        results: list = []
        threads = [
            threading.Thread(target=lambda: results.append(sink._ingest_url()))
            for _ in range(n)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)
        assert gets.count("GET") == 1, gets
        assert len(results) == n
        if status == 200:
            assert sink._resolved_url == mapping["default"]
            assert sink._discovery_failures == 0
        else:
            assert sink._discovery_failures == 1

    def test_backoff_delay_is_jittered_and_capped(self):
        d1 = _ship._backoff_delay(_ship._DISCOVERY_RETRY_S, 1)
        assert _ship._DISCOVERY_RETRY_S * 0.5 <= d1 <= _ship._DISCOVERY_RETRY_S
        assert _ship._backoff_delay(_ship._DISCOVERY_RETRY_S, 50) <= _ship._BACKOFF_CAP_S
        samples = {_ship._backoff_delay(3600.0, 3) for _ in range(20)}
        assert len(samples) > 1  # jitter: a fleet does not probe in lockstep


class TestCloudDefault:
    """A multi-tenant cloud stack has no user toggle (PUT is 403 there);
    it ships only when the deployment opts in. Before this, prod's own
    containers shipped at the built-in meta default and beaconed a
    collector prod never deployed."""

    def test_cloud_without_opt_in_is_off(self, monkeypatch):
        monkeypatch.setenv("NARRANEXUS_DEPLOYMENT_MODE", "cloud")
        assert _ship.telemetry_consent() == {"mode": "off", "source": "default"}
        assert _ship.ship_config() is None

    def test_cloud_env_override_still_ships(self, monkeypatch):
        monkeypatch.setenv("NARRANEXUS_DEPLOYMENT_MODE", "cloud")
        monkeypatch.setenv("NEXUS_DIAG_SHIP", "full")
        config = _ship.ship_config()
        assert config is not None and config["mode"] == "full"

    def test_cloud_managed_default_still_ships(self, monkeypatch):
        monkeypatch.setenv("NARRANEXUS_DEPLOYMENT_MODE", "cloud")
        monkeypatch.setenv("NEXUS_DIAG_DEFAULT_SHIP", "meta")
        assert _ship.telemetry_consent() == {"mode": "meta", "source": "default"}

    def test_local_default_stays_meta(self, monkeypatch):
        monkeypatch.setenv("NARRANEXUS_DEPLOYMENT_MODE", "local")
        assert _ship.telemetry_consent() == {"mode": "meta", "source": "default"}

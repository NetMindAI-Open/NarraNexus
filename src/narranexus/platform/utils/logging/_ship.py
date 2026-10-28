"""
@file_name: _ship.py
@author:
@date: 2026-08-10
@description: Network log sink — the PUSH half of the observability
design (pull half = the manyfold diagnostics endpoints; the two share
no machinery on purpose).

``setup_logging`` registers this as one more loguru sink; the file sink
stays the source of truth and this ships a copy to the collector
(scripts/diag_collector). This is product TELEMETRY, not an operator
feature: no deployer has to configure anything, and the authorization
basis is the user's own consent.

Consent resolution (first match wins):
  1. ``NEXUS_DIAG_SHIP`` env — explicit override: ``off`` silences,
     ``meta``/``full`` force a level (dev/self-host knob, and the test
     suite's kill switch).
  2. Opt-out marker file ``~/.narranexus/telemetry_optout`` (path
     overridable via ``NEXUS_DIAG_OPTOUT_FILE`` — containerized
     self-hosts must point every service at ONE mounted path) — written
     by the settings UI (``set_telemetry_optout``) when the user turns
     telemetry off. DURABILITY IS A MOUNT PROPERTY: the marker survives
     exactly as long as the filesystem under its path does. In a
     container, HOME is writable layer — a rebuild would silently
     revert an opted-out full-level sandbox to shipping — so run.sh
     container mode points the marker at /data (the persisted volume).
     Anyone touching the Dockerfile HOME/volume layout is touching
     consent state. Withdrawal is honoured WITHOUT a restart: ``_send``
     re-checks consent on every egress, so an opt-out written mid-run
     silences shipping within one flush interval. Re-enabling needs a
     restart ONLY when telemetry was already off at process start (no
     sink was registered then); a mid-run off→on flip resumes at the
     next flush through the same gate. The file is PER-MACHINE: callers on
     multi-tenant surfaces must gate the write themselves (the backend
     route refuses it in cloud mode).
  3. Managed default: ``NEXUS_DIAG_DEFAULT_SHIP`` (``meta``/``full``)
     — a DEFAULT-layer input, not an override: it changes what applies
     when the user has expressed nothing, and the opt-out marker still
     wins. This is how our run.sh container mode gives manyfold
     sandboxes ``full`` (joint-support debugging is their telemetry's
     purpose) WITHOUT confiscating the user's switch — an env override
     here would grey the toggle and 409 the PUT, hollowing out the
     consent design on exactly the single-tenant user runtime it was
     built for. Invalid/absent values fall through; ``off`` is not
     accepted (a deployment that wants silence sets NEXUS_DIAG_SHIP).
  4. Cloud default: on a multi-tenant cloud deployment
     (``is_cloud_mode()``) nothing ships unless layer 1 or 3 opted
     in — no user there can hold the toggle (the settings PUT is 403),
     so the built-in default below has no consent basis on that
     surface. Our dev stack opts in with ``NEXUS_DIAG_SHIP=full``;
     prod, which runs no collector, ships nothing.
  5. Default: ``_DEFAULT_MODE`` = **meta** — the consent basis (the
     first-run disclosure + settings toggle) shipped in the same
     change that flipped this from off, never apart. meta (no INFO
     bodies) is the highest level the base disclosure copy honestly
     describes; the copy names the full-level content for surfaces
     that run full. Single-tenant surfaces (desktop, local, sprite
     sandboxes) show the toggle — sandboxes run full via the managed
     default and can still opt out; multi-tenant cloud is governed by
     the deployment env instead (layer 4).

  meta — AUDIT (25) and up: structured lifecycle events + warnings,
         no INFO bodies.
  full — everything the file sink sees, at its resolved level.

Ingest URL resolution (first match wins):
  1. ``NEXUS_DIAG_SHIP_URL`` env — direct override (points a dev/self-
     hosted deployment at its own collector; skips discovery).
  2. Discovery: ``GET <NEXUS_DIAG_DISCOVERY_URL or the built-in
     https://agent.narra.nexus/telemetry/v1/config>`` returns
     ``{"ingest": {"default": url, "staging": url, ...}}``; the sender
     picks by its env label — ``NEXUS_DIAG_ENV`` > ``NARRA_SURFACE`` >
     deployment mode ("cloud"/"local") — falling back to the map's
     "default" entry. Deployment-specific label derivation (e.g.
     manyfold staging detection) lives in run.sh container mode, which
     injects ``NEXUS_DIAG_ENV`` — this utility reads no other
     integration's env vars. FOUR-SIDED CONTRACT — a label lives in
     four places and introducing one means updating all four:
     (1) the sender's env label (here); (2) the collector's
     ``DIAG_COLLECT_KNOWN_ENVS``, which decides the STORAGE PARTITION
     (a label missing there is demoted into unknown/, the partition
     its size cap drains first); (3) the discovery document's ingest
     map, which decides the RECEIVING HOST — ``mapping.get(label)``
     falls back to "default", so a label missing from the map silently
     ships to the default collector with no warning on either side;
     (4) WHICH collector serves that discovery document to this label:
     prod (agent.narra.nexus) is the built-in default, but run.sh
     points ``staging`` sandboxes at the dev collector — so a redirected
     label's collector must serve DIAG_COLLECT_CONFIG_JSON too, or the
     sender 404s and backs off a full TTL.

     The document is fetched LAZILY on the worker thread with a TTL
     cache — never at setup, so process start and test runs touch no
     network. Unresolvable discovery leaves the sink idle until the
     next probe; consecutive failed probes back off exponentially with
     jitter (from 60s for 5xx/network, from the TTL for a definite
     3xx/4xx/not-a-document answer) up to ``_BACKOFF_CAP_S``.

No client secret: the repository is open source, so a baked or shared
token authenticates nothing — the collector is a public endpoint
hardened by abuse controls (size caps, rate limits), and a spoofed
envelope only pollutes our own diagnostics. ``NEXUS_DIAG_SHIP_TOKEN``
remains an optional knob for private collectors.

The ship level is enforced at loguru dispatch (the sink is registered
WITH that minimum level), so filtered records cost nothing.

Delivery contract — never interfere with the process being observed:
- ``enqueue=True`` gives this sink its own queue + worker thread; the
  emitting coroutine never waits on the network, and a slow collector
  cannot back up the FILE sink (separate queue).
- Batches flush at ``_BATCH_MAX`` records, ``_BATCH_MAX_BYTES``
  serialized bytes (half the collector's 8MB wire cap, gzip headroom —
  count-only batching can exceed a byte-capped receiver), or
  ``_FLUSH_INTERVAL_S`` seconds. Send failures DROP the batch after a
  short timeout — the file sink still has every line; gaps are pulled
  via the diagnostics endpoints. Failures report via ``sys.stderr``
  (never through loguru: a failing ship must not emit into itself).
- Circuit breaker: ``_BREAKER_THRESHOLD`` consecutive TRANSIENT
  failures OPEN it for ``_BREAKER_COOLDOWN_S``, doubling (with jitter,
  up to ``_BACKOFF_CAP_S``) on every re-open without a delivery in
  between — records drop at the
  door instead of buffering (a dead collector must not become a memory
  leak: the enqueue queue is unbounded while the drain rate is floored
  at timeout×batch). After the cooldown the breaker goes HALF-OPEN:
  traffic is admitted for one probe batch, whose failure re-opens
  IMMEDIATELY (no re-earning the threshold) and whose success closes
  fully. Permanent rejections (4xx: over-size, auth misconfig) drop
  the batch WITHOUT feeding the breaker — "this batch is unacceptable"
  is not "the collector is down".
- ONE module-level ``atexit`` handler flushes every live sink on clean
  exit (final shutdown otherwise loses the last interval forever;
  startup backfill only helps when the SAME service starts again).
  Sinks are tracked in a ``WeakSet`` — per-instance atexit handlers
  would pin every sink ever constructed and stack serial timeouts.
- On registration, the tail of today's existing log file is shipped
  once with ``"backfill": true`` — the crash-tail catch-up, so lines
  written just before an unclean restart still reach the collector.

Identity: every record carries the envelope fields
``(env, runtime_id, host, service)`` merged flat — the collector
partitions by them, and on a sprite (single manyfold runtime) that
makes every line attributable without per-line agent binding.
"""
from __future__ import annotations

import atexit
import gzip
import json
import os
import random
import socket
import sys
import threading
import time
import weakref
from pathlib import Path
from typing import Any, Optional

import httpx

from narranexus.platform.utils.deployment_mode import get_deployment_mode, is_cloud_mode

# Test seam, mirroring integrations/manyfold_outbound.
_transport_for_tests: Optional[httpx.BaseTransport] = None

_BATCH_MAX = 200
_BATCH_MAX_BYTES = 4 * 1024 * 1024
_FLUSH_INTERVAL_S = 3.0
_SEND_TIMEOUT_S = 2.0
_BACKFILL_LINES = 200
_AUDIT_LEVEL_NO = 25
_BREAKER_THRESHOLD = 5
_BREAKER_COOLDOWN_S = 60.0

_DISCOVERY_URL_DEFAULT = "https://agent.narra.nexus/telemetry/v1/config"
_DISCOVERY_TTL_S = 3600.0
_DISCOVERY_RETRY_S = 60.0
# Every repeated failure (discovery probe or breaker re-open) doubles its
# wait up to this cap, with jitter. A fixed 60s retry against a collector
# that does not exist (prod has none: its /telemetry/v1/config answers
# 502) cost ~1.4k requests per process per day; doubling to 6h costs ~15.
# The price is recovery latency: a collector deployed during an outage is
# picked up within one cap, which is fine for best-effort telemetry.
_BACKOFF_CAP_S = 6 * 3600.0
# Each delay is drawn from [(1 - jitter) * d, d] so a fleet of processes
# that failed together does not re-probe in lockstep.
_BACKOFF_JITTER = 0.25
_OPTOUT_FILE = Path.home() / ".narranexus" / "telemetry_optout"


def _optout_file() -> Path:
    """Marker path, resolved PER CALL. ``NEXUS_DIAG_OPTOUT_FILE``
    overrides for containerized single-tenant self-hosts: with one
    container per service and no shared HOME mount, a marker written
    by the backend container is invisible to poller/mcp/workers (they
    keep shipping while the UI reports "off") and dies with the
    writable layer on recreate — point every service at ONE mounted
    (absolute) path instead. Call-time resolution keeps the test
    fixtures that repoint ``_OPTOUT_FILE`` working and needs no child
    interpreter to test."""
    override = os.environ.get("NEXUS_DIAG_OPTOUT_FILE", "").strip()
    return Path(override) if override else _OPTOUT_FILE
# Default LEVEL is "meta" (AUDIT+ structured events, warnings, errors
# — no INFO bodies), because "full" ships INFO lines verbatim and
# production INFO includes entire user messages (agent_runtime logs
# input_content) — content the disclosure copy explicitly does not
# cover. "full" stays an explicit deployment knob (env override);
# raising the DEFAULT to full requires a ship-side redaction pass and
# rewritten disclosure copy first. Flipped from "off" in the same
# change that shipped the consent basis (disclosure + settings toggle).
_DEFAULT_MODE = "meta"
# Discovery may only point telemetry at hosts we control: the document
# is served from a PUBLIC endpoint, and this string decides where user
# logs go. https-only + domain allowlist; a hijacked/misconfigured
# document is rejected and the previous resolution (if any) survives.
_ALLOWED_INGEST_SUFFIXES = ("narra.nexus",)

_LIVE_SINKS: "weakref.WeakSet[ShipSink]" = weakref.WeakSet()


def _backoff_delay(base_s: float, failures: int) -> float:
    """Wait before the next attempt after ``failures`` consecutive
    failures: ``base_s * 2**(failures - 1)``, capped at _BACKOFF_CAP_S,
    then jittered downwards (never above the cap)."""
    exponent = min(max(failures, 1) - 1, 32)
    raw = min(_BACKOFF_CAP_S, base_s * (2 ** exponent))
    return raw * random.uniform(1.0 - _BACKOFF_JITTER, 1.0)


def _flush_all_at_exit() -> None:
    # atexit is LIFO and this handler registers at import time — AFTER
    # loguru registers its own teardown — so it runs while records may
    # still sit in loguru's enqueue worker. Drain that queue FIRST
    # (complete() waits for it without tearing handlers down), or the
    # tail of the final batch reaches the sink buffer only after
    # close() has guaranteed it never goes out.
    try:
        from loguru import logger as _logger

        _logger.complete()
    except Exception:  # noqa: BLE001 — exiting; drain is best-effort
        pass
    for sink in list(_LIVE_SINKS):
        try:
            sink.flush()
            sink._client.close()
        except Exception:  # noqa: BLE001 — exiting; nothing left to report to
            pass


atexit.register(_flush_all_at_exit)


def telemetry_consent() -> dict:
    """Consent state plus WHICH layer of the precedence chain decided
    it — the settings UI may only offer the toggle for "optout" and
    "default" (an env override is the deployment's decision, not the
    user's). Single source of the precedence rule; ship_mode() is a
    view of it."""
    raw = os.environ.get("NEXUS_DIAG_SHIP", "").strip().lower()
    if raw in ("off", "meta", "full"):
        return {"mode": raw, "source": "env"}
    if _optout_file().exists():
        return {"mode": "off", "source": "optout"}
    managed = os.environ.get("NEXUS_DIAG_DEFAULT_SHIP", "").strip().lower()
    if managed in ("meta", "full"):
        return {"mode": managed, "source": "default"}
    if is_cloud_mode():
        # Multi-tenant cloud: no user can hold the consent toggle (the
        # settings PUT is 403 there), so the built-in default does not
        # apply — the deployment opts in via NEXUS_DIAG_SHIP or the
        # managed default above, or nothing ships.
        return {"mode": "off", "source": "default"}
    return {"mode": _DEFAULT_MODE, "source": "default"}


def set_telemetry_optout(opted_out: bool) -> None:
    """Create/remove the opt-out marker. Scope is per-USER-ACCOUNT on
    this host (Path.home()), not strictly per-machine: a co-deployed
    process under a different HOME keeps its own consent state — on a
    desktop install all sidecars share HOME, so the distinction is
    latent there (containerized self-hosts: see _optout_file).
    Idempotent; resolution is per call — tests repoint ``_OPTOUT_FILE``
    and the settings route must follow."""
    marker = _optout_file()
    if opted_out:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.touch()
    else:
        marker.unlink(missing_ok=True)


def ship_mode() -> str:
    """Consent + level resolution. See the module docstring for the
    precedence; "off" means "do not register the sink at all"."""
    return telemetry_consent()["mode"]


def _env_label() -> str:
    """Envelope + routing label. Another INTEGRATION's env vars are
    off-limits here (run.sh container mode derives and injects
    ``NEXUS_DIAG_ENV`` from them instead) — but our OWN deployment
    contract is fair game, and necessary: cloud-stack containers are
    started by compose directly (no run.sh, no NARRA_SURFACE), so
    without the ``get_deployment_mode()`` fallback every cloud record
    would be labeled "unknown" and land in the collector's stranger
    bucket — the partition its size cap drains FIRST. Caveat: that
    fallback is contract-first but HEURISTIC second — absent
    ``NARRANEXUS_DEPLOYMENT_MODE`` it guesses from the database URL
    (non-sqlite → "cloud"), so a local dev install pointed at MySQL
    self-labels "cloud"; treat the label as best-effort routing, not
    a strong identity."""
    label = (
        os.environ.get("NEXUS_DIAG_ENV", "").strip()
        or os.environ.get("NARRA_SURFACE", "").strip()
    )
    if label:
        return label
    return get_deployment_mode()


def _allowed_ingest_url(url: str) -> bool:
    from urllib.parse import urlsplit

    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    host = (parts.hostname or "").lower()
    return parts.scheme == "https" and any(
        host == sfx or host.endswith("." + sfx)
        for sfx in _ALLOWED_INGEST_SUFFIXES
    )


def ship_config() -> Optional[dict]:
    """Resolved sender config, or None when telemetry is off.

    ``url`` may be None here: without an env override the ingest URL
    comes from discovery, resolved lazily on the worker thread (process
    start and test runs must not touch the network)."""
    mode = ship_mode()
    if mode == "off":
        return None
    return {
        "mode": mode,
        "url": os.environ.get("NEXUS_DIAG_SHIP_URL", "").strip() or None,
        "token": os.environ.get("NEXUS_DIAG_SHIP_TOKEN", "").strip(),
        "env": _env_label(),
        "runtime_id": os.environ.get("MANYFOLD_RUNTIME_ID", "").strip() or "-",
    }


class ShipSink:
    """Loguru callable sink: buffer → batch → gzip POST. Never raises."""

    def __init__(self, service: str, config: dict) -> None:
        self._service = service
        self._config = config
        self._host = socket.gethostname()
        # Buffer holds ENCODED lines: bytes are what both the flush
        # threshold and the wire measure, and each record is serialized
        # + encoded exactly once (len(str) counts characters — CJK text
        # would have tripped the "4MB" threshold around 12MB).
        self._buf: list[bytes] = []
        self._buf_bytes = 0
        self._lock = threading.Lock()
        # One long-lived connection pool for discovery AND sends: a
        # fresh client per request pays TCP+TLS twice per delivery
        # inside a 2s budget — a healthy-but-slow-handshake collector
        # would read as five straight timeouts and OPEN the breaker.
        self._client = httpx.Client(
            timeout=_SEND_TIMEOUT_S, transport=_transport_for_tests
        )
        # Guards breaker state (_cooldown_until/_half_open/_fail_streak):
        # read on the loguru worker (__call__) and written on whichever
        # thread ran the send. RLock: the send path holds it across
        # _send_locked → _note_transient_failure → _open_breaker.
        self._state_lock = threading.RLock()
        # Serializes _send: both the enqueue worker (size-triggered flush)
        # and the timer thread can reach it; without this the breaker
        # accounting races and two batches can interleave on the wire.
        self._send_lock = threading.Lock()
        self._last_flush = time.monotonic()
        self._fail_streak = 0
        # Breaker state: OPEN while now < _cooldown_until; HALF-OPEN once
        # the cooldown elapsed, until a probe batch settles it.
        self._cooldown_until = 0.0
        self._half_open = False
        self._dropped_in_cooldown = 0
        # Consecutive breaker OPENs without a delivery in between; each
        # one doubles the cooldown (reset by any collector answer).
        self._breaker_trips = 0
        # Lazy discovery state: url resolves on the worker thread at
        # first send, refreshes on TTL, backs off between failed probes.
        self._resolved_url: Optional[str] = None
        self._url_expires = 0.0
        self._discovery_next = 0.0
        self._discovery_failures = 0
        self._discovery_noted = False
        _LIVE_SINKS.add(self)
        # Periodic flusher so a quiet process still delivers its tail.
        # Daemon: never blocks interpreter shutdown.
        self._timer = threading.Thread(
            target=self._flush_loop, name=f"diag-ship-{service}", daemon=True
        )
        self._timer.start()

    # -- loguru entry point ------------------------------------------------

    def __call__(self, message: Any) -> None:
        try:
            if self._breaker_open():
                with self._state_lock:
                    self._dropped_in_cooldown += 1
                return
            record = message.record
            entry = {
                "ts": record["time"].isoformat(),
                "level": record["level"].name,
                "service": self._service,
                "env": self._config["env"],
                "runtime_id": self._config["runtime_id"],
                "host": self._host,
                "run_id": str(record["extra"].get("run_id", "") or ""),
                "event_id": str(record["extra"].get("event_id", "") or ""),
                "logger": f"{record['name']}:{record['function']}:{record['line']}",
                "message": record["message"],
            }
            self._push(json.dumps(entry, ensure_ascii=False).encode("utf-8"))
        except Exception as e:  # noqa: BLE001 — a broken ship must stay silent
            sys.stderr.write(f"[diag-ship] record build failed: {e}\n")

    def _push(self, line: bytes) -> None:
        with self._lock:
            self._buf.append(line)
            self._buf_bytes += len(line)
            should_flush = (
                len(self._buf) >= _BATCH_MAX
                or self._buf_bytes >= _BATCH_MAX_BYTES
            )
        if should_flush:
            self.flush()

    def _flush_loop(self) -> None:
        while True:
            time.sleep(_FLUSH_INTERVAL_S)
            if time.monotonic() - self._last_flush >= _FLUSH_INTERVAL_S:
                self.flush()

    # -- circuit breaker ---------------------------------------------------

    def _breaker_open(self) -> bool:
        with self._state_lock:
            if self._cooldown_until <= 0:
                return False
            if time.monotonic() < self._cooldown_until:
                return True
            # Cooldown elapsed → HALF-OPEN: admit traffic; the next probe
            # batch settles it (failure re-opens, success closes).
            self._cooldown_until = 0.0
            self._half_open = True
            dropped, self._dropped_in_cooldown = self._dropped_in_cooldown, 0
        if dropped:
            sys.stderr.write(
                f"[diag-ship] breaker half-open after cooldown; "
                f"{dropped} records dropped while open (file log intact)\n"
            )
        return False

    def _open_breaker(self, reason: str) -> None:
        with self._state_lock:
            self._breaker_trips += 1
            cooldown = _backoff_delay(_BREAKER_COOLDOWN_S, self._breaker_trips)
            self._cooldown_until = time.monotonic() + cooldown
            self._half_open = False
            self._fail_streak = 0
        with self._lock:
            dropped = len(self._buf)
            self._buf.clear()
            self._buf_bytes = 0
        with self._state_lock:
            self._dropped_in_cooldown += dropped
        sys.stderr.write(
            f"[diag-ship] breaker OPEN ({reason}); dropping records for "
            f"{cooldown:.0f}s (file log intact; recover via "
            f"diagnostics pull)\n"
        )

    # -- delivery ----------------------------------------------------------

    def flush(self) -> None:
        # With the breaker open the door is already starving the buffer;
        # anything still here (e.g. the atexit sweep racing a fresh OPEN)
        # must not spend a timeout on a known-dead collector.
        with self._state_lock:
            breaker_holding = (
                self._cooldown_until > 0
                and time.monotonic() < self._cooldown_until
            )
        if breaker_holding:
            with self._lock:
                dropped = len(self._buf)
                self._buf.clear()
                self._buf_bytes = 0
            with self._state_lock:
                self._dropped_in_cooldown += dropped
            return
        with self._lock:
            if not self._buf:
                return
            batch, self._buf = self._buf, []
            self._buf_bytes = 0
            self._last_flush = time.monotonic()
        self._send(batch)

    def _ingest_url(self) -> Optional[str]:
        """Env override, else discovery with TTL cache + retry backoff.

        Runs only on the worker/timer threads (send path) — never at
        setup. Stale-if-error: a previously resolved URL outlives a
        failed refresh; with nothing resolved the batch is dropped
        quietly until a probe succeeds.

        Failed probes back off EXPONENTIALLY (see _backoff_delay) from a
        base that depends on the answer: a 5xx or network error starts
        at the short _DISCOVERY_RETRY_S (it may be transient), a
        definite "nothing usable here" (3xx/4xx, a 200 that is not a
        discovery document) starts at a full _DISCOVERY_TTL_S. Both
        double per consecutive failure up to _BACKOFF_CAP_S; a
        successful resolution resets the streak."""
        if self._config["url"]:
            return self._config["url"]
        # Claim the probe atomically: the sink's own timer thread and the
        # loguru enqueue worker both reach here via flush(), and only one
        # of them may spend a request. The HTTP GET itself runs OUTSIDE
        # the lock so a 5s timeout never stalls the other thread.
        with self._state_lock:
            now = time.monotonic()
            if self._resolved_url and now < self._url_expires:
                return self._resolved_url
            if now < self._discovery_next:
                return self._resolved_url
            # Provisional hold while this probe is in flight; overwritten
            # with the real result below.
            self._discovery_next = now + _DISCOVERY_RETRY_S
        discovery = (
            os.environ.get("NEXUS_DIAG_DISCOVERY_URL", "").strip()
            or _DISCOVERY_URL_DEFAULT
        )
        url, retry_base = self._probe_discovery(discovery)
        with self._state_lock:
            if url:
                self._resolved_url = url
                self._url_expires = now + _DISCOVERY_TTL_S
                self._discovery_next = 0.0
                self._discovery_failures = 0
                self._discovery_noted = False
                return url
            self._discovery_failures += 1
            self._discovery_next = now + _backoff_delay(
                retry_base, self._discovery_failures
            )
            first_note = not self._discovery_noted
            self._discovery_noted = True
            resolved = self._resolved_url
        if first_note:
            sys.stderr.write(
                f"[diag-ship] telemetry discovery unresolved via {discovery}; "
                f"batches dropped until it answers, re-probing with "
                f"exponential backoff (file log intact)\n"
            )
        return resolved

    def _probe_discovery(self, discovery: str) -> tuple[Optional[str], float]:
        """One discovery GET (no shared state touched).

        Returns (ingest_url, retry_base): the allowed ingest URL for this
        env, or None plus the backoff base for the failure class."""
        # Until proven otherwise a failure is transient (5xx, network,
        # a disallowed URL); the branches below mark definite answers.
        retry_base = _DISCOVERY_RETRY_S
        try:
            resp = self._client.get(discovery)
            if 300 <= resp.status_code < 500:
                # A 3xx or 4xx is a DEFINITE "no usable discovery
                # document here" answer (404 = collector has no
                # DIAG_COLLECT_CONFIG_JSON, 401/403 = not for us, 3xx =
                # a redirect we do not follow — our collectors serve the
                # doc directly at 200) — same class as the not-a-
                # document 200 below: back off from a full TTL, not the
                # short transient base.
                return None, _DISCOVERY_TTL_S
            if 200 <= resp.status_code < 300:
                try:
                    document = resp.json()
                except ValueError:
                    document = None
                mapping = (
                    document.get("ingest") if isinstance(document, dict) else None
                )
                if not isinstance(mapping, dict):
                    # The endpoint ANSWERED but is not a discovery
                    # endpoint (e.g. an SPA fallback serving index.html
                    # with a 200): this deployment has no telemetry
                    # service right now — a fleet of idle installs must
                    # not beacon the vendor every minute for a service
                    # that isn't there.
                    return None, _DISCOVERY_TTL_S
                url = mapping.get(self._config["env"]) or mapping.get("default")
                if url and _allowed_ingest_url(str(url)):
                    return str(url), retry_base
                if url:
                    sys.stderr.write(
                        f"[diag-ship] discovery offered disallowed ingest "
                        f"url {url!r} (https + *.narra.nexus only); ignored\n"
                    )
        except Exception:  # noqa: BLE001 — discovery is best-effort
            pass
        return None, retry_base

    def _send(self, lines: list[bytes]) -> None:
        # Consent is re-checked at the door on EVERY egress (one stat):
        # the sink registers at startup, but an opt-out written mid-run
        # must silence shipping now, not at the next restart. The env
        # override still wins in both directions via ship_mode().
        if ship_mode() == "off":
            return
        url = self._ingest_url()
        if not url:
            return  # noted once by _ingest_url; file log has everything
        body = gzip.compress(b"\n".join(lines))
        headers = {
            "Content-Type": "application/x-ndjson",
            "Content-Encoding": "gzip",
        }
        if self._config["token"]:
            headers["Authorization"] = f"Bearer {self._config['token']}"
        with self._send_lock:
            self._send_locked(url, body, headers)

    def _send_locked(self, url: str, body: bytes, headers: dict) -> None:
        try:
            resp = self._client.post(url, content=body, headers=headers)
            if 200 <= resp.status_code < 300:
                with self._state_lock:
                    self._fail_streak = 0
                    self._half_open = False
                    self._breaker_trips = 0
                return
            if 400 <= resp.status_code < 500:
                # Permanent rejection (413 over-size, 401 misconfig):
                # "this batch is unacceptable" is not "the collector is
                # down" — drop it, say so, do NOT feed the breaker. The
                # collector answered, so a half-open probe counts as
                # settled-alive (without this the half-open flag stuck).
                with self._state_lock:
                    self._half_open = False
                    self._fail_streak = 0
                    self._breaker_trips = 0
                sys.stderr.write(
                    f"[diag-ship] batch rejected (HTTP {resp.status_code}); "
                    f"dropped without breaker accounting\n"
                )
                return
            self._note_transient_failure(f"HTTP {resp.status_code}")
        except Exception as e:  # noqa: BLE001 — drop the batch, file sink has it
            self._note_transient_failure(f"{type(e).__name__}: {e}")

    def _note_transient_failure(self, reason: str) -> None:
        with self._state_lock:
            if self._half_open:
                half_open_probe = True
            else:
                half_open_probe = False
                self._fail_streak += 1
                threshold_hit = self._fail_streak >= _BREAKER_THRESHOLD
        if half_open_probe:
            # The probe failed — straight back to OPEN.
            self._open_breaker(f"probe failed: {reason}")
            return
        if threshold_hit:
            self._open_breaker(f"consecutive failures ({reason})")
            return
        # First failure and every 50th after: enough signal to notice a
        # dead collector without scrolling stderr off a cliff.
        if self._fail_streak == 1 or self._fail_streak % 50 == 0:
            sys.stderr.write(
                f"[diag-ship] send failed ({reason}); "
                f"streak={self._fail_streak}, batch dropped "
                f"(file log intact; recover via diagnostics pull)\n"
            )

    # -- crash-tail backfill ----------------------------------------------

    def backfill_async(self, log_file: Path) -> None:
        """Backfill on a daemon thread: the send is a synchronous POST
        with up to the full timeout — inline it would stall every process
        START whenever the collector is slow, which is exactly the
        interference this sink promises never to cause."""
        threading.Thread(
            target=self.backfill_from,
            args=(log_file,),
            name=f"diag-ship-backfill-{self._service}",
            daemon=True,
        ).start()

    def backfill_from(self, log_file: Path) -> None:
        """Ship the tail of an existing log file once, flagged backfill.

        Covers lines written just before an unclean restart (the previous
        process died with batches unflushed). Duplicate lines on the
        collector are possible and acceptable — dedup is a read-time
        concern, losing the crash context is not."""
        try:
            if not log_file.is_file():
                return
            lines = log_file.read_text(errors="replace").splitlines()[
                -_BACKFILL_LINES:
            ]
            if not lines:
                return
            batch = [
                json.dumps(
                    {
                        "ts": "",
                        "level": "BACKFILL",
                        "service": self._service,
                        "env": self._config["env"],
                        "runtime_id": self._config["runtime_id"],
                        "host": self._host,
                        "run_id": "",
                        "event_id": "",
                        "logger": "",
                        "message": line,
                        "backfill": True,
                    },
                    ensure_ascii=False,
                ).encode("utf-8")
                for line in lines
            ]
            self._send(batch)
        except Exception as e:  # noqa: BLE001
            sys.stderr.write(f"[diag-ship] backfill failed: {e}\n")


def ship_sink_level(mode: str, resolved_level: str = "INFO") -> Any:
    """Loguru minimum level for the ship sink.

    meta = AUDIT-and-up. full = whatever the FILE sink runs at — the
    documented contract is "full ships what the file sink sees", and
    hardcoding INFO here broke exactly the case that matters most:
    NEXUS_LOG_LEVEL=DEBUG turned on for an incident showed DEBUG in the
    local file but never at the collector."""
    return _AUDIT_LEVEL_NO if mode == "meta" else resolved_level

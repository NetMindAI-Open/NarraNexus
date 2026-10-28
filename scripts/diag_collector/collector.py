"""
@file_name: collector.py
@author:
@date: 2026-08-10
@description: Diagnostic log collector — the receiving end of the
observability push sink (utils/logging/_ship.py).

Runs on OUR ops hosts (staging sandboxes → NarraNexus-dev, prod →
xyz-algo), NOT inside any sandbox. TLS is terminated by the host's
existing caddy; this service listens on a local port behind it. Auth is
a static bearer token shared with the senders via env — service-to-
service HTTP, no SSH/pem involved (pem stays what HUMANS use to log in
and read the collected files).

Storage layout (grep-first: every record is one self-contained JSON
line with the envelope fields merged in):

    <DATA_DIR>/<env>/<runtime_id>/<service>/<YYYY-MM-DD>.jsonl

The <env> level is an ALLOWLIST, not a free value: labels outside
DIAG_COLLECT_KNOWN_ENVS collapse into the single "unknown" bucket at
write time (with a warning naming the demoted label — silent collapse
would let vocabulary drift rot our own data unnoticed), and the size
cap partitions by the env alone. Below the env level, directory
populations are bounded (256 runtimes / 16 services per parent; beyond
the cap new names land in an "overflow" bucket) purely as inode
hygiene — the segment values come from untrusted record fields.
Attribution survives regardless: every record keeps its envelope
fields inline.

Retention: files older than DIAG_COLLECT_RETENTION_DAYS (default 30)
are deleted by a daily sweep (startup + every 24h), which also removes
emptied directories bottom-up.

This is a PUBLIC telemetry endpoint by design (telemetry redesign,
2026-08-11): the sender codebase is open source, so a baked or shared
client secret authenticates nothing — security here is abuse control
(streaming size caps, gzip-bomb bounds, per-IP rate limits), not
secrecy. A spoofed envelope pollutes only our own diagnostics data.
``DIAG_COLLECT_TOKEN`` remains an optional knob for private
deployments; when set, requests must present it.

Env:
    DIAG_COLLECT_TOKEN            optional bearer; set → required on
                                  every request (private deployments)
    DIAG_COLLECT_CONFIG_JSON      optional JSON served verbatim at
                                  GET /telemetry/v1/config — the
                                  discovery document senders resolve
                                  ingest URLs from. Set it on EVERY
                                  collector that senders discover
                                  against: prod (agent.narra.nexus) is
                                  the built-in default, and the DEV
                                  collector (dev-agent) too, because
                                  run.sh points STAGING sandboxes there
                                  (they never read prod's document).
                                  Its keys are the side of the label
                                  contract that decides the RECEIVING
                                  HOST; a label missing here silently
                                  routes to "default". Absent entirely,
                                  /v1/config returns 404 and senders
                                  back off a full TTL.
    DIAG_COLLECT_KNOWN_ENVS       comma-separated env labels that get
                                  their own storage partition; default
                                  "staging,cloud,local,desktop,dev,
                                  sprite" (the sender label vocabulary
                                  + our dev cloud stack + non-staging
                                  manyfold sandboxes — their own
                                  partition, so a full-level sandbox
                                  fleet rotates itself under the size
                                  cap instead of crowding prod). FOUR-SIDED with
                                  the senders' NEXUS_DIAG_ENV, the
                                  discovery map above, and WHICH
                                  collector serves that map: this list
                                  only decides the STORAGE PARTITION,
                                  the map's keys decide the receiving
                                  host, and run.sh decides which
                                  collector a label discovers against
                                  (staging -> dev) — extend all four
                                  together.
                                  Anything else lands in unknown/
                                  with a warning
    DIAG_COLLECT_DATA_DIR         default ~/diag-collect
    DIAG_COLLECT_RETENTION_DAYS   default 30
    DIAG_COLLECT_MAX_DATA_GB      default 20 — HARD footprint cap: the
                                  collector deletes its own oldest
                                  files to stay under it. The host's
                                  disk can never fill because of
                                  telemetry; the worst an attacker
                                  achieves is rotating OUR buffer.
    DIAG_COLLECT_PORT             default 9880

Deployment (EXACT caddy directives — constraints in prose don't get
executed):

    handle_path /telemetry/* {
        request_body { max_size 8MB }
        reverse_proxy 127.0.0.1:9880 {
            header_up X-Real-IP {remote_host}
        }
    }

…and set DIAG_COLLECT_TRUST_REAL_IP=1 on the collector. Without the
flag the X-Real-IP header is IGNORED (a client can send it too — 
trusting it unconditionally re-opens the client-authored-identity hole
this design closed); the failure mode of a missed flag is then "rate
limit degrades to one global bucket", never "rate limit silently
bypassed".

Run: uv run python scripts/diag_collector/collector.py
"""
from __future__ import annotations

import asyncio
import gzip
import hmac
import io
import json
import os
import re
import time
import zlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, TypeVar

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from loguru import logger


@asynccontextmanager
async def _lifespan(app: FastAPI):
    task = asyncio.get_running_loop().create_task(_retention_loop())
    yield
    task.cancel()


app = FastAPI(title="narranexus-diag-collector", lifespan=_lifespan)

_SAFE_SEGMENT_RE = re.compile(r"[^a-zA-Z0-9_\-.]")
_MAX_BODY_BYTES = 32 * 1024 * 1024  # decompressed
# Wire-size cap enforced WHILE reading the request — request.body()
# would buffer an arbitrarily large plaintext POST before any check
# ran (the gzip-bomb guard bounds amplification, not raw size).
_MAX_WIRE_BYTES = 8 * 1024 * 1024


def _bounded_decompress(raw: bytes) -> bytes:
    """Streaming gunzip with the size cap enforced WHILE inflating — a
    checked-after-the-fact limit is no limit at all against a gzip bomb
    (a few KB of request would fully materialize before the 413)."""
    out = io.BytesIO()
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(raw)) as gz:
            while True:
                chunk = gz.read(1024 * 1024)
                if not chunk:
                    break
                out.write(chunk)
                if out.tell() > _MAX_BODY_BYTES:
                    raise HTTPException(status_code=413, detail="batch too large")
    # gzip.decompress raises EOFError on truncation and zlib.error on
    # corruption — neither is an OSError; catching OSError alone turns a
    # malformed body into a 500.
    except (OSError, EOFError, zlib.error):
        raise HTTPException(status_code=400, detail="bad gzip body")
    return out.getvalue()


def _data_dir() -> Path:
    return Path(
        os.environ.get("DIAG_COLLECT_DATA_DIR", "")
        or Path.home() / "diag-collect"
    )


def _retention_days() -> int:
    return int(os.environ.get("DIAG_COLLECT_RETENTION_DAYS", "30"))


def _max_data_bytes() -> int:
    return int(
        float(os.environ.get("DIAG_COLLECT_MAX_DATA_GB", "20")) * 1024**3
    )


# Size-cap bookkeeping: a full tree scan per request would be absurd, so
# writes accumulate into a counter and the enforcement pass runs when
# the estimate says it could matter (or on the daily retention tick).
_size_check_appended = 0
_SIZE_CHECK_EVERY_BYTES = 512 * 1024 * 1024


def enforce_size_cap() -> int:
    """Delete oldest .jsonl files until the data dir fits the cap.

    THE load-bearing defense of the public-endpoint design: the
    collector's footprint is capped by construction, independent of any
    judgment about the sender. A flood degrades to "our telemetry
    buffer rotates faster", never "the shared host's disk fills".
    Returns files deleted."""
    root = _data_dir()
    if not root.is_dir():
        return 0
    # Partition = the ENV alone, for every env. Deletion drains the
    # largest partition's oldest files first — water-leveling — so the
    # fairness claim only holds if strangers cannot spread across
    # partitions. Both halves are now constructive: env values outside
    # the deployment-owned allowlist collapse into unknown/ at write
    # time, and WITHIN an env the runtime dimension carries no
    # partition weight — spoofing a known env and rotating runtime_id
    # mints nothing (previously keyed per env/runtime, 257 mintable
    # sub-partitions let a flood keep each just below the victim's and
    # the leveling loop drained the victim). What remains, stated
    # honestly: traffic claiming a known env IS that one partition —
    # indistinguishable from the legit sender without authentication,
    # so a spoofing flood rotates that env's data, ours included,
    # oldest first. The same collapse means NO isolation between
    # LEGITIMATE senders sharing an env: one high-volume sender rotates
    # its same-env peers' history out — accepted while unauthenticated.
    # Upgrade path for whoever hardens this next: once
    # DIAG_COLLECT_TOKEN is enabled labels stop being forgeable, and
    # the runtime dimension can safely regain partition weight — the
    # token is not just an escape hatch, it is the precondition that
    # makes two-level leveling sound. The hard guarantees stay the cap
    # itself and the global byte budget.
    partitions: dict[tuple, list[tuple[float, int, Path]]] = {}
    sizes: dict[tuple, int] = {}
    total = 0
    for path in root.rglob("*.jsonl"):
        try:
            stat = path.stat()
        except OSError:
            continue
        key = path.relative_to(root).parts[:1]
        partitions.setdefault(key, []).append((stat.st_mtime, stat.st_size, path))
        sizes[key] = sizes.get(key, 0) + stat.st_size
        total += stat.st_size
    cap = _max_data_bytes()
    if total <= cap:
        return 0
    target = int(cap * 0.9)  # free headroom so we don't re-enter per write
    for files in partitions.values():
        files.sort()  # oldest first within each partition
    deleted = 0
    while total > target and sizes:
        # Tie-break on file COUNT, then on the key, so the drain order is a
        # property of the data and not of `rglob`'s traversal order.
        #
        # `max(sizes, key=sizes.get)` returns whichever equal-sized partition
        # the dict happened to see first, which is the filesystem's business.
        # At the last deletion of a flood that is exactly the decision that
        # matters: with a 400-byte victim (one old file) tied against a
        # 400-byte flood remnant (two newer files), draining the victim leaves
        # 0 / 400 and draining the flood leaves 400 / 200. The second is what
        # water-leveling means, and it was a coin toss.
        #
        # The load-bearing property is DETERMINISM. The direction is a
        # heuristic and is stated as one: at equal size, a partition with more
        # files tends to have a smaller oldest file, so draining it takes a
        # smaller step and leaves the two partitions closer together — which is
        # what leveling means. It is not an anti-abuse property: a flooder that
        # consolidates into fewer, larger files wins every size tie against a
        # victim holding many small ones. Tightening that needs authentication
        # (see the DIAG_COLLECT_TOKEN note above), not a better tie-break.
        # The key itself only settles the remaining tie, and only so the
        # outcome stops depending on `rglob`.
        key = max(sizes, key=lambda k: (sizes[k], len(partitions[k]), k))
        files = partitions[key]
        if not files:
            sizes.pop(key, None)
            partitions.pop(key, None)
            continue
        _, size, path = files.pop(0)
        try:
            path.unlink()
        except OSError:
            continue
        total -= size
        sizes[key] -= size
        deleted += 1
    if deleted:
        logger.warning(
            f"[diag-collector] size cap: dropped {deleted} oldest files "
            f"to stay under {cap} bytes"
        )
    return deleted


# --- per-IP rate limiting (the public endpoint's actual defense) -----------

_RATE_WINDOW_S = 60.0
_RATE_MAX_REQUESTS = 120
_RATE_MAX_BYTES = 64 * 1024 * 1024  # wire bytes per IP per window
_RATE_MAX_IPS = 10_000
# Global (all-IPs) budget: the layer that still binds when an attacker
# rotates identities. Per-IP fairness is best-effort; this is the cap.
_GLOBAL_MAX_BYTES = 256 * 1024 * 1024

# Separate ledgers: request timestamps and byte entries. Settlement
# appends its OWN byte entry — the earlier fill-the-last-slot scheme
# crossed accounts when two same-IP requests interleaved
# (precheck A → precheck B → settle A landed in B's slot).
_rate_counts: dict[str, list[float]] = {}
_rate_bytes: dict[str, list[tuple[float, int]]] = {}
_global_window: list[tuple[float, int]] = []


def _client_ip(request: Request) -> str:
    """Rate-limit identity. X-Real-IP is honoured ONLY when
    DIAG_COLLECT_TRUST_REAL_IP=1 says our proxy overwrites it (see the
    module docstring's caddy block) — a client can send that header
    too, and trusting it by default would re-open the client-authored-
    identity hole (XFF's first hop) this design closed. Unflagged, the
    peer address is used even though behind a proxy that collapses to
    one global bucket: degraded fairness beats silent bypass."""
    if os.environ.get("DIAG_COLLECT_TRUST_REAL_IP", "") == "1":
        real_ip = request.headers.get("x-real-ip", "").strip()
        if real_ip:
            return real_ip
    return request.client.host if request.client else "unknown"


_E = TypeVar("_E")


def _prune(window: list[_E], now: float, ts: Callable[[_E], float]) -> list[_E]:
    """Drop entries older than the rate window. All THREE sliding-window
    ledgers (_rate_counts, _rate_bytes, _global_window) share this one
    rule — `ts` extracts the timestamp from an entry (a bare float in
    _rate_counts, the first tuple field in the byte ledgers) — so a
    window change cannot land in one ledger and miss the others."""
    return [e for e in window if now - ts(e) < _RATE_WINDOW_S]


def _rate_precheck(request: Request) -> str:
    """Request-count gate BEFORE reading the body. Byte budgets settle
    after the stream is read (Content-Length is client-authored)."""
    now = time.monotonic()
    ip = _client_ip(request)
    if len(_rate_counts) > _RATE_MAX_IPS:
        # Evict the ~10% least-recently-active IPs. clear() was an
        # attacker primitive: flood with fake identities, reset
        # EVERYONE's counters including your own.
        stale = sorted(
            _rate_counts.items(),
            key=lambda kv: kv[1][-1] if kv[1] else 0.0,
        )[: max(1, _RATE_MAX_IPS // 10)]
        for key, _ in stale:
            _rate_counts.pop(key, None)
            _rate_bytes.pop(key, None)
    counts = _prune(_rate_counts.get(ip, []), now, ts=lambda e: e)
    if len(counts) >= _RATE_MAX_REQUESTS:
        _rate_counts[ip] = counts
        raise HTTPException(status_code=429, detail="rate limited")
    counts.append(now)
    _rate_counts[ip] = counts
    return ip


def _rate_settle_bytes(ip: str, wire_bytes: int) -> None:
    """Charge ACTUAL streamed bytes; reject when either budget is
    blown. Post-consumption by necessity — but storage (the resource
    that matters, see the size cap) is only spent on accepted requests."""
    now = time.monotonic()
    global _global_window
    window = _prune(_rate_bytes.get(ip, []), now, ts=lambda e: e[0])
    window.append((now, wire_bytes))
    _rate_bytes[ip] = window
    _global_window = _prune(_global_window, now, ts=lambda e: e[0])
    _global_window.append((now, wire_bytes))
    if sum(b for _, b in window) > _RATE_MAX_BYTES:
        raise HTTPException(status_code=429, detail="rate limited")
    if sum(b for _, b in _global_window) > _GLOBAL_MAX_BYTES:
        raise HTTPException(status_code=429, detail="global budget exceeded")


def _require_auth(request: Request) -> None:
    token = os.environ.get("DIAG_COLLECT_TOKEN", "").strip()
    if not token:
        # Public mode — the default for the open-source telemetry
        # design. Defense is the rate limiter + size caps, not secrecy.
        return
    header = request.headers.get("authorization", "")
    # Constant-time comparison, over BYTES: compare_digest raises
    # TypeError on non-ASCII str (headers decode as latin-1, so any
    # >127 byte in the header would turn a bad token into a 500).
    if not hmac.compare_digest(
        header.encode("utf-8", errors="replace"),
        f"Bearer {token}".encode(),
    ):
        raise HTTPException(status_code=401, detail="bad token")


def _segment(value: str) -> str:
    """One path segment from untrusted sender metadata: whitelist chars,
    never empty, never a dot-name (path traversal is structurally
    impossible — no separators survive)."""
    cleaned = _SAFE_SEGMENT_RE.sub("_", str(value or ""))[:64].strip("._")
    return cleaned or "unknown"


# The env level is an ALLOWLIST — fairness lives here. The sender label
# vocabulary (staging + NARRA_SURFACE values) is a closed set WE define
# at deployment time, so narrowing the value domain is constructive:
# strangers cannot mint env partitions at all, they share unknown/.
_UNKNOWN_ENV = "unknown"
# "dev" = OUR dev cloud stack: both EC2 stacks bake the same image
# (NARRANEXUS_DEPLOYMENT_MODE=cloud), so prod keeps the "cloud" label
# and the dev stack sets NEXUS_DIAG_ENV=dev in its compose .env — one
# line on one host — to get its own STORAGE partition. The allowlist
# decides directories only; which HOST receives the logs is the
# discovery map's key set (the receiving-host side of the four-sided
# label contract, see .env.example): the prod collector's
# DIAG_COLLECT_CONFIG_JSON must carry a "dev" ingest entry, or the
# label silently routes to the "default" (prod) ingest — no warning
# fires on either side.
_DEFAULT_KNOWN_ENVS = "staging,cloud,local,desktop,dev,sprite"

# One warning per demoted label, not per record — silent collapse is how
# our own data would rot unnoticed if the sender vocabulary drifts
# (incident lessons #3/#4: the observability system's own failure must
# be detectable). Bounded so label rotation cannot flood the log.
_collapsed_warned: set[str] = set()
_COLLAPSED_WARNED_MAX = 100
# The memo resets on a time window: without it, 100 rotated garbage
# labels would fill the set once and the warning this exists FOR — our
# own vocabulary drifting — would never fire again. Rotation garbage
# now suppresses at most one window. (The check-then-add below is
# unlocked across to_thread workers; a duplicated line is accepted.)
_COLLAPSED_RESET_S = 3600.0
_collapsed_reset_at = 0.0


def _known_envs() -> frozenset[str]:
    """Allowlist entries pass the SAME normalization as incoming labels
    (_segment + lowercase): an ops typo — a stray space, 'Cloud' — must
    not silently collapse all traffic into unknown/."""
    raw = os.environ.get("DIAG_COLLECT_KNOWN_ENVS", "") or _DEFAULT_KNOWN_ENVS
    return frozenset(
        _segment(v).lower() for v in raw.split(",") if v.strip()
    )


# Below the env level the population bounds are INODE HYGIENE only —
# runtime/service names come from untrusted record fields, and without
# a cap rotation would mint unlimited directories (they carry no
# fairness weight: the size-cap partition key is the env alone).
_MAX_RUNTIME_DIRS = 256
_MAX_SERVICE_DIRS = 16
_OVERFLOW_SEGMENT = "overflow"


def _bounded_segment(
    parent: Path,
    raw: str,
    cap: int,
    pending: set[Path],
    dir_counts: dict[Path, int],
) -> str:
    """Sanitized segment, demoted to the overflow bucket once the parent
    already holds `cap` child directories (the overflow dir itself takes
    one slot once created, so real-name capacity is effectively cap-1).
    `pending` memoizes dirs known to exist or admitted earlier in the
    batch; `dir_counts` memoizes per-parent populations — both matter
    under flood, where an unmemoized saturated parent would pay an
    iterdir() per RECORD."""
    name = _segment(raw)
    child = parent / name
    if child in pending:
        return name
    if child.is_dir():
        pending.add(child)  # skip the stat for subsequent records
        return name
    count = dir_counts.get(parent)
    if count is None:
        try:
            count = (
                sum(1 for p in parent.iterdir() if p.is_dir())
                if parent.is_dir()
                else 0
            )
        except OSError:
            count = 0
        dir_counts[parent] = count
    if count >= cap:
        return _OVERFLOW_SEGMENT
    dir_counts[parent] = count + 1
    pending.add(child)
    return name


def _process_batch(raw: bytes, gzipped: bool) -> int:
    """Decompress, parse, route, and write one batch. Runs OFF the
    event loop (asyncio.to_thread): everything here is CPU- or disk-
    bound — a 32MB inflate, ~100k json.loads on a full batch, stats
    for directory admission, the appends themselves. Any of it inline
    would stall every other sender past their 2s send timeout, which
    on the sender side means dropped batches — the platform must not
    be the interruption source. (Round-1 fixed this for the writes;
    the parse stage grew its own I/O since, so the whole pipeline
    moves off-loop together.) Returns accepted record count."""
    if gzipped:
        # (plaintext needs no second cap: the wire cap already bounds
        # it below _MAX_BODY_BYTES — a dead branch here would be a
        # kept-just-in-case path, rule #2)
        raw = _bounded_decompress(raw)
    known = _known_envs()
    # Per-batch hoists: _data_dir() (env read + Path build) and the
    # collapse-warning window reset both run ONCE here, not per record
    # — a full batch can carry ~100k records.
    data_dir = _data_dir()
    global _collapsed_reset_at
    now_mono = time.monotonic()
    if now_mono >= _collapsed_reset_at and _collapsed_warned:
        _collapsed_warned.clear()
        _collapsed_reset_at = now_mono + _COLLAPSED_RESET_S
    accepted = 0
    files: dict[Path, list[str]] = {}
    pending_dirs: set[Path] = set()
    dir_counts: dict[Path, int] = {}
    # Partition by UTC date — records carry UTC ts, and a local-date
    # filename would disagree with its own contents around midnight.
    day = datetime.now(timezone.utc).date()
    for line in raw.decode("utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except ValueError:
            continue  # one broken line must not sink the batch
        env_name = _segment(record.get("env", "")).lower()
        if env_name not in known:
            if (
                env_name not in _collapsed_warned
                and len(_collapsed_warned) < _COLLAPSED_WARNED_MAX
            ):
                if not _collapsed_warned:
                    # The window opens when the memo receives its FIRST
                    # entry — a fixed cadence let a label warned just
                    # before the boundary re-warn seconds later.
                    _collapsed_reset_at = now_mono + _COLLAPSED_RESET_S
                _collapsed_warned.add(env_name)
                logger.warning(
                    f"[diag-collector] env label {env_name!r} not in "
                    f"DIAG_COLLECT_KNOWN_ENVS; storing under unknown/ "
                    f"(the partition the size cap drains first)"
                )
            env_name = _UNKNOWN_ENV
        env_dir = data_dir / env_name
        runtime_dir = env_dir / _bounded_segment(
            env_dir, record.get("runtime_id", ""), _MAX_RUNTIME_DIRS,
            pending_dirs, dir_counts,
        )
        target = (
            runtime_dir
            / _bounded_segment(
                runtime_dir, record.get("service", ""), _MAX_SERVICE_DIRS,
                pending_dirs, dir_counts,
            )
            / f"{day:%Y-%m-%d}.jsonl"
        )
        record["received_at"] = datetime.now(timezone.utc).isoformat()
        files.setdefault(target, []).append(
            json.dumps(record, ensure_ascii=False)
        )
        accepted += 1

    for target, lines in files.items():
        payload = "\n".join(lines) + "\n"
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            fh = target.open("a", encoding="utf-8")
        except OSError:
            # The retention sweep's empty-dir rmdir can race the gap
            # between mkdir and open; one retry closes the window
            # instead of turning the batch into a 500 (which the
            # sender would count against its breaker). The write itself
            # stays OUTSIDE the retry: re-appending after a mid-stream
            # failure (ENOSPC/EIO) would leave "half + full" duplicate
            # content in the file.
            target.parent.mkdir(parents=True, exist_ok=True)
            fh = target.open("a", encoding="utf-8")
        with fh:
            fh.write(payload)
    return accepted


@app.post("/v1/ingest")
async def ingest(request: Request):
    _require_auth(request)
    ip = _rate_precheck(request)
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > _MAX_WIRE_BYTES:
            raise HTTPException(status_code=413, detail="request too large")
        chunks.append(chunk)
    _rate_settle_bytes(ip, total)
    gzipped = request.headers.get("content-encoding", "").lower() == "gzip"
    accepted = await asyncio.to_thread(_process_batch, b"".join(chunks), gzipped)

    global _size_check_appended
    _size_check_appended += total
    if _size_check_appended >= _SIZE_CHECK_EVERY_BYTES:
        _size_check_appended = 0
        await asyncio.to_thread(enforce_size_cap)

    return {"ok": True, "accepted": accepted}


@app.get("/healthz")
async def healthz():
    return {"ok": True, "data_dir": str(_data_dir())}


@app.get("/v1/config")
async def discovery_config():
    """The discovery document senders resolve their ingest URL from
    (see _ship.py). Served verbatim from env so URL rotation is an env
    change on the collector, with zero client releases. Senders pick
    WHICH collector to ask by their env label — prod is the built-in
    default, staging sandboxes ask the dev collector (run.sh) — so
    every collector a label routes to must serve this document, or
    those senders 404 and back off. Public and unauthenticated on
    purpose: it contains only URLs."""
    raw = os.environ.get("DIAG_COLLECT_CONFIG_JSON", "").strip()
    if not raw:
        raise HTTPException(status_code=404, detail="no discovery config set")
    try:
        document = json.loads(raw)
    except ValueError:
        raise HTTPException(
            status_code=500, detail="DIAG_COLLECT_CONFIG_JSON is not valid JSON"
        ) from None
    # Shape check here, loudly — a scalar/array served as a discovery
    # document would fail SILENTLY at every sender (their defensive
    # parse just skips resolution), which reads as "telemetry is dark"
    # with no error anywhere.
    if not isinstance(document, dict) or not isinstance(
        document.get("ingest"), dict
    ):
        raise HTTPException(
            status_code=500,
            detail='DIAG_COLLECT_CONFIG_JSON must be {"ingest": {...}}',
        )
    return document


def sweep_retention() -> int:
    """Delete .jsonl files older than the retention window. Returns count."""
    cutoff = time.time() - _retention_days() * 86400
    deleted = 0
    root = _data_dir()
    if not root.is_dir():
        return 0
    for path in root.rglob("*.jsonl"):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
                deleted += 1
        except OSError:
            continue
    # Bottom-up empty-dir cleanup: unlink() never removes directories,
    # and expired/rotated identities would otherwise leave an ever-
    # growing dir tree that every rglob scan here and in
    # enforce_size_cap pays for. Children sort after their parents, so
    # the reversed walk empties leaves first; rmdir refuses non-empty
    # dirs, which is exactly the filter — and the root itself is not in
    # its own rglob, so the data dir always survives.
    for path in sorted(
        (p for p in root.rglob("*") if p.is_dir()), reverse=True
    ):
        try:
            path.rmdir()
        except OSError:
            continue
    return deleted


async def _retention_loop() -> None:
    while True:
        deleted = await asyncio.to_thread(sweep_retention)
        if deleted:
            logger.info(f"[diag-collector] retention: dropped {deleted} files")
        await asyncio.to_thread(enforce_size_cap)
        await asyncio.sleep(24 * 3600)


def main() -> None:
    import uvicorn

    if not os.environ.get("DIAG_COLLECT_TOKEN", "").strip():
        logger.info(
            "[diag-collector] running PUBLIC (no token) — the intended "
            "open-source telemetry mode; abuse control = rate limits + caps"
        )
    port = int(os.environ.get("DIAG_COLLECT_PORT", "9880"))
    uvicorn.run(app, host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()

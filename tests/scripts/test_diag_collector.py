"""
@file_name: test_diag_collector.py
@author:
@date: 2026-08-10
@description: Diagnostic log collector — auth, gzip ingest, partitioned
JSONL storage, path-segment sanitization, retention sweep.
"""
from __future__ import annotations

import gzip
import importlib.util
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from httpx import ASGITransport

_SPEC = importlib.util.spec_from_file_location(
    "diag_collector",
    Path(__file__).resolve().parents[2] / "scripts/diag_collector/collector.py",
)
collector = importlib.util.module_from_spec(_SPEC)
assert _SPEC and _SPEC.loader
_SPEC.loader.exec_module(collector)


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("DIAG_COLLECT_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DIAG_COLLECT_TOKEN", "coll-tok")
    monkeypatch.setattr(collector, "_rate_counts", {})
    monkeypatch.setattr(collector, "_rate_bytes", {})
    return tmp_path


# "staging" is in the collector's default known-env allowlist — tests
# should exercise the real label vocabulary (staging/cloud/local/desktop).
def _lines(*messages, env_label="staging", runtime="rt_x", service="backend"):
    return "\n".join(
        json.dumps(
            {
                "ts": "2026-08-10T12:00:00+00:00",
                "level": "INFO",
                "service": service,
                "env": env_label,
                "runtime_id": runtime,
                "message": m,
            }
        )
        for m in messages
    )


async def _post(body: bytes, *, token="coll-tok", gzipped=True):
    headers = {}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    if gzipped:
        headers["Content-Encoding"] = "gzip"
    transport = ASGITransport(app=collector.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        return await c.post("/v1/ingest", content=body, headers=headers)


async def test_ingest_writes_partitioned_jsonl(env):
    resp = await _post(gzip.compress(_lines("a", "b").encode()))
    assert resp.status_code == 200
    assert resp.json()["accepted"] == 2
    target = (
        env
        / "staging"
        / "rt_x"
        / "backend"
        / f"{datetime.now(timezone.utc).date():%Y-%m-%d}.jsonl"
    )
    assert target.is_file()
    rows = [json.loads(ln) for ln in target.read_text().splitlines()]
    assert [r["message"] for r in rows] == ["a", "b"]
    assert all("received_at" in r for r in rows)


async def test_ingest_appends_across_batches(env):
    await _post(gzip.compress(_lines("one").encode()))
    await _post(gzip.compress(_lines("two").encode()))
    target = next(env.rglob("*.jsonl"))
    assert len(target.read_text().splitlines()) == 2


async def test_bad_token_rejected(env):
    resp = await _post(gzip.compress(_lines("x").encode()), token="wrong")
    assert resp.status_code == 401


async def test_plain_body_accepted(env):
    resp = await _post(_lines("plain").encode(), gzipped=False)
    assert resp.status_code == 200


async def test_bad_gzip_rejected(env):
    resp = await _post(b"not-gzip-at-all")
    assert resp.status_code == 400


async def test_broken_line_skipped_not_fatal(env):
    body = _lines("good") + "\n{broken json\n" + _lines("also good")
    resp = await _post(gzip.compress(body.encode()))
    assert resp.json()["accepted"] == 2


async def test_path_segments_sanitized(env):
    body = _lines("evil", env_label="../../etc", runtime="a/b", service="")
    resp = await _post(gzip.compress(body.encode()))
    assert resp.status_code == 200
    written = list(env.rglob("*.jsonl"))
    assert len(written) == 1
    rel = written[0].relative_to(env)
    # No separator survives sanitization; empty segments become "unknown";
    # and a sanitized-but-unlisted env collapses into the unknown bucket.
    assert ".." not in rel.parts
    assert rel.parts[0] == "unknown"
    assert "unknown" in rel.parts


async def test_retention_sweep_deletes_old_files(env):
    await _post(gzip.compress(_lines("keep").encode()))
    old = env / "e" / "r" / "s" / "2020-01-01.jsonl"
    old.parent.mkdir(parents=True)
    old.write_text("{}\n")
    stale = time.time() - 90 * 86400
    os.utime(old, (stale, stale))
    deleted = collector.sweep_retention()
    assert deleted == 1
    assert not old.exists()
    assert list(env.rglob("*.jsonl"))  # fresh file survived


async def test_gzip_bomb_bounded_413(env, monkeypatch):
    """The cap fires WHILE inflating — a small request must never fully
    materialize past the limit."""
    monkeypatch.setattr(collector, "_MAX_BODY_BYTES", 1024)
    bomb = gzip.compress(b"0" * 100_000)  # tiny wire, big inflate
    resp = await _post(bomb)
    assert resp.status_code == 413


async def test_truncated_gzip_is_400_not_500(env):
    truncated = gzip.compress(_lines("x").encode())[:-5]  # EOFError path
    resp = await _post(truncated)
    assert resp.status_code == 400


async def test_no_token_is_public_mode(monkeypatch, tmp_path):
    """Telemetry redesign (2026-08-11): the collector is a PUBLIC
    endpoint by default — open-source senders can't hold a secret, so
    defense is rate limiting + size caps, not tokens."""
    monkeypatch.setenv("DIAG_COLLECT_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("DIAG_COLLECT_TOKEN", raising=False)
    resp = await _post(gzip.compress(_lines("x").encode()), token=None)
    assert resp.status_code == 200


async def test_set_token_is_still_enforced(env):
    # Private-deployment knob: once configured, it is required.
    resp = await _post(gzip.compress(_lines("x").encode()), token="wrong")
    assert resp.status_code == 401


async def test_rate_limit_429(env, monkeypatch):
    monkeypatch.setattr(collector, "_RATE_MAX_REQUESTS", 2)
    monkeypatch.setattr(collector, "_rate_counts", {})
    monkeypatch.setattr(collector, "_rate_bytes", {})
    body = gzip.compress(_lines("x").encode())
    assert (await _post(body)).status_code == 200
    assert (await _post(body)).status_code == 200
    assert (await _post(body)).status_code == 429


async def test_discovery_config_served_verbatim(env, monkeypatch):
    doc = {"ingest": {"default": "https://a/v1/ingest", "staging": "https://b/v1/ingest"}}
    monkeypatch.setenv("DIAG_COLLECT_CONFIG_JSON", json.dumps(doc))
    transport = ASGITransport(app=collector.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        resp = await c.get("/v1/config")
    assert resp.status_code == 200
    assert resp.json() == doc


async def test_discovery_config_404_when_unset(env, monkeypatch):
    monkeypatch.delenv("DIAG_COLLECT_CONFIG_JSON", raising=False)
    transport = ASGITransport(app=collector.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        resp = await c.get("/v1/config")
    assert resp.status_code == 404


async def test_wire_size_cap_precedes_buffering(env, monkeypatch):
    monkeypatch.setattr(collector, "_MAX_WIRE_BYTES", 1024)
    resp = await _post(b"0" * 10_000, gzipped=False)
    assert resp.status_code == 413


async def test_non_ascii_auth_header_is_401_not_500(env):
    transport = ASGITransport(app=collector.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        resp = await c.post(
            "/v1/ingest",
            content=b"{}",
            # bytes value: httpx's own header validation would refuse the
            # non-ASCII str before it ever reached the server
            headers={"Authorization": "Bearer café-token".encode("latin-1")},
        )
    assert resp.status_code == 401


async def test_rotating_identity_headers_cannot_mint_buckets(env, monkeypatch):
    """Without DIAG_COLLECT_TRUST_REAL_IP, BOTH identity headers are
    ignored (X-Real-IP is client-sendable too) — rotating them must not
    mint fresh buckets; everyone shares the peer-address bucket."""
    monkeypatch.delenv("DIAG_COLLECT_TRUST_REAL_IP", raising=False)
    monkeypatch.setattr(collector, "_RATE_MAX_REQUESTS", 2)
    body = gzip.compress(_lines("x").encode())
    transport = ASGITransport(app=collector.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        for i in range(3):
            resp = await c.post(
                "/v1/ingest",
                content=body,
                headers={
                    "Authorization": "Bearer coll-tok",
                    "Content-Encoding": "gzip",
                    "X-Forwarded-For": f"10.0.0.{i}",  # attacker-rotated
                    "X-Real-IP": f"10.1.0.{i}",  # attacker-rotated too
                },
            )
    assert resp.status_code == 429


async def test_trusted_hop_used_only_when_flagged(env, monkeypatch):
    """With the flag on (deployment declares caddy overwrites the
    header), X-Real-IP keys the buckets — distinct real clients get
    distinct budgets."""
    monkeypatch.setenv("DIAG_COLLECT_TRUST_REAL_IP", "1")
    monkeypatch.setattr(collector, "_RATE_MAX_REQUESTS", 1)
    body = gzip.compress(_lines("x").encode())
    transport = ASGITransport(app=collector.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        r1 = await c.post(
            "/v1/ingest", content=body,
            headers={"Authorization": "Bearer coll-tok",
                     "Content-Encoding": "gzip", "X-Real-IP": "203.0.113.7"},
        )
        r2 = await c.post(
            "/v1/ingest", content=body,
            headers={"Authorization": "Bearer coll-tok",
                     "Content-Encoding": "gzip", "X-Real-IP": "203.0.113.8"},
        )
        r3 = await c.post(
            "/v1/ingest", content=body,
            headers={"Authorization": "Bearer coll-tok",
                     "Content-Encoding": "gzip", "X-Real-IP": "203.0.113.7"},
        )
    assert (r1.status_code, r2.status_code, r3.status_code) == (200, 200, 429)


async def test_byte_budget_uses_actual_stream_not_header(env, monkeypatch):
    monkeypatch.setattr(collector, "_RATE_MAX_BYTES", 100)
    monkeypatch.setattr(collector, "_rate_counts", {})
    monkeypatch.setattr(collector, "_rate_bytes", {})
    big = _lines("y" * 400).encode()  # plain, well over 100 bytes actual
    transport = ASGITransport(app=collector.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        resp = await c.post(
            "/v1/ingest",
            content=big,
            headers={
                "Authorization": "Bearer coll-tok",
                # lying header must not matter — settlement reads the stream
                "Content-Length": str(len(big)),
            },
        )
    assert resp.status_code == 429


async def test_size_cap_deletes_oldest_first(env, monkeypatch):
    d = env / "e" / "r" / "s"
    d.mkdir(parents=True)
    old = d / "2026-01-01.jsonl"
    new = d / "2026-06-01.jsonl"
    old.write_text("o" * 600)
    new.write_text("n" * 600)
    os.utime(old, (1000, 1000))
    monkeypatch.setenv("DIAG_COLLECT_MAX_DATA_GB", str(1000 / 1024**3))  # 1000 bytes
    deleted = collector.enforce_size_cap()
    assert deleted == 1
    assert not old.exists() and new.exists()


async def test_size_cap_noop_under_limit(env):
    (env / "a.jsonl").write_text("x")
    assert collector.enforce_size_cap() == 0


async def test_unlisted_env_collapses_into_unknown(env):
    """Env values outside DIAG_COLLECT_KNOWN_ENVS are collapsed at
    write time. The value domain is deployment-owned — 'stranger
    traffic piles into ONE partition' is a property of construction,
    not a population bound an attacker can spread under."""
    await _post(gzip.compress(_lines("a", env_label="rot-1").encode()))
    await _post(gzip.compress(_lines("b", env_label="rot-2").encode()))
    assert not (env / "rot-1").exists() and not (env / "rot-2").exists()
    rows = [
        json.loads(ln)
        for f in (env / "unknown").rglob("*.jsonl")
        for ln in f.read_text().splitlines()
    ]
    assert {r["env"] for r in rows} == {"rot-1", "rot-2"}  # attribution inline


async def test_known_envs_allowlist_is_env_tunable(env, monkeypatch):
    monkeypatch.setenv("DIAG_COLLECT_KNOWN_ENVS", "prodx")
    await _post(gzip.compress(_lines("a", env_label="prodx").encode()))
    assert (env / "prodx").is_dir()
    await _post(gzip.compress(_lines("b", env_label="staging").encode()))
    assert not (env / "staging").exists()  # default list fully replaced


async def test_allowlist_entries_are_normalized(env, monkeypatch):
    """Allowlist entries pass the same normalization as incoming labels
    (_segment + lowercase): an ops typo like ' Cloud ' must not silently
    collapse ALL traffic into unknown/ — the partition that drains first."""
    monkeypatch.setenv("DIAG_COLLECT_KNOWN_ENVS", " Cloud , staging ")
    await _post(gzip.compress(_lines("a", env_label="cloud").encode()))
    assert (env / "cloud").is_dir()
    await _post(gzip.compress(_lines("b", env_label="Staging").encode()))
    assert (env / "staging").is_dir()  # case-insensitive both directions


async def test_dev_label_has_own_partition(env):
    """Plan A: our dev cloud stack sets NEXUS_DIAG_ENV=dev (both EC2
    stacks bake the same cloud image, so deployment mode alone cannot
    tell them apart) — "dev" is first-class vocabulary, not a stranger."""
    resp = await _post(gzip.compress(_lines("x", env_label="dev").encode()))
    assert resp.status_code == 200
    assert (env / "dev").is_dir()
    assert not (env / "unknown").exists()


async def test_sprite_label_has_own_partition(env):
    """Non-staging manyfold sandboxes label themselves "sprite"
    (run.sh) — first-class vocabulary with its own partition, so a
    full-level sandbox fleet rotates itself under the size cap instead
    of crowding prod's history."""
    resp = await _post(gzip.compress(_lines("x", env_label="sprite").encode()))
    assert resp.status_code == 200
    assert (env / "sprite").is_dir()
    assert not (env / "unknown").exists()


async def test_collapse_warning_memo_resets_after_window(env, monkeypatch):
    """A hundred rotated garbage labels must not permanently silence
    the warning this memo exists FOR — our own vocabulary drifting.
    After the reset window the memo clears and the label warns again."""
    warnings: list[str] = []
    monkeypatch.setattr(
        collector, "logger", SimpleNamespace(
            warning=lambda m: warnings.append(m),
            info=lambda m: None,
        )
    )
    monkeypatch.setattr(collector, "_collapsed_warned", set())
    monkeypatch.setattr(collector, "_collapsed_reset_at", 0.0)
    await _post(gzip.compress(_lines("a", env_label="drift-env").encode()))
    await _post(gzip.compress(_lines("b", env_label="drift-env").encode()))
    assert sum("drift-env" in w for w in warnings) == 1
    monkeypatch.setattr(collector, "_collapsed_reset_at", 0.0)  # window expiry
    await _post(gzip.compress(_lines("c", env_label="drift-env").encode()))
    assert sum("drift-env" in w for w in warnings) == 2


async def test_collapse_to_unknown_warns_once_per_label(env, monkeypatch):
    """Silent collapse is how our own data would rot unnoticed if the
    label vocabulary drifts (incident lessons #3/#4): the collector must
    say WHICH label it demoted, once per label, not per record."""
    warnings: list[str] = []
    monkeypatch.setattr(
        collector, "logger", SimpleNamespace(
            warning=lambda m: warnings.append(m),
            info=lambda m: None,
        )
    )
    monkeypatch.setattr(collector, "_collapsed_warned", set())
    await _post(gzip.compress(_lines("a", "b", env_label="typo-env").encode()))
    await _post(gzip.compress(_lines("c", env_label="typo-env").encode()))
    assert sum("typo-env" in w for w in warnings) == 1


async def test_runtime_rotation_within_known_env_is_one_partition(env, monkeypatch):
    """Partition key is the ENV alone: spoofing a known env and rotating
    runtime_id must not mint 257 small partitions to water-level the
    victim — everything claiming env X IS partition X, so the rotation
    buys nothing and the flooded env drains as one block."""
    ours = env / "cloud" / "rt_ours" / "s"
    ours.mkdir(parents=True)
    keep = ours / "2026-01-01.jsonl"
    keep.write_text("o" * 400)
    os.utime(keep, (500, 500))  # older than everything in the flood
    for i in range(4):
        d = env / "staging" / f"rt_{i}" / "s"
        d.mkdir(parents=True)
        f = d / "2026-02-01.jsonl"
        f.write_text("f" * 300)  # each individually smaller than ours
        os.utime(f, (1000 + i, 1000 + i))
    # 1600 total, cap 900: leveling must take three flood files (1200 →
    # 300, staying above ours at every step) and never touch `keep`.
    monkeypatch.setenv("DIAG_COLLECT_MAX_DATA_GB", str(900 / 1024**3))
    deleted = collector.enforce_size_cap()
    assert keep.exists()
    assert deleted >= 3


async def test_write_failure_is_not_retried(env, monkeypatch):
    """The vanished-dir retry covers ONLY mkdir→open: a write() that
    fails mid-stream (ENOSPC/EIO) must not re-append the whole payload
    — that would leave 'half + full' duplicate content in the file."""
    writes = {"n": 0}
    real_open = Path.open

    class _FailingFile:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def write(self, payload):
            writes["n"] += 1
            raise OSError("disk full mid-write")

    def fake_open(self, *args, **kwargs):
        if self.suffix == ".jsonl":
            return _FailingFile()
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", fake_open)
    with pytest.raises(OSError):
        await _post(gzip.compress(_lines("x").encode()))
    assert writes["n"] == 1  # write is outside the retry scope


async def test_size_cap_treats_unknown_subtree_as_one_partition(env, monkeypatch):
    """Water-leveling countermeasure: rotation spread inside unknown/
    cannot keep each flood dir just below the known sender's partition,
    because the WHOLE unknown/ subtree is one partition — its dirs sum,
    become the largest, and drain first."""
    ours = env / "staging" / "rt_ours" / "s"
    ours.mkdir(parents=True)
    keep = ours / "2026-01-01.jsonl"
    keep.write_text("o" * 400)
    os.utime(keep, (500, 500))  # older than everything in the flood
    for i in range(4):
        d = env / "unknown" / f"rt_{i}" / "s"
        d.mkdir(parents=True)
        f = d / "2026-02-01.jsonl"
        f.write_text("f" * 200)  # each individually SMALLER than ours
        os.utime(f, (1000 + i, 1000 + i))
    monkeypatch.setenv("DIAG_COLLECT_MAX_DATA_GB", str(700 / 1024**3))
    deleted = collector.enforce_size_cap()
    assert keep.exists()
    assert deleted >= 3


async def test_write_retries_when_dir_vanishes(env, monkeypatch):
    """The retention sweep's empty-dir rmdir can race the mkdir→open
    gap in the write path; one retry keeps the batch from becoming a
    500 (which the sender would count against its breaker)."""
    real_open = Path.open
    calls = {"n": 0}

    def flaky_open(self, *args, **kwargs):
        if self.suffix == ".jsonl" and calls["n"] == 0:
            calls["n"] += 1
            raise FileNotFoundError("dir swept between mkdir and open")
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", flaky_open)
    resp = await _post(gzip.compress(_lines("x").encode()))
    assert resp.status_code == 200
    assert calls["n"] == 1
    assert len(list(env.rglob("*.jsonl"))) == 1


async def test_rotated_identities_collapse_into_overflow_dirs(env, monkeypatch):
    """Below the env level the population bound is INODE HYGIENE (the
    fairness property lives in the env allowlist): runtime/service
    names come from untrusted record fields, and without a cap rotation
    would mint unlimited directories. Past the cap, new names collapse
    into overflow/. Attribution survives in record content."""
    monkeypatch.setattr(collector, "_MAX_RUNTIME_DIRS", 2)
    for i in range(4):
        resp = await _post(gzip.compress(_lines("x", runtime=f"rt_{i}").encode()))
        assert resp.status_code == 200
    base = env / "staging"
    assert (base / "rt_0").is_dir() and (base / "rt_1").is_dir()
    assert not (base / "rt_2").exists() and not (base / "rt_3").exists()
    rows = [
        json.loads(ln)
        for f in (base / "overflow").rglob("*.jsonl")
        for ln in f.read_text().splitlines()
    ]
    assert {r["runtime_id"] for r in rows} == {"rt_2", "rt_3"}


async def test_retention_sweep_removes_empty_dirs(env):
    """unlink() never removes directories: without bottom-up cleanup,
    expired identities leave an ever-growing dir tree that every rglob
    scan pays for. The data root itself must survive."""
    d = env / "e" / "r" / "s"
    d.mkdir(parents=True)
    f = d / "2026-01-01.jsonl"
    f.write_text("x")
    os.utime(f, (1000, 1000))
    assert collector.sweep_retention() == 1
    assert not (env / "e").exists()
    assert env.is_dir()


async def test_size_cap_drains_largest_partition_first(env, monkeypatch):
    """A flood squeezes ITSELF out: deletion drains the biggest
    env/runtime partition's oldest files before touching known
    senders' data."""
    flood = env / "unknown" / "rt_flood" / "s"
    ours = env / "manyfold-prod" / "rt_ours" / "s"
    flood.mkdir(parents=True)
    ours.mkdir(parents=True)
    for i in range(3):
        f = flood / f"2026-0{i+1}-01.jsonl"
        f.write_text("f" * 300)
        os.utime(f, (1000 + i, 1000 + i))
    keep = ours / "2026-01-01.jsonl"
    keep.write_text("o" * 100)
    os.utime(keep, (500, 500))  # OLDER than everything in the flood
    monkeypatch.setenv("DIAG_COLLECT_MAX_DATA_GB", str(600 / 1024**3))
    deleted = collector.enforce_size_cap()
    # Global-oldest deletion would have taken `keep` first; partition-
    # aware deletion drains the flood instead.
    assert keep.exists()
    assert deleted >= 2

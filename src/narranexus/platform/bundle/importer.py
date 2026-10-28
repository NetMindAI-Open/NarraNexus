"""
@file_name: importer.py
@author: NetMind.AI
@date: 2026-05-08
@description: Bundle import pipeline — preflight + confirm

Pipeline (PRD §8.4):
1. Form check (size, zip, manifest)
2. Security (zip-bomb, traversal, sha256)
3. Compatibility (schema_version)
4. ID rewrite (5 layers: kind regex + structured field map + free-text regex)
5. Name suffix dedupe ("Trading Bot (1)")
6. user_id injection
7. Transactional write
8. Summary
"""

import asyncio
import io
import json
import os
import re
import shutil
import tarfile
import tempfile
import uuid
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

from narranexus.platform.utils.db.schema_registry import TABLES
from typing import Any, Dict, List, Optional
from loguru import logger

from narranexus.platform.bundle.team_bulletin_transfer import (
    write_imported_bulletin,
)

from narranexus.platform.utils.db.db_factory import get_db_client
from narranexus.platform.utils.db.dialect_errors import is_unique_violation
from narranexus.platform.utils.deployment_mode import is_cloud_mode
from narranexus.platform.utils.url_safety import is_obviously_non_public_url
from narranexus.platform.utils.file_safety import (
    sanitize_filename,
    validate_zip_member_path,
)
from narranexus.platform.schema.entity_schema import (
    AGENT_TEXT_MAX_LENGTH,
    normalize_agent_text,
)
from .id_field_map import STRUCTURED_ID_FIELDS, gen_new_id
from .channel_credential_tables import bundle_rows as _credential_bundle_rows
from .id_schema import build_all_id_regex, ID_KINDS
from .security import (
    extract_zip_safely,
    file_sha256,
    bytes_sha256,
    MAX_BUNDLE_BYTES,
)


# Preflight session TTL — older sessions are pruned every preflight call.
PREFLIGHT_TTL_HOURS = 6


def _loads_maybe(value: Any, default: Any) -> Any:
    """Bundle rows store list/dict columns as JSON STRINGS ('[]', '{}').
    Decode them before handing values to a pydantic model; pass through
    values that are already structured (newer in-memory paths) and fall
    back to `default` on empty/garbage. The social-entities path is the
    one importer branch that reconstructs a model instead of inserting a
    raw row (its destination moved to the unified memory store), so it is
    the one place that needs this (2026-06-11 bug: every legacy bundle
    with social entities failed pydantic validation)."""
    if value is None or value == "":
        return default
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (ValueError, TypeError):
            return default
        return parsed if isinstance(parsed, type(default)) else default
    return value if isinstance(value, type(default)) else default


def _clamp_agent_text(value: Optional[str]) -> tuple[Optional[str], bool]:
    """Clamp an agent name/description to AGENT_TEXT_MAX_LENGTH.

    Returns (clamped_value, was_trimmed). None/empty pass through untouched.
    Used at import time because raw db.insert bypasses the Agent model's
    length validation — see the call site for why an unclamped value would
    strand the agent as insertable-but-unreadable.
    """
    if not value or len(value) <= AGENT_TEXT_MAX_LENGTH:
        return value, False
    return value[:AGENT_TEXT_MAX_LENGTH], True


def _sanitize_for_schema(
    table: str, row: Dict[str, Any], dropped: Dict[str, int]
) -> Dict[str, Any]:
    """Drop columns the CURRENT schema no longer has.

    Old bundles legitimately carry columns that later schema versions
    removed — e.g. `narratives.embedding_updated_at`, dropped in the
    unified-memory refactor (v1.7.16). The live DB was migrated; a bundle
    is the same data arriving through the import path, so it gets the
    same tolerance: unknown columns are stripped and counted (surfaced in
    the import summary), never inserted blind (2026-06-11 bug: a v1.3.4
    bundle aborted mid-import on the first narratives row).
    """
    tdef = TABLES.get(table)
    if tdef is None:
        return row
    known = {c.name for c in tdef.columns}
    clean = {k: v for k, v in row.items() if k in known}
    for k in row.keys() - known:
        key = f"{table}.{k}"
        dropped[key] = dropped.get(key, 0) + 1
    return clean


async def _rollback_partial_import(db, id_map: Dict[str, str]) -> Dict[str, int]:
    """Best-effort compensating cleanup after a mid-import failure.

    confirm() is not transactional (the backends expose no cross-table
    transaction), so a failure used to strand orphan teams/agents
    (2026-06-11: six orphan 'Financial Morning Briefing' teams from
    repeated failed imports). All new IDs are minted upfront in id_map,
    which makes compensation tractable: sweep every registered table
    that carries an agent_id column for the NEW agent ids, then the
    team/bus tables by their NEW ids. Per-table failures are logged and
    skipped — rollback must never mask the original error.

    Returns {table: rows_deleted} for the audit log. Skill pack files
    are intentionally left in place (re-import overwrites them; deleting
    shared skills could break other agents).
    """
    new_ids = set(id_map.values())
    new_agent_ids = [i for i in new_ids if i.startswith("agent_")]
    new_team_ids = [i for i in new_ids if i.startswith("team_")]
    new_channel_ids = [i for i in new_ids if i.startswith("buschan_") or i.startswith("channel_")]

    deleted: Dict[str, int] = {}

    async def _del(table: str, field: str, value: str) -> None:
        try:
            n = await db.delete(table, {field: value})
            if n:
                deleted[table] = deleted.get(table, 0) + n
        except Exception as de:  # noqa: BLE001 — never mask the original error
            logger.warning(f"bundle_import.rollback.skip table={table} {field}={value}: {de}")

    # Every registered table with an agent_id column, swept per new agent.
    agent_tables = [name for name, tdef in TABLES.items()
                    if any(c.name == "agent_id" for c in tdef.columns)]
    for aid in new_agent_ids:
        for t in agent_tables:
            await _del(t, "agent_id", aid)
    for tid in new_team_ids:
        await _del("team_work_items", "team_id", tid)
        # The bulletin too. The agent_tables sweep above only covers tables with
        # an agent_id column, and team_bulletin_entries has team_id/author_id —
        # so rows written by write_imported_bulletin survived a rollback keyed on
        # a team_id deleted on the next line: unreachable by every query path,
        # which is exactly the orphan `_wipe_team_data` argues against.
        await _del("team_bulletin_entries", "team_id", tid)
        await _del("team_members", "team_id", tid)
        await _del("teams", "team_id", tid)
    for cid in new_channel_ids:
        await _del("bus_channel_members", "channel_id", cid)
        await _del("bus_messages", "channel_id", cid)
        await _del("bus_channels", "channel_id", cid)
    if deleted:
        logger.info(
            "bundle_import.rollback.done "
            + " ".join(f"{k}={v}" for k, v in sorted(deleted.items()))
        )
    return deleted


async def _cleanup_stale_preflights(db) -> None:
    """Delete preflight rows older than PREFLIGHT_TTL_HOURS + their work_dirs.
    Called inline from preflight() — cheap; expected row count is tiny."""
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=PREFLIGHT_TTL_HOURS)).strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    rows = await db.execute(
        "SELECT token, work_dir FROM bundle_preflight_sessions WHERE created_at < %s",
        params=(cutoff,),
        fetch=True,
    )
    for r in rows or []:
        wd = r.get("work_dir")
        if wd:
            try:
                shutil.rmtree(wd, ignore_errors=True)
            except Exception:
                pass
    if rows:
        await db.execute(
            "DELETE FROM bundle_preflight_sessions WHERE created_at < %s",
            params=(cutoff,),
            fetch=False,
        )


async def preflight(zip_path: Path, user_id: str) -> Dict[str, Any]:
    """Validate the bundle and report what would be created.
    Returns: {preflight_token, manifest, warnings, name_clashes}"""
    if not zip_path.exists():
        raise ValueError("zip_path does not exist")
    size = zip_path.stat().st_size
    if size > MAX_BUNDLE_BYTES:
        raise ValueError(f"bundle too large: {size}B > {MAX_BUNDLE_BYTES}B")

    # Extract to a persistent dir (kept until /confirm completes or TTL expires).
    # Use a shared volume-friendly path under ~/.nexusagent so docker compose
    # can persist it across backend restarts (mount point set by ops).
    # SINGLE-WORKER ASSUMPTION: this path is on the local fs of whichever
    # backend process handled preflight. Multi-pod scale needs a shared
    # volume (RWX) or object-store rewrite — see
    # .mindflow/project/references/scaling_assumptions.md §1.
    sessions_root = Path.home() / ".nexusagent" / "bundle_preflight"
    sessions_root.mkdir(parents=True, exist_ok=True)
    work_dir = Path(tempfile.mkdtemp(prefix="nx-import-", dir=str(sessions_root)))
    try:
        # zip extraction is CPU-bound — offload to a worker thread.
        await asyncio.to_thread(extract_zip_safely, zip_path, work_dir)
    except Exception:
        shutil.rmtree(work_dir, ignore_errors=True)
        raise

    manifest_path = work_dir / "manifest.json"
    if not manifest_path.exists():
        shutil.rmtree(work_dir, ignore_errors=True)
        raise ValueError("manifest.json missing in bundle")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    # Every manifest string that later becomes a path segment is validated
    # HERE, once, before anything reads with it — the same lesson the export
    # side learned the hard way (`skill_dir` was gated per-branch, so the branch
    # nobody looked at stayed open). `agents` entries become
    # `work_dir/agents/{aid}/agent.json` and `.../channel_credentials.json`; a
    # traversing id reads those two filenames from outside the unpack dir, and
    # a hit on `channel_credentials.json` is imported as the caller's own
    # credentials. The most reachable target is a concurrent import's
    # `bundle_preflight/nx-import-*` dir, not a random host path.
    #
    # Rejecting the WHOLE bundle rather than skipping the offending agent is
    # deliberate: `aid` is also the id_map key and appears in the summary, so a
    # partially-filtered list would desync id_map from the per-agent writes.
    for aid in manifest.get("agents", []) or []:
        try:
            sanitize_filename(str(aid), label="agent id in manifest")
        except ValueError as e:
            shutil.rmtree(work_dir, ignore_errors=True)
            raise ValueError(f"manifest contains an unusable agent id ({e})")

    # Legacy bundle compatibility: pre-3d7e089 exports put every
    # `skipped_external_edge` line into manifest.warnings (one per row).
    # That floods the preflight UI with hundreds of "Bundle warnings"
    # entries that are actually expected closure-drop events. Demote them
    # to manifest.info here so any bundle (old or new) renders the same
    # in the import wizard.
    raw_warnings = manifest.get("warnings") or []
    real_warnings: List[str] = []
    legacy_external_edge_count = 0
    for w in raw_warnings:
        if isinstance(w, str) and w.startswith("skipped_external_edge:"):
            legacy_external_edge_count += 1
        else:
            real_warnings.append(w)
    if legacy_external_edge_count > 0:
        manifest["warnings"] = real_warnings
        info = list(manifest.get("info") or [])
        info.append(
            f"skipped {legacy_external_edge_count} external entity reference(s) "
            "outside the bundle closure (expected — see PRD §8.3)"
        )
        manifest["info"] = info
        info_counters = dict(manifest.get("info_counters") or {})
        info_counters["skipped_external_edge"] = (
            info_counters.get("skipped_external_edge", 0) + legacy_external_edge_count
        )
        manifest["info_counters"] = info_counters

    # Compatibility: walk the bundle-format-version migration chain so older
    # bundles can be upgraded in-place to the current major before import.
    from narranexus.platform.bundle._bundle_migrations import (
        apply_migrations,
        CURRENT_BUNDLE_MAJOR,
    )
    try:
        manifest = await apply_migrations(work_dir, manifest)
    except ValueError:
        shutil.rmtree(work_dir, ignore_errors=True)
        raise
    # Persist the (possibly migrated) manifest back to disk so confirm() reads
    # the upgraded version.
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )

    # Name clashes
    db = await get_db_client()
    name_clashes: List[Dict[str, str]] = []
    for aid in manifest.get("agents", []):
        agent_path = work_dir / "agents" / aid / "agent.json"
        if not agent_path.exists():
            continue
        a = json.loads(agent_path.read_text(encoding="utf-8"))
        existing = await db.get(
            "agents", {"agent_name": a["agent_name"], "created_by": user_id}
        )
        if existing:
            name_clashes.append({
                "agent_id_in_bundle": aid,
                "agent_name": a["agent_name"],
                "existing_count": len(existing),
            })
    team_clash = None
    team = manifest.get("team")
    if team:
        existing_team = await db.get(
            "teams", {"name": team["name"], "owner_user_id": user_id}
        )
        if existing_team:
            team_clash = {"name": team["name"], "existing_count": len(existing_team)}

    # IM channel credential clashes: for opt-in bundles, a credential whose
    # bot-identity is already bound in this environment will be SKIPPED on
    # confirm (not overwritten). Surface it here so the wizard can warn the
    # user that the migrated agent's channel won't get that binding.
    credential_clashes: List[Dict[str, Any]] = []
    for aid in manifest.get("agents", []):
        cred_path = work_dir / "agents" / aid / "channel_credentials.json"
        if not cred_path.exists():
            continue
        try:
            cred_payload = json.loads(cred_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        from narranexus.platform.channel.credential_store import GenericCredentialStore, UnknownChannel

        cred_store = GenericCredentialStore(db)
        for crow in _credential_bundle_rows(cred_payload):
            channel, external = crow.get("channel"), crow.get("external_id")
            if not channel or not external:
                continue
            try:
                bound = await cred_store.find_one(str(channel), external_id=str(external))
            except UnknownChannel:
                # a channel this install does not know: nothing to clash with.
                # Anything else (DB error, unreadable row) propagates — preflight
                # is a dry run and may fail loudly; a "no conflicts" answer
                # produced by a swallowed error would be false consent.
                bound = None
            if bound is not None:
                credential_clashes.append({
                    "agent_id_in_bundle": aid,
                    "table": "channel_credentials",
                    "channel": channel,
                    "identity": {"external_id": external},
                })

    token = uuid.uuid4().hex
    summary = {
        "preflight_token": token,
        "manifest": manifest,
        "name_clashes": name_clashes,
        "team_clash": team_clash,
        "credential_clashes": credential_clashes,
        "warnings": manifest.get("warnings", []),
    }
    # Persist to DB so confirm() works across worker boundaries / restarts.
    await _cleanup_stale_preflights(db)
    await db.insert(
        "bundle_preflight_sessions",
        {
            "token": token,
            "user_id": user_id,
            "work_dir": str(work_dir),
            "manifest_json": json.dumps(manifest, ensure_ascii=False, default=str),
        },
    )
    return summary


def _bundle_member_path(work_dir: Path, archive_ref: Optional[str]) -> Optional[Path]:
    """Resolve a manifest `archive_ref` to a path inside the unpacked bundle.

    `archive_ref` is written by whoever produced the `.nxbundle` — as untrusted
    as any request field, and it used to be joined onto `work_dir` verbatim.
    `"../../../root/.nexusagent/.../x.zip"` would read outside the unpack dir,
    and the zip branch then copies whatever it read into the importing user's
    own `skill_archives` (from where a later export ships it out). Mirror image
    of the export-side `skill_dir` hole.

    Uses `validate_zip_member_path` rather than `ensure_within_directory`
    because a ref is legitimately multi-segment (`skills/<agent_id>/<name>.zip`)
    and `ensure_within_directory` accepts only a single segment.

    Returns None when the ref is missing or unsafe; callers already treat None
    as "archive missing in bundle" and record a per-skill failure.
    """
    if not archive_ref:
        return None
    try:
        member = validate_zip_member_path(archive_ref)
    except ValueError as e:
        logger.warning(f"bundle_import.archive_ref.rejected ref={archive_ref!r}: {e}")
        return None
    candidate = (work_dir / member).resolve(strict=False)
    if not candidate.is_relative_to(work_dir.resolve(strict=False)):
        logger.warning(f"bundle_import.archive_ref.escapes ref={archive_ref!r}")
        return None
    return candidate


async def confirm(preflight_token: str, user_id: str) -> Dict[str, Any]:
    """Execute the import; on ANY mid-import failure run the compensating
    rollback so no orphan team/agents survive (confirm used to be
    best-effort and repeated failures stranded partial teams)."""
    ctx: Dict[str, Any] = {}
    try:
        return await _confirm_inner(preflight_token, user_id, ctx)
    except Exception:
        id_map = ctx.get("id_map")
        db = ctx.get("db")
        if id_map and db is not None:
            logger.error(
                f"bundle_import.failed.rolling_back new_ids={len(id_map)} "
                f"(orphan sweep — original error re-raised after cleanup)"
            )
            await _rollback_partial_import(db, id_map)
        raise


async def _confirm_inner(
    preflight_token: str, user_id: str, _rollback_ctx: Dict[str, Any]
) -> Dict[str, Any]:
    """Execute the actual import using a previously preflighted bundle.

    Emits structured log lines prefixed `bundle_import.<event>` throughout
    so a complete import can be reconstructed from the backend log via
    `grep bundle_import.* backend.log`. Each log line is one event; values
    are key=value pairs for easy parsing.
    """
    import time
    _t0 = time.monotonic()

    db = await get_db_client()
    _rollback_ctx["db"] = db
    row = await db.get_one("bundle_preflight_sessions", {"token": preflight_token})
    if not row:
        logger.warning(f"bundle_import.error event=token_not_found token={preflight_token[:12]}…")
        raise ValueError("preflight_token not found or expired")
    if row["user_id"] != user_id:
        logger.warning(
            f"bundle_import.error event=token_user_mismatch token={preflight_token[:12]}… "
            f"row_user={row['user_id']} requesting_user={user_id}"
        )
        raise ValueError("preflight_token user mismatch")

    work_dir = Path(row["work_dir"])
    manifest = json.loads(row["manifest_json"])
    if not work_dir.exists():
        await db.delete("bundle_preflight_sessions", {"token": preflight_token})
        logger.warning(f"bundle_import.error event=work_dir_missing path={work_dir}")
        raise ValueError("preflight working dir missing — please re-upload the bundle")

    # Manifest snapshot — what we expect the bundle to contain.
    manifest_agents = manifest.get("agents", []) or []
    manifest_skills = manifest.get("skills", []) or []
    logger.info(
        f"bundle_import.start "
        f"token={preflight_token[:12]}… "
        f"user_id={user_id} "
        f"bundle_format={manifest.get('bundle_format_version')} "
        f"manifest_agents={len(manifest_agents)} "
        f"manifest_skills={len(manifest_skills)} "
        f"manifest_team={(manifest.get('team') or {}).get('name')!r}"
    )

    # ---- ID Rewrite ----
    id_map: Dict[str, str] = {}

    # Collect ALL old IDs from structured fields
    for aid in manifest.get("agents", []):
        id_map[aid] = gen_new_id("agent")

    if manifest.get("team"):
        old_team_id = manifest["team"].get("team_id")
        if old_team_id:
            id_map[old_team_id] = gen_new_id("team")

    # Walk the per-agent dirs and pre-collect IDs
    agents_dir = work_dir / "agents"
    if agents_dir.exists():
        for adir in agents_dir.iterdir():
            if not adir.is_dir():
                continue
            # narratives
            ndir = adir / "narratives"
            if ndir.exists():
                for sub in ndir.iterdir():
                    if sub.is_dir():
                        if sub.name not in id_map:
                            id_map[sub.name] = gen_new_id("narrative")
                        # events.jsonl IDs
                        e_path = sub / "events.jsonl"
                        if e_path.exists():
                            with open(e_path, "r", encoding="utf-8") as f:
                                for line in f:
                                    line = line.strip()
                                    if not line:
                                        continue
                                    try:
                                        rec = json.loads(line)
                                    except json.JSONDecodeError:
                                        continue
                                    eid = rec.get("event_id")
                                    if eid and eid not in id_map:
                                        id_map[eid] = gen_new_id("event")
            # instances
            inst_dir = adir / "instances"
            if inst_dir.exists():
                for kdir in inst_dir.iterdir():
                    if kdir.is_dir():
                        for ifile in kdir.iterdir():
                            if ifile.suffix == ".json":
                                rec = json.loads(ifile.read_text(encoding="utf-8"))
                                iid = rec.get("instance_id")
                                if iid and iid not in id_map:
                                    id_map[iid] = gen_new_id("instance")
            # agent_messages
            am_path = adir / "agent_messages.jsonl"
            if am_path.exists():
                with open(am_path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            rec = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        mid = rec.get("message_id")
                        if mid and mid not in id_map:
                            id_map[mid] = gen_new_id("message")
            # jobs
            jobs_path = adir / "jobs.json"
            if jobs_path.exists():
                try:
                    for jrec in json.loads(jobs_path.read_text(encoding="utf-8")):
                        jid = jrec.get("job_id")
                        if jid and jid not in id_map:
                            id_map[jid] = gen_new_id("job")
                except (OSError, json.JSONDecodeError):
                    pass
            # artifacts (1.1+ bundles). Pre-collect artifact_id so the structured
            # rewrite in the write phase doesn't fall through to "pass original
            # value", which would either UNIQUE-conflict on re-import to the same
            # instance or leave cross-references stale.
            art_path = adir / "artifacts.json"
            if art_path.exists():
                try:
                    for arec in json.loads(art_path.read_text(encoding="utf-8")):
                        aiid = arec.get("artifact_id")
                        if aiid and aiid not in id_map:
                            id_map[aiid] = gen_new_id("artifact")
                except (OSError, json.JSONDecodeError):
                    pass

    # Bundle-level: bus channels (channel_id) and bus messages (message_id)
    bus_path = work_dir / "bus.json"
    if bus_path.exists():
        try:
            bus_data = json.loads(bus_path.read_text(encoding="utf-8"))
            for ch in (bus_data.get("channels") or []):
                cid = ch.get("channel_id")
                if cid and cid not in id_map:
                    id_map[cid] = gen_new_id("channel")
            for ms in (bus_data.get("messages") or []):
                mid = ms.get("message_id")
                if mid and mid not in id_map:
                    id_map[mid] = gen_new_id("message")
        except (OSError, json.JSONDecodeError):
            pass

    # Bundle-level: mcp_hints.json entries become live mcp_urls rows on import,
    # so pre-collect their ids the same way. Pre-1.1 bundles wrote only name/url
    # (no mcp_id field); those legacy entries just won't appear here and the
    # write phase mints a fresh id at insert time.
    mcp_hints_path = work_dir / "mcp_hints.json"
    if mcp_hints_path.exists():
        try:
            for mh in json.loads(mcp_hints_path.read_text(encoding="utf-8")):
                mhid = mh.get("mcp_id")
                if mhid and mhid not in id_map:
                    id_map[mhid] = gen_new_id("mcp")
        except (OSError, json.JSONDecodeError):
            pass

    # Log id_map breakdown by kind so we can verify "did pre-collect cover
    # everything" — if the import later fails on UNIQUE conflict for some
    # ID kind, this tells us whether pre-collect missed it.
    id_map_breakdown: Dict[str, int] = {}
    for old_id, new_id in id_map.items():
        # infer kind from prefix of new_id (we minted these so they're well-formed)
        kind = new_id.split("_", 1)[0]
        id_map_breakdown[kind] = id_map_breakdown.get(kind, 0) + 1
    logger.info(
        "bundle_import.id_map.collected "
        + " ".join(f"{k}={v}" for k, v in sorted(id_map_breakdown.items()))
        + f" total={len(id_map)}"
    )
    _rollback_ctx["id_map"] = id_map

    free_text_regex = build_all_id_regex()
    OWNER_PLACEHOLDER = "<original_owner>"

    def rewrite_id(s: str) -> str:
        return id_map.get(s, s)

    def rewrite_text(text: str) -> str:
        if not isinstance(text, str):
            return text
        # Layer 4 (a): kind-prefixed ID rewrite
        text = free_text_regex.sub(lambda m: id_map.get(m.group(), m.group()), text)
        # Layer 4 (b): owner placeholder → recipient user_id (bug 4 fix)
        if OWNER_PLACEHOLDER in text:
            text = text.replace(OWNER_PLACEHOLDER, user_id)
        return text

    def rewrite_value(v: Any) -> Any:
        if isinstance(v, str):
            return rewrite_text(v)
        if isinstance(v, list):
            return [rewrite_value(x) for x in v]
        if isinstance(v, dict):
            return {k: rewrite_value(x) for k, x in v.items()}
        return v

    # Helper: write a row using the structured map and free-text rewrite
    def rewrite_row(table: str, row: dict) -> dict:
        out = dict(row)
        # Strip legacy auto-increment 'id' so we get a fresh PK
        out.pop("id", None)
        for col in list(out.keys()):
            val = out[col]
            kind = STRUCTURED_ID_FIELDS.get(table, {}).get(col)
            if kind and isinstance(val, str):
                out[col] = id_map.get(val, val)
            elif isinstance(val, str):
                # Owner placeholder → current user_id; also rewrite kind-prefixed IDs
                out[col] = rewrite_text(val)
            elif isinstance(val, (list, dict)):
                out[col] = rewrite_value(val)
        # User-attribution columns: any column literally named user_id, created_by,
        # or owner_user_id should be set to the recipient's user_id (the source
        # value was scrubbed to the placeholder during export, but we re-assert
        # here in case some legacy bundle stored a literal source user_id).
        #
        # Exception: bus_channels.created_by stores an AGENT_ID (the channel
        # owner agent), not a user_id. The structured-ID branch above already
        # mapped it from old → new agent_id via id_map. Forcing it to user_id
        # here would break the trigger's "channel owner always activated"
        # logic (msg_bus_trigger.py:154 compares created_by against agent_id).
        #
        # Exception: IM channel credential tables. Their user-ish columns
        # (slack/telegram/wechat `owner_user_id`, discord `owner_user_id` +
        # `user_id`) hold the IM-side owner identity (a Slack/Telegram/Discord
        # user id), NOT a NarraNexus user id. Reattributing them to the
        # recipient would corrupt the owner-trust signal the trigger uses. The
        # agent_id column was already mapped via STRUCTURED_ID_FIELDS above.
        if table != "channel_credentials":
            for col in list(out.keys()):
                if col in ("user_id", "created_by", "owner_user_id"):
                    if table == "bus_channels" and col == "created_by":
                        continue
                    v = out[col]
                    if isinstance(v, str) and (v == OWNER_PLACEHOLDER or v != user_id):
                        out[col] = user_id
        return out

    # ---- Name suffix dedupe ----
    db = await get_db_client()

    async def dedupe_name(table: str, name_col: str, owner_filter: dict, candidate: str) -> str:
        existing = await db.get(table, {**owner_filter, name_col: candidate})
        if not existing:
            return candidate
        n = 1
        while True:
            new_name = f"{candidate} ({n})"
            existing = await db.get(table, {**owner_filter, name_col: new_name})
            if not existing:
                return new_name
            n += 1

    # ---- WRITE phase (best-effort transactional via try/except + rollback notes) ----
    written_summary = {
        "agents_created": 0,
        "agents_renamed": 0,
        "team_created": False,
        "narratives_created": 0,
        "events_created": 0,
        "instances_created": 0,
        "messages_created": 0,
        "social_entities_created": 0,
        "awareness_rows_created": 0,
        "jobs_created": 0,
        "narrative_links_created": 0,
        "memory_rows_created": 0,
        "artifacts_created": 0,
        "mcp_urls_created": 0,
        "bus_channels_created": 0,
        "bus_members_created": 0,
        "bus_messages_created": 0,
        "bus_registry_created": 0,
        "inbox_rows_created": 0,
        "skills_imported": 0,
        "mcp_hints": 0,
        # Opt-in IM channel credentials: imported = landed inactive; skipped =
        # a same-bot binding already existed in the target env (see clash check).
        "channel_credentials_imported": 0,
        "channel_credentials_skipped_conflict": 0,
        # [{agent_name, fields:[...]}] for agents whose name/description was
        # over-long in the bundle and got trimmed to AGENT_TEXT_MAX_LENGTH.
        "agent_fields_trimmed": [],
        "warnings": [],
    }

    # Legacy-column tolerance: every bundle-sourced row is sanitized
    # against the CURRENT schema before insert (see _sanitize_for_schema).
    dropped_legacy_columns: Dict[str, int] = {}

    async def _ins(table: str, row: Dict[str, Any]) -> None:
        await db.insert(table, _sanitize_for_schema(table, row, dropped_legacy_columns))

    # -- Team --
    new_team_id = None
    if manifest.get("team"):
        team = manifest["team"]
        old_tid = team.get("team_id")
        new_tid = id_map.get(old_tid) if old_tid else gen_new_id("team")
        if not old_tid:
            id_map["__team_synthetic__"] = new_tid

        team_name = await dedupe_name(
            "teams", "name", {"owner_user_id": user_id}, team["name"]
        )
        intro = team.get("intro_md", "")
        if not intro:
            readme_path = work_dir / "README.md"
            if readme_path.exists():
                intro = readme_path.read_text(encoding="utf-8")
        await _ins("teams", {
            "team_id": new_tid,
            "owner_user_id": user_id,
            "name": team_name,
            "description": team.get("description"),
            "color": team.get("color"),
            "source": "bundle",
            "intro_md": intro,
        })
        # The rules land under the NEW team id, with the ceilings re-applied:
        # a bundle is untrusted input and may have been hand-edited.
        written_summary["bulletin_entries"] = await write_imported_bulletin(
            db, new_tid, team.get("bulletin") or []
        )
        # The board comes back with the team. Ids are remapped here rather than
        # in the bundle: `assignee_id` is a SOURCE agent id, and one that fell
        # outside the export closure has no counterpart here — it becomes
        # unclaimed work, which is true and actionable, instead of a dangling
        # reference nobody can chase.
        #
        # `root_run_id` is left NULL on purpose (the exporter drops it): it
        # named a run in the source environment, and honouring it would let a
        # cascade stop here match items it never produced.
        board = team.get("work_items") or []
        for item in board:
            old_assignee = item.get("assignee_id") or ""
            await _ins("team_work_items", {
                "item_id": gen_new_id("wi"),
                "team_id": new_tid,
                # Backfilled after the bus channels are restored (the team row
                # is written before them). Left empty when the bundle carried
                # no chat: the board is still fully usable in the UI (that view
                # keys on team_id), only patrol waits — and patrol needs a room
                # to speak into anyway.
                "channel_id": "",
                "title": item.get("title") or "",
                "assignee_id": id_map.get(old_assignee) if old_assignee else None,
                "status": item.get("status") or "open",
                "created_by": id_map.get(item.get("created_by") or "", "") or "",
                "root_run_id": None,
            })
        if board:
            written_summary["work_items"] = len(board)

        new_team_id = new_tid
        written_summary["team_created"] = True
        written_summary["team_id"] = new_tid
        written_summary["team_name"] = team_name

    if new_team_id:
        logger.info(
            f"bundle_import.team.created new_id={new_team_id} name={team_name!r} "
            f"intro_md_chars={len(intro or '')}"
        )

    # -- Per-agent write --
    for old_aid in manifest.get("agents", []):
        # snapshot per-agent counts so we can log a delta after writing this agent
        before = {k: v for k, v in written_summary.items() if isinstance(v, int)}
        adir = work_dir / "agents" / old_aid
        if not adir.is_dir():
            logger.warning(f"bundle_import.agent.skip old_id={old_aid} reason=dir_missing")
            continue
        agent_path = adir / "agent.json"
        if not agent_path.exists():
            logger.warning(f"bundle_import.agent.skip old_id={old_aid} reason=agent_json_missing")
            continue
        agent_record = json.loads(agent_path.read_text(encoding="utf-8"))
        new_aid = id_map[old_aid]

        # Clamp over-long name/description to the AGENT_TEXT_MAX_LENGTH ceiling
        # the Agent model enforces on read. The raw db.insert below bypasses
        # that model, so an unclamped value would strand the agent as
        # "insertable but unreadable" — every later edit/delete deserializes the
        # row and fails Pydantic validation.
        # Normalized FIRST — before dedupe and before the clamp. This insert
        # bypasses AgentRepository, so nothing else will strip it, and a row
        # holding " name " can never be renamed afterwards: the update path
        # compares normalized values, so saving the stripped form is judged a
        # no-op and never written (see agent_field_matches). Ahead of dedupe
        # for a second reason — an unstripped name would not match the
        # already-normalized rows it is supposed to be deduped against.
        original_name = normalize_agent_text(agent_record["agent_name"])
        clamped_name, name_trimmed = _clamp_agent_text(original_name)
        clamped_desc, desc_trimmed = _clamp_agent_text(
            normalize_agent_text(agent_record.get("agent_description"))
        )
        agent_record["agent_description"] = clamped_desc

        # Dedupe against existing (already-normalized, already-clamped) names,
        # THEN normalize and clamp again. dedupe_name appends a " (n)" suffix
        # that has neither a length budget nor a whitespace budget of its own:
        # on a clash a clamped 255-char name becomes "…255… (1)" = 259 and lands
        # back over the ceiling, and an EMPTY candidate becomes " (1)" with a
        # leading space — an unnormalized value, i.e. a row that can never be
        # renamed afterwards (see agent_field_matches). Re-running both on the
        # FINAL name is what actually guarantees the raw insert stores a
        # normalized, readable value. agent_name has no UNIQUE constraint, so a
        # rare post-clamp collision is harmless — two same-named agents,
        # exactly as manual creation allows.
        deduped_name = await dedupe_name(
            "agents", "agent_name", {"created_by": user_id}, clamped_name
        )
        final_name, dedupe_overflow = _clamp_agent_text(
            normalize_agent_text(deduped_name)
        )
        # Last of the five creation paths to get a non-empty-name fallback (the
        # auth route uses "New Agent", the migration applier "Imported Agent",
        # and both create_agent legs refuse outright). A bundle is user-supplied
        # input, so an empty or whitespace-only agent_name in agent.json is
        # ordinary; stored empty it renders as a bare agent_id everywhere.
        #
        # AFTER dedupe on purpose: before it, every empty-named bundle would be
        # deduped against the existing "Imported Agent" rows and collect a
        # " (n)" suffix. After it, a duplicate name is simply allowed —
        # agent_name has no UNIQUE constraint, as the comment above says.
        final_name = final_name or "Imported Agent"
        name_trimmed = name_trimmed or dedupe_overflow

        renamed = (final_name != clamped_name)
        if renamed:
            written_summary["agents_renamed"] += 1
        if name_trimmed or desc_trimmed:
            trimmed_fields = [
                f for f, hit in (("agent_name", name_trimmed),
                                 ("agent_description", desc_trimmed)) if hit
            ]
            written_summary["agent_fields_trimmed"].append(
                {"agent_name": final_name, "fields": trimmed_fields}
            )
            written_summary["warnings"].append(
                f"Agent {final_name!r}: {', '.join(trimmed_fields)} exceeded "
                f"{AGENT_TEXT_MAX_LENGTH} chars and was trimmed to fit."
            )

        # Insert agents row (new agent_id, current user_id)
        new_agent_row = rewrite_row("agents", agent_record)
        new_agent_row["agent_id"] = new_aid
        new_agent_row["agent_name"] = final_name
        new_agent_row["created_by"] = user_id
        new_agent_row.pop("agent_create_time", None)
        new_agent_row.pop("agent_update_time", None)
        await _ins("agents", new_agent_row)
        written_summary["agents_created"] += 1
        rename_part = f"renamed_from={original_name!r}" if renamed else "renamed=no"
        logger.info(
            f"bundle_import.agent.created old_id={old_aid} new_id={new_aid} "
            f"name={final_name!r} {rename_part}"
        )

        # team_members
        if new_team_id:
            await _ins("team_members", {"team_id": new_team_id, "agent_id": new_aid})

        # Narratives + events
        ndir = adir / "narratives"
        if ndir.exists():
            for nsub in ndir.iterdir():
                if not nsub.is_dir():
                    continue
                nfile = nsub / "narrative.json"
                if not nfile.exists():
                    continue
                nrow = json.loads(nfile.read_text(encoding="utf-8"))
                new_nrow = rewrite_row("narratives", nrow)
                new_nrow.pop("created_at", None)
                new_nrow.pop("updated_at", None)
                await _ins("narratives", new_nrow)
                written_summary["narratives_created"] += 1

                e_path = nsub / "events.jsonl"
                if e_path.exists():
                    with open(e_path, "r", encoding="utf-8") as f:
                        for line in f:
                            line = line.strip()
                            if not line:
                                continue
                            try:
                                erec = json.loads(line)
                            except json.JSONDecodeError:
                                continue
                            new_erow = rewrite_row("events", erec)
                            new_erow.pop("created_at", None)
                            new_erow.pop("updated_at", None)
                            new_erow["user_id"] = user_id
                            await _ins("events", new_erow)
                            written_summary["events_created"] += 1

        # instances. Filter out rows whose module_class isn't registered in
        # module_registry on this side: importing them produces zombie rows the
        # runtime would log "Unknown module type X, skipping" against on every
        # agent turn (and they'd be dead weight forever, since cascade-delete
        # only fires when the agent itself is deleted). Cascade-drop their
        # instance-scoped children (jobs / social / memory / narrative_links)
        # by remembering the skipped instance_ids.
        from narranexus.platform.module_system import module_registry
        skipped_instance_ids: set = set()
        skipped_by_class: Dict[str, int] = {}
        inst_dir = adir / "instances"
        if inst_dir.exists():
            for kdir in inst_dir.iterdir():
                if not kdir.is_dir():
                    continue
                for ifile in kdir.iterdir():
                    if ifile.suffix != ".json":
                        continue
                    irec = json.loads(ifile.read_text(encoding="utf-8"))
                    mclass = irec.get("module_class") or kdir.name
                    if mclass not in module_registry:
                        skipped_instance_ids.add(irec.get("instance_id") or "")
                        skipped_by_class[mclass] = skipped_by_class.get(mclass, 0) + 1
                        continue
                    new_irow = rewrite_row("module_instances", irec)
                    new_irow["user_id"] = user_id
                    new_irow.pop("created_at", None)
                    new_irow.pop("updated_at", None)
                    await _ins("module_instances", new_irow)
                    written_summary["instances_created"] += 1
        if skipped_by_class:
            for cls_name, n in skipped_by_class.items():
                msg = (
                    f"agent {old_aid}: skipped {n} {cls_name} instance(s) — "
                    "module class not registered in this build"
                )
                written_summary["warnings"].append(msg)
                logger.warning(f"bundle_import.skip_unknown_module agent={old_aid} class={cls_name} count={n}")

        # social entities — written into the unified memory_entity store via the
        # repo (entity's single home now). The flat records + id rewrites are
        # unchanged; only the destination moved off instance_social_entities.
        se_path = adir / "social_entities.json"
        if se_path.exists():
            from narranexus.platform.repository import SocialNetworkRepository
            from narranexus.platform.schema import SocialNetworkEntity
            social_repo = SocialNetworkRepository(db, new_aid)
            for srec in json.loads(se_path.read_text(encoding="utf-8")):
                if srec.get("instance_id") in skipped_instance_ids:
                    continue
                new_sr = rewrite_row("social_entities", srec)
                # entity_id might be an agent_id in our closure
                if srec.get("entity_type") == "agent":
                    eid = new_sr.get("entity_id")
                    if eid in id_map:
                        new_sr["entity_id"] = id_map[eid]
                entity = SocialNetworkEntity(
                    entity_id=new_sr.get("entity_id"),
                    entity_type=new_sr.get("entity_type") or "user",
                    instance_id=new_sr.get("instance_id"),
                    entity_name=new_sr.get("entity_name"),
                    aliases=_loads_maybe(new_sr.get("aliases"), []),
                    entity_description=new_sr.get("entity_description"),
                    identity_info=_loads_maybe(new_sr.get("identity_info"), {}),
                    contact_info=_loads_maybe(new_sr.get("contact_info"), {}),
                    familiarity=new_sr.get("familiarity") or "known_of",
                    relationship_strength=float(new_sr.get("relationship_strength") or 0.0),
                    interaction_count=int(new_sr.get("interaction_count") or 0),
                    last_interaction_time=new_sr.get("last_interaction_time"),
                    keywords=_loads_maybe(new_sr.get("tags"), []),
                    expertise_domains=_loads_maybe(new_sr.get("expertise_domains"), []),
                    related_job_ids=_loads_maybe(new_sr.get("related_job_ids"), []),
                    persona=new_sr.get("persona"),
                    extra_data=_loads_maybe(new_sr.get("extra_data"), {}),
                )
                await social_repo.save_entity(entity)
                written_summary["social_entities_created"] += 1

        # agent_messages
        am_path = adir / "agent_messages.jsonl"
        if am_path.exists():
            with open(am_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        mrec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    new_mr = rewrite_row("agent_messages", mrec)
                    new_mr.pop("created_at", None)
                    await _ins("agent_messages", new_mr)
                    written_summary["messages_created"] += 1

        # awareness (bug 1 — was being exported but never inserted on import)
        aware_path = adir / "awareness.json"
        if aware_path.exists():
            for arec in json.loads(aware_path.read_text(encoding="utf-8")):
                if arec.get("instance_id") in skipped_instance_ids:
                    continue
                new_ar = rewrite_row("instance_awareness", arec)
                new_ar.pop("created_at", None)
                new_ar.pop("updated_at", None)
                await _ins("instance_awareness", new_ar)
                written_summary["awareness_rows_created"] += 1

        # An import that renamed is a rename, and this file copies the profile
        # row for row — so the imported agent arrives declaring the name it had
        # in the bundle while its own row holds the deduped/clamped one. That is
        # Shenzhen round 2 reaching the same state through the import path.
        #
        # Reconciliation, not the rename transaction: that one's precondition is
        # an existing row it may write, and here the row was just created with
        # the final name already in it. Idempotent by construction — it returns
        # None when the profile and the row already agree — so it is safe on
        # every import, renamed or not. AFTER the awareness insert above: the
        # correction has to land in a row that exists.
        if renamed:
            # builtin.awareness owns the identity record; it listens for this
            # host event (module/awareness_module/plugin_hooks.py).
            from narranexus.platform.utils.host_hooks import call_host_hook

            outcome = await call_host_hook("onDidSettleAgentName", db=db, agent_id=new_aid, name=final_name)
            if outcome.first is False:
                logger.warning(
                    f"bundle_import.agent.identity_not_corrected new_id={new_aid} "
                    f"renamed_from={original_name!r} to={final_name!r} — the "
                    f"imported profile may still declare the previous name"
                )

        # instance_jobs.
        #
        # Two correctness rules:
        # 1. Cascade-drop rows whose parent module_instance was skipped above
        #    (unknown module class) — otherwise the runtime ends up with orphan
        #    Job rows pointing to a non-existent instance, and JobModel
        #    validation fires on every poll.
        # 2. PRESERVE created_at / updated_at from the bundle. The instance_jobs
        #    schema has no DB-level default for these columns, so popping them
        #    inserts NULL — and JobModel.created_at is a non-Optional datetime,
        #    so job_trigger fails to parse the row on first poll. (See
        #    schema_registry.py: Column("created_at", "TEXT", "DATETIME(6)")
        #    has neither nullable=False nor default.)
        jobs_path = adir / "jobs.json"
        if jobs_path.exists():
            from datetime import datetime, timezone
            for jrec in json.loads(jobs_path.read_text(encoding="utf-8")):
                if jrec.get("instance_id") in skipped_instance_ids:
                    continue
                new_jr = rewrite_row("instance_jobs", jrec)
                # Backfill timestamps if the bundle is missing them. Rows from
                # older bundles (pre-fix) may have been written with NULLs;
                # fall back to "now" so JobModel parsing works.
                if not new_jr.get("created_at"):
                    new_jr["created_at"] = datetime.now(timezone.utc).isoformat(sep=" ")
                if not new_jr.get("updated_at"):
                    new_jr["updated_at"] = new_jr["created_at"]
                await _ins("instance_jobs", new_jr)
                written_summary["jobs_created"] += 1

        # instance_narrative_links (bidirectional binding between narratives + module instances)
        #
        # The table's UNIQUE key is (instance_id, narrative_id) only — link_type
        # is NOT in the unique index. Two real-world cases can produce the same
        # pair twice in one import:
        #   1. The same instance has multiple link rows on source side (e.g.
        #      one ACTIVE + one COMPLETED for archival), so the bundle's
        #      links file legitimately has both rows.
        #   2. Cross-agent shared instances appear in two agents' links files;
        #      both rows rewrite to the same new (instance_id, narrative_id)
        #      pair, and the second agent's loop hits the unique constraint.
        # Either way: aborting the whole confirm() on row #2 is the wrong
        # call. Skip the dup, count it, keep going.
        nl_path = adir / "instance_narrative_links.json"
        nl_dups = 0
        if nl_path.exists():
            seen_pairs: set = set()
            for nrec in json.loads(nl_path.read_text(encoding="utf-8")):
                if nrec.get("instance_id") in skipped_instance_ids:
                    continue
                new_nl = rewrite_row("instance_narrative_links", nrec)
                new_nl.pop("created_at", None)
                new_nl.pop("updated_at", None)
                pair = (new_nl.get("instance_id"), new_nl.get("narrative_id"))
                if pair in seen_pairs:
                    # Intra-file duplicate (case 1) — skip without hitting DB.
                    nl_dups += 1
                    continue
                seen_pairs.add(pair)
                try:
                    await _ins("instance_narrative_links", new_nl)
                    written_summary["narrative_links_created"] += 1
                except Exception as ex:
                    # Cross-agent / cross-file duplicate (case 2) — pair was
                    # inserted by an earlier agent's loop in this same confirm().
                    # We catch broadly because the underlying driver's exception
                    # type differs by backend (pymysql.IntegrityError on MySQL,
                    # sqlite3.IntegrityError via aiosqlite on SQLite). Re-raise
                    # anything that doesn't smell like a unique-constraint hit.
                    if is_unique_violation(ex):
                        nl_dups += 1
                    else:
                        raise
        if nl_dups:
            written_summary["warnings"].append(
                f"agent {old_aid}: skipped {nl_dups} duplicate instance_narrative_links row(s)"
            )
            logger.info(
                f"bundle_import.dedup_narrative_links agent={old_aid} skipped={nl_dups}"
            )

        # Per-instance memory family (used by various Modules to remember per-turn state)
        for memory_table in (
            "instance_module_report_memory",
            "instance_json_format_memory",
            "instance_json_format_memory_chat",
            "module_report_memory",
        ):
            mp = adir / f"{memory_table}.json"
            if not mp.exists():
                continue
            for mrec in json.loads(mp.read_text(encoding="utf-8")):
                if mrec.get("instance_id") in skipped_instance_ids:
                    continue
                new_mm = rewrite_row(memory_table, mrec)
                new_mm.pop("created_at", None)
                new_mm.pop("updated_at", None)
                try:
                    await _ins(memory_table, new_mm)
                    written_summary["memory_rows_created"] += 1
                except Exception as me:
                    logger.warning(f"insert {memory_table} failed: {me}")

        # workspace tar — offload extraction to thread.
        ws_tar = adir / "workspace.tar.gz"
        ws_size_b = ws_tar.stat().st_size if ws_tar.exists() else 0
        ws_extracted = False
        ws_target = None
        if ws_tar.exists():
            from narranexus.platform.settings import settings as core_settings
            from narranexus.platform.utils.workspace_paths import agent_workspace_path
            target = agent_workspace_path(new_aid, user_id, base=core_settings.base_working_path)
            target.mkdir(parents=True, exist_ok=True)
            await asyncio.to_thread(_extract_tar_safely, ws_tar, target)
            await asyncio.to_thread(_rewrite_workspace_text_files, target, id_map, user_id)
            ws_extracted = True
            ws_target = target

        # instance_artifacts (1.1+ bundles): validation, re-prefixing and the
        # session/pinned reset live in `rewrite_artifact_row`. A bad pointer
        # raises ValueError and aborts the import (not caught below).
        art_path = adir / "artifacts.json"
        if art_path.exists():
            try:
                for arec in json.loads(art_path.read_text(encoding="utf-8")):
                    new_ar = rewrite_artifact_row(
                        rewrite_row("instance_artifacts", arec), new_aid, user_id
                    )
                    try:
                        await _ins("instance_artifacts", new_ar)
                        written_summary["artifacts_created"] += 1
                    except Exception as ae:
                        logger.warning(
                            f"bundle_import.artifact.insert_failed agent={old_aid} "
                            f"old_id={arec.get('artifact_id')} reason={ae}"
                        )
                        written_summary["warnings"].append(
                            f"agent {old_aid}: artifact {arec.get('artifact_id')} insert failed: {ae}"
                        )
            except (OSError, json.JSONDecodeError) as e:
                logger.warning(f"bundle_import.artifacts.read_failed agent={old_aid} reason={e}")
                written_summary["warnings"].append(
                    f"agent {old_aid}: artifacts.json could not be read: {e}"
                )

        # IM channel credentials (opt-in bundles only). Two invariants:
        #  1) Every credential lands INACTIVE (active_col forced to 0),
        #     regardless of the source value. The user must manually activate
        #     the channel here — that activation is what claims the single
        #     WebSocket slot the IM issues per app, preventing the migrated
        #     agent from double-connecting the same bot from two environments.
        #  2) A credential whose bot-identity is ALREADY bound in this
        #     environment is SKIPPED, not force-overwritten. Stealing a live
        #     bot from an existing agent would be destructive; we keep the
        #     existing binding and report the skip.
        cred_path = adir / "channel_credentials.json"
        if cred_path.exists():
            from narranexus.platform.channel.credential_store import GenericCredentialStore, UnknownChannel

            try:
                cred_payload = json.loads(cred_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as e:
                logger.warning(
                    f"bundle_import.channel_credentials.read_failed agent={old_aid} reason={e}"
                )
                cred_payload = {}
            cred_store = GenericCredentialStore(db)
            for crow in _credential_bundle_rows(cred_payload):
                channel = str(crow.get("channel") or "")
                external = crow.get("external_id")
                if not channel:
                    continue
                try:
                    # Clash check: is this exact bot already bound here?
                    if external and await cred_store.find_one(channel, external_id=str(external)) is not None:
                        written_summary["channel_credentials_skipped_conflict"] += 1
                        written_summary["warnings"].append(
                            f"agent {old_aid}: {channel} bot binding already exists "
                            f"in this environment (external_id={external}) — skipped, not overwritten"
                        )
                        continue
                    new_crow = rewrite_row("channel_credentials", crow)  # remaps agent_id old → new
                    values = {
                        k: v for k, v in new_crow.items()
                        if k not in ("id", "channel", "agent_id", "enabled", "external_id", "created_at", "updated_at", "version")
                    }
                    # Every imported binding lands INACTIVE (invariant 1): the user activates it here.
                    await cred_store.upsert(channel, new_crow["agent_id"], values, enabled=False)
                    written_summary["channel_credentials_imported"] += 1
                except UnknownChannel as ce:
                    written_summary["warnings"].append(f"agent {old_aid}: {channel} credential skipped: {ce}")
                except Exception as ce:  # noqa: BLE001
                    logger.warning(
                        f"bundle_import.channel_credential.insert_failed agent={old_aid} "
                        f"channel={channel} reason={ce}"
                    )
                    written_summary["warnings"].append(
                        f"agent {old_aid}: {channel} credential insert failed: {ce}"
                    )

        # Per-agent delta log so the user can see counts attributable to THIS agent
        delta = {
            k: written_summary[k] - before.get(k, 0)
            for k in (
                "narratives_created", "events_created", "instances_created",
                "messages_created", "social_entities_created",
                "awareness_rows_created", "jobs_created", "narrative_links_created",
                "memory_rows_created", "artifacts_created",
            )
            if isinstance(written_summary.get(k), int) and (written_summary.get(k, 0) - before.get(k, 0)) > 0
        }
        delta_str = " ".join(f"{k}={v}" for k, v in sorted(delta.items())) or "(empty)"
        logger.info(
            f"bundle_import.agent.write old_id={old_aid} new_id={new_aid} "
            f"workspace_tar={ws_size_b}B workspace_target={ws_target} "
            f"{delta_str}"
        )

    logger.info(
        f"bundle_import.agents.done agents_created={written_summary['agents_created']} "
        f"renamed={written_summary['agents_renamed']}"
    )

    # -- Bus state (bug 2: messagebus channels/messages/registry not migrated) --
    # Cross-agent and cross-channel rewrite is needed: channel_id and agent_id
    # both change. rewrite_row handles this since both kinds are in
    # STRUCTURED_ID_FIELDS.
    bus_path = work_dir / "bus.json"
    if bus_path.exists():
        try:
            bus = json.loads(bus_path.read_text(encoding="utf-8"))
            for ch in (bus.get("channels") or []):
                new_ch = rewrite_row("bus_channels", ch)
                new_ch.pop("created_at", None)
                new_ch.pop("updated_at", None)
                # owner_user_id → recipient (rewrite_row handles this column-wise)
                try:
                    await _ins("bus_channels", new_ch)
                    written_summary["bus_channels_created"] += 1
                except Exception as e:
                    logger.warning(f"bus_channels insert failed: {e}")
            # The work board was written with the team, before this block, so
            # its room reference is filled in now that the room exists.
            if new_team_id:
                try:
                    room = await db.get_one(
                        "bus_channels",
                        {"created_by": f"team_{new_team_id}", "channel_type": "group"},
                    )
                    if room:
                        await db.update(
                            "team_work_items",
                            {"team_id": new_team_id},
                            {"channel_id": room["channel_id"]},
                        )
                except Exception as e:  # noqa: BLE001 — board keeps its UI value
                    logger.warning(f"work-board channel backfill failed: {e}")

            for m in (bus.get("members") or []):
                new_m = rewrite_row("bus_channel_members", m)
                try:
                    await _ins("bus_channel_members", new_m)
                    written_summary["bus_members_created"] += 1
                except Exception as e:
                    logger.warning(f"bus_channel_members insert failed: {e}")
            for ms in (bus.get("messages") or []):
                new_ms = rewrite_row("bus_messages", ms)
                new_ms.pop("created_at", None)
                try:
                    await _ins("bus_messages", new_ms)
                    written_summary["bus_messages_created"] += 1
                except Exception as e:
                    logger.warning(f"bus_messages insert failed: {e}")
            for r in (bus.get("registry") or []):
                new_r = rewrite_row("bus_agent_registry", r)
                try:
                    await _ins("bus_agent_registry", new_r)
                    written_summary["bus_registry_created"] += 1
                except Exception as e:
                    # Likely a UNIQUE collision if the recipient happens to already
                    # have a registry row for this new agent_id (shouldn't, since
                    # ID was just freshly minted, but be defensive).
                    logger.warning(f"bus_agent_registry insert failed: {e}")
        except Exception as e:
            logger.warning(f"bus.json processing failed: {e}")
            written_summary["warnings"].append(f"bus state import failed: {e}")

    # -- Inbox (per-user notifications tied to events in closure) --
    inbox_path = work_dir / "inbox.json"
    if inbox_path.exists():
        try:
            for ib in json.loads(inbox_path.read_text(encoding="utf-8")):
                new_ib = rewrite_row("inbox_table", ib)
                new_ib.pop("created_at", None)
                # Drop pre-existing message_id (we'll let SQLite mint a new one
                # via the auto-increment surrogate; message_id UNIQUE would clash
                # if user re-imports the same bundle).
                if "message_id" in new_ib:
                    new_ib["message_id"] = id_map.get(new_ib["message_id"], new_ib["message_id"])
                try:
                    await _ins("inbox_table", new_ib)
                    written_summary["inbox_rows_created"] += 1
                except Exception as e:
                    logger.warning(f"inbox_table insert failed: {e}")
        except Exception as e:
            logger.warning(f"inbox.json processing failed: {e}")
            written_summary["warnings"].append(f"inbox import failed: {e}")

    logger.info(
        f"bundle_import.bus.done channels={written_summary.get('bus_channels_created', 0)} "
        f"members={written_summary.get('bus_members_created', 0)} "
        f"messages={written_summary.get('bus_messages_created', 0)} "
        f"registry={written_summary.get('bus_registry_created', 0)} "
        f"inbox={written_summary.get('inbox_rows_created', 0)}"
    )

    # -- Skills (auto-install per-(agent, skill)) --
    # SEC-07: `skill_name` here comes from the imported bundle's manifest, i.e.
    # from whoever authored the `.nxbundle` — as untrusted as a form field.
    # Archive paths are built via `prepare_archive_target`, never by f-string.
    skill_install_failures: List[Dict[str, str]] = []

    from narranexus.platform.utils.plugin_services import skill_workspace
    from narranexus.platform.bundle.skill_backup import (
        backup_after_api_install,
        prepare_archive_target,
        register_archive,
    )

    # Perf optimization: when N entries reference the same source (same
    # skill_name + same install_method + same source URL or zip archive_ref),
    # we install once into the FIRST agent's skills/ dir and `cp -r` to the
    # rest. Avoids N×git-clones / N×zip-extracts which can dominate import
    # time on team bundles (5 agents sharing 4 skills = 20 unnecessary
    # subprocess.git invocations under the naive impl).
    # full_copy is excluded — each agent's archive zip carries that agent's
    # own .skill_meta.json / credentials, so they're distinct payloads.
    from narranexus.platform.utils.file_safety import ensure_within_directory
    from narranexus.platform.settings import settings as core_settings

    def install_cache_key(s_entry: dict) -> Optional[str]:
        # Keyed on the manifest's ``name`` (what the builder writes — there is
        # no ``skill_name`` key in a manifest entry). With the wrong key two
        # rows of one multi-skill repo collapsed onto the same cache slot and
        # the second agent received skill A's files under skill B's name.
        m = s_entry.get("install_method")
        if m == "url":
            return f"url::{s_entry.get('name')}::{s_entry.get('source_url')}::{s_entry.get('branch') or 'main'}"
        if m == "zip":
            return f"zip::{s_entry.get('name')}::{s_entry.get('archive_ref')}"
        return None

    install_cache: Dict[str, Path] = {}  # cache_key → "first installed skill dir"

    def _install_one_skill_from_github(sm, src_url: str, branch: str, skill_name: str, skill_dir: Optional[str]):
        """Clone the repo and install ONLY the skill this bundle row names.

        A repo may ship several skills; the row's consent covers one. The
        root is matched by SKILL.md name or by directory (``skill_dir`` from
        the manifest, else the sanitised name). No match -> ValueError with
        what the repo does ship; never a guess at the first root.
        """
        wanted_dir = sanitize_filename(skill_dir or skill_name, label="skill name")
        temp_dir = Path(tempfile.mkdtemp())
        try:
            roots, canonical_url = sm.fetch_github_repo(src_url, branch, temp_dir)
            found = []
            for root in roots:
                name = sm.parse_skill_package(root).name
                found.append(name)
                if name == skill_name or root.name == wanted_dir:
                    return sm.install_from_dir(root, source_type="github", source_url=canonical_url)
            raise ValueError(
                f"repository {src_url} ships no skill named '{skill_name}' (found: {', '.join(found)})"
            )
        finally:
            if temp_dir.exists():
                shutil.rmtree(temp_dir)

    def _copy_skill_to_agent(src_skill_dir: Path, target_aid: str, skill_name: str) -> None:
        copy_skill_into_agent(
            src_skill_dir, target_aid, user_id, skill_name, base=core_settings.base_working_path,
        )

    for s in manifest.get("skills", []):
        method = s.get("install_method")
        skill_name = s.get("name")
        old_aid = s.get("agent_id")
        # Backward compat: pre-per-agent bundles had no agent_id; install on all.
        target_aids = []
        if old_aid:
            new_aid = id_map.get(old_aid)
            if new_aid:
                target_aids = [new_aid]
            else:
                skill_install_failures.append(
                    {"skill": skill_name, "reason": f"agent {old_aid} not in id_map"}
                )
                continue
        else:
            # Legacy bundle: apply this skill to every imported agent.
            target_aids = [id_map[old] for old in manifest.get("agents", []) if old in id_map]

        if not skill_name:
            continue
        try:
            if method == "url":
                src_url = s.get("source_url")
                branch = s.get("branch") or "main"
                if not src_url:
                    skill_install_failures.append(
                        {"skill": skill_name, "reason": "url install method but no source_url"}
                    )
                    continue
                key = install_cache_key(s)
                cached_dir = install_cache.get(key) if key else None
                # First pass: install into the first agent's workspace, then
                # cache that dir. Subsequent agents copy from the cache.
                first_aid = target_aids[0]
                if cached_dir is None:
                    sm = skill_workspace(first_aid, user_id)
                    # A multi-skill repo is NOT installed whole: only the
                    # skill this row names (the user consented to that one).
                    try:
                        info = await asyncio.to_thread(
                            _install_one_skill_from_github, sm, src_url, branch, skill_name, s.get("skill_dir")
                        )
                    except ValueError as exc:
                        skill_install_failures.append({"skill": skill_name, "reason": str(exc)})
                        continue
                    cached_dir = Path(info.path)
                    if key:
                        install_cache[key] = cached_dir
                else:
                    await asyncio.to_thread(
                        _copy_skill_to_agent, cached_dir, first_aid, skill_name
                    )
                # Copy to remaining agents (no clone needed)
                for new_aid in target_aids[1:]:
                    await asyncio.to_thread(
                        _copy_skill_to_agent, cached_dir, new_aid, skill_name
                    )
                await backup_after_api_install(
                    user_id=user_id,
                    skill_name=skill_name,
                    source_type="github",
                    source_url=src_url,
                    branch=branch,
                )
                written_summary["skills_imported"] += 1

            elif method == "zip":
                archive_ref = s.get("archive_ref")
                zip_path = _bundle_member_path(work_dir, archive_ref)
                if not zip_path or not zip_path.exists():
                    skill_install_failures.append(
                        {"skill": skill_name, "reason": "zip archive missing in bundle"}
                    )
                    continue
                tgt = prepare_archive_target(user_id, skill_name)
                if not tgt.exists():
                    await asyncio.to_thread(shutil.copy2, zip_path, tgt)
                key = install_cache_key(s)
                cached_dir = install_cache.get(key) if key else None
                first_aid = target_aids[0]
                if cached_dir is None:
                    sm = skill_workspace(first_aid, user_id)
                    # Pin the dest folder to the bundle's known skill_dir so it
                    # isn't re-derived (wrongly) from SKILL.md frontmatter.
                    info = await asyncio.to_thread(
                        sm.install_skill, zip_path, target_dir_name=s.get("skill_dir")
                    )
                    cached_dir = Path(info.path)
                    if key:
                        install_cache[key] = cached_dir
                else:
                    await asyncio.to_thread(
                        _copy_skill_to_agent, cached_dir, first_aid, skill_name
                    )
                for new_aid in target_aids[1:]:
                    await asyncio.to_thread(
                        _copy_skill_to_agent, cached_dir, new_aid, skill_name
                    )
                # `tgt` is ALREADY the archive location (we copied the bundle's
                # zip there above), so register it directly — same shape as the
                # full_copy branch below. Routing this through
                # `backup_after_api_install` was a latent bug: that helper is
                # for "copy an incoming zip INTO the registry", so it recomputed
                # the same `archive_target` and did `copy2(tgt, tgt)` →
                # `SameFileError` → swallowed by its broad `except` → the
                # `skill_archives` row was never written. Effect: an imported
                # zip-method skill could never be re-exported as `zip` (it
                # silently degraded to full_copy, which ships secrets under full
                # mode). Verified by `test_imported_zip_skill_registers_archive`.
                # sha256 is computed from the file on disk, NOT taken from the
                # manifest. The manifest value is unreliable in two ways: it is
                # the de-dup sentinel `"shared"` for entries 2..N when several
                # agents reference one skill (see builder's copied_zip_ref), and
                # when `tgt` already existed we kept the old bytes above and
                # never copied, so the manifest digest describes a different
                # file. This column's only job is integrity — a value that is
                # sometimes a literal word is worse than an extra hash.
                await register_archive(
                    user_id=user_id,
                    skill_name=skill_name,
                    source_type="zip",
                    source_url=None,
                    archive_path=str(tgt),
                    sha256=await asyncio.to_thread(file_sha256, tgt),
                )
                written_summary["skills_imported"] += 1

            elif method == "full_copy":
                # full_copy is per-agent: each agent's archive_ref points at
                # skills/<agent_id>/<skill>-full.zip with that agent's own
                # .skill_meta.json + wallets.
                archive_ref = s.get("archive_ref")
                zip_path = _bundle_member_path(work_dir, archive_ref)
                if not zip_path or not zip_path.exists():
                    skill_install_failures.append(
                        {"skill": skill_name, "reason": "full_copy archive missing in bundle"}
                    )
                    continue
                # Stash a copy in skill_archives for re-export (uses last-seen).
                tgt = prepare_archive_target(user_id, skill_name, suffix="_full.zip")
                await asyncio.to_thread(shutil.copy2, zip_path, tgt)
                for new_aid in target_aids:
                    sm = skill_workspace(new_aid, user_id)
                    # Pin the dest folder to the bundle's known skill_dir so the
                    # full_copy overwrites skills/<skill_dir>/ (restoring the
                    # credential the workspace snapshot had stripped) instead of
                    # landing in a temp-derived name.
                    await asyncio.to_thread(
                        sm.install_skill, zip_path, target_dir_name=s.get("skill_dir")
                    )
                await register_archive(
                    user_id=user_id,
                    skill_name=skill_name,
                    source_type="zip",
                    source_url=None,
                    archive_path=str(tgt),
                    sha256=s.get("sha256", "imported"),
                )
                written_summary["skills_imported"] += 1

            elif method == "builtin":
                written_summary["skills_imported"] += 1

            else:
                skill_install_failures.append(
                    {"skill": skill_name, "reason": f"unknown install_method '{method}'"}
                )
        except Exception as e:
            logger.exception(f"skill install failed for '{skill_name}' on {old_aid}: {e}")
            skill_install_failures.append({"skill": f"{skill_name}@{old_aid}", "reason": str(e)})

    if skill_install_failures:
        written_summary["warnings"].extend([
            f"skill install failed: {f['skill']} — {f['reason']}"
            for f in skill_install_failures
        ])
        written_summary["skill_install_failures"] = skill_install_failures
        for f in skill_install_failures:
            logger.warning(f"bundle_import.skill.failure skill={f['skill']!r} reason={f['reason']!r}")
    logger.info(
        f"bundle_import.skills.done installed={written_summary['skills_imported']} "
        f"failures={len(skill_install_failures)}"
    )

    # -- mcp_hints / mcp_urls --
    #
    # Pre-1.1 behavior was "show hints, let user manually re-create MCP rows".
    # 1.1+ writes directly into mcp_urls: when the bundle author opted MCPs
    # into the bundle, the recipient gets working rows immediately and the
    # background MCP poller re-validates each URL (connection_status reset to
    # None, last_check_time/last_error cleared). We keep mcp_hints_data on the
    # response so the frontend can still surface "what was added" to the user.
    #
    # 1.0 bundles auto-included every closure-agent MCP without user consent;
    # gating write-through on 1.1+ avoids surprise-creating rows on the
    # recipient when they import an older bundle. Hint-only fallback is
    # preserved for them.
    bundle_version_str = str(manifest.get("bundle_format_version") or "1.0")
    try:
        _bv_parts = bundle_version_str.split(".")
        _bv_major = int(_bv_parts[0])
        _bv_minor = int(_bv_parts[1]) if len(_bv_parts) > 1 else 0
    except (ValueError, IndexError):
        _bv_major, _bv_minor = 1, 0
    mcp_write_through_enabled = (_bv_major, _bv_minor) >= (1, 1)

    mcp_hints_path = work_dir / "mcp_hints.json"
    if mcp_hints_path.exists():
        try:
            hints = json.loads(mcp_hints_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            logger.warning(f"bundle_import.mcp_hints.read_failed reason={e}")
            hints = []
        written_summary["mcp_hints"] = len(hints)
        written_summary["mcp_hints_data"] = hints  # frontend surfaces the list

        if not mcp_write_through_enabled:
            logger.info(
                f"bundle_import.mcp.skip_writethrough bundle_version={bundle_version_str} "
                f"hint_rows={len(hints)} (legacy bundle — hint-only)"
            )
            hints = []  # short-circuit the write loop below

        for hint in hints:
            # rewrite_row handles mcp_id + agent_id via STRUCTURED_ID_FIELDS;
            # for legacy 1.0 hints (no mcp_id field) we mint one inline.
            row: Dict[str, Any] = {
                "mcp_id": hint.get("mcp_id"),
                "agent_id": hint.get("agent_id"),
                "user_id": user_id,  # recipient
                "name": hint.get("name") or "",
                "url": hint.get("url") or "",
                "description": hint.get("description"),
                "is_enabled": int(hint.get("is_enabled", 1) or 0),
                # Reset health so the poller re-validates against THIS instance.
                "connection_status": None,
                "last_check_time": None,
                "last_error": None,
                "metadata": hint.get("metadata"),
            }
            new_row = rewrite_row("mcp_urls", row)
            # Legacy bundles without mcp_id: mint one now (rewrite_row passes
            # None through unchanged).
            if not new_row.get("mcp_id"):
                new_row["mcp_id"] = gen_new_id("mcp")
            # agent_id may not be in id_map for malformed bundles; skip those.
            if not new_row.get("agent_id"):
                logger.warning(
                    f"bundle_import.mcp.skip reason=agent_id_missing mcp_id={new_row.get('mcp_id')}"
                )
                continue
            # SSRF: a bundle is attacker-supplied and this write bypasses the
            # create/update route's screen. Don't let it plant an internal MCP
            # URL that the agent would later fetch (cloud only — the same
            # DNS-free screen the route applies; local/desktop keeps localhost).
            # `is_obviously_non_public_url` is parse-safe: a malformed/garbage
            # URL is screened out, NOT thrown — a raw urlparse here would raise
            # (bad IPv6 / non-string) and roll the whole import back.
            if is_cloud_mode() and is_obviously_non_public_url(new_row.get("url")):
                try:
                    _host = urlparse(new_row.get("url") or "").hostname or "(none)"
                except Exception:  # noqa: BLE001
                    _host = "(unparseable)"
                logger.warning(
                    f"bundle_import.mcp.skip reason=non_public_url mcp_id={new_row.get('mcp_id')}"
                )
                written_summary["warnings"].append(
                    f"MCP {new_row.get('name') or new_row.get('mcp_id')}: "
                    f"host {_host!r} is not a public endpoint — skipped"
                )
                continue
            try:
                await _ins("mcp_urls", new_row)
                written_summary["mcp_urls_created"] += 1
            except Exception as me:
                logger.warning(f"bundle_import.mcp.insert_failed reason={me}")
                written_summary["warnings"].append(
                    f"mcp_urls insert failed for {new_row.get('name')}: {me}"
                )
        logger.info(
            f"bundle_import.mcp.done created={written_summary['mcp_urls_created']} "
            f"hint_rows={len(hints)}"
        )

    # ---- Verification pass: re-query DB for the new agents and confirm row counts
    # match what we think we wrote. Helps catch silent failures that didn't raise.
    verification: Dict[str, Any] = {"per_agent": [], "discrepancies": []}
    new_agent_ids = [id_map[old] for old in manifest.get("agents", []) if old in id_map]
    for naid in new_agent_ids:
        # Count rows in major tables for this agent
        n_narr = len(await db.get("narratives", {"agent_id": naid}))
        n_evt = len(await db.get("events", {"agent_id": naid}))
        n_inst = len(await db.get("module_instances", {"agent_id": naid}))
        n_msg = len(await db.get("agent_messages", {"agent_id": naid}))
        n_job = len(await db.get("instance_jobs", {"agent_id": naid}))
        agent_inst_ids = [r["instance_id"] for r in await db.get("module_instances", {"agent_id": naid})]
        n_aware = 0
        n_social = 0
        from narranexus.platform.repository import SocialNetworkRepository
        _social_repo = SocialNetworkRepository(db)
        for iid in agent_inst_ids:
            n_aware += len(await db.get("instance_awareness", {"instance_id": iid}))
            n_social += len(await _social_repo.get_all_entities(iid, limit=100000))
        per = {
            "agent_id": naid,
            "narratives": n_narr, "events": n_evt, "instances": n_inst,
            "messages": n_msg, "jobs": n_job, "awareness": n_aware,
            "social_entities": n_social,
        }
        verification["per_agent"].append(per)
        logger.info(
            f"bundle_import.verify.agent new_id={naid} "
            + " ".join(f"{k}={v}" for k, v in per.items() if k != "agent_id")
        )
    written_summary["verification"] = verification

    # ---- Backfill the unified-memory SEARCH INDEXES for the imported agents.
    # The raw inserts above bypass the live projection-write points
    # (crud._index_narrative / step_4 interaction / create_job / send_message),
    # so without this an imported narrative/job/bus/interaction is invisible to
    # `remember` until it is re-touched. Covers BOTH old bundles (which predate
    # the indexes) and current ones (same raw-insert path). Best-effort +
    # per-agent isolation: one agent's failure never aborts the import. Scoped to
    # this import — new_agent_ids are freshly minted, so every row under them
    # came from this bundle.
    from narranexus.platform.memory.backfill import backfill_agent_search_indexes
    _bf_total = 0
    for naid in new_agent_ids:
        try:
            _bf_total += await backfill_agent_search_indexes(db, naid)
        except Exception as e:  # noqa: BLE001 — index backfill is best-effort enrichment
            logger.warning(f"bundle_import.backfill failed for agent {naid}: {e}")
    written_summary["search_indexes_backfilled"] = _bf_total
    if dropped_legacy_columns:
        written_summary["dropped_legacy_columns"] = dropped_legacy_columns
        logger.info(
            "bundle_import.legacy_columns.dropped "
            + " ".join(f"{k}={v}" for k, v in sorted(dropped_legacy_columns.items()))
        )

    # ---- Final summary log: one line that captures everything important.
    duration_ms = int((time.monotonic() - _t0) * 1000)
    summary_kv = " ".join(
        f"{k}={v}" for k, v in sorted(written_summary.items())
        if isinstance(v, int)
    )
    fail_count = len(written_summary.get("warnings") or [])
    logger.info(
        f"bundle_import.done duration_ms={duration_ms} "
        f"id_map_size={len(id_map)} {summary_kv} warnings={fail_count}"
    )

    # cleanup
    try:
        shutil.rmtree(work_dir, ignore_errors=True)
    finally:
        await db.delete("bundle_preflight_sessions", {"token": preflight_token})

    return written_summary


def artifact_relpath_or_raise(fp: str) -> str:
    """Validate an ``artifacts.json`` ``file_path`` from a bundle.

    The value is attacker-controlled. It must be a plain workspace-relative
    path: no ``..`` segment, no backslash, no NUL, nothing empty. A leading
    slash is tolerated (older bundles wrote it) and stripped. Anything else
    aborts the import — a bad pointer would otherwise become a row whose raw
    / edit / upload paths point outside the new agent's workspace.
    """
    from narranexus.platform.utils.file_safety import validate_zip_member_path
    if "\\" in (fp or ""):
        raise ValueError(f"artifact file_path must not contain backslashes: {fp!r}")
    rel = (fp or "").lstrip("/")
    validate_zip_member_path(rel)  # raises ValueError on empty / NUL / '..' / absolute
    return rel


def rewrite_artifact_row(new_ar: dict, new_aid: str, user_id: str) -> dict:
    """Re-home one ``artifacts.json`` row (already id-rewritten) onto the new agent.

    The bundle's ``file_path`` is workspace-relative and attacker-controlled:
    it is validated (:func:`artifact_relpath_or_raise`, ValueError aborts the
    import) and re-prefixed with the recipient's workspace so the DB convention
    (``file_path`` relative to ``base_working_path``) holds. Sessions are not
    portable across instances, so session ids are cleared and the row pinned so
    the user can still find it in Settings -> Artifacts. Mutates and returns
    ``new_ar``.
    """
    from narranexus.platform.utils.workspace_paths import agent_workspace_relpath
    fp = artifact_relpath_or_raise(new_ar["file_path"]) if new_ar.get("file_path") else ""
    if fp:
        new_ar["file_path"] = f"{agent_workspace_relpath(new_aid, user_id)}/{fp}"
    new_ar["user_id"] = user_id
    new_ar["session_id"] = None
    new_ar["original_session_id"] = None
    new_ar["pinned"] = 1
    new_ar.pop("created_at", None)
    new_ar.pop("updated_at", None)
    return new_ar


def copy_skill_into_agent(
    src_skill_dir: Path, target_aid: str, user_id: str, skill_name: str, *, base: str
) -> Path:
    """Copy an already-installed skill dir into ``{target agent}/skills/{name}``.

    Module-level (not a closure) so the symlink behaviour is testable: the
    name is sanitised and confined, an existing target is replaced, and the
    copy never dereferences links (see :func:`copy_skill_dir`).
    """
    from narranexus.platform.utils.file_safety import ensure_within_directory, sanitize_filename
    from narranexus.platform.utils.workspace_paths import agent_workspace_path
    skills_base = agent_workspace_path(target_aid, user_id, base=base) / "skills"
    skills_base.mkdir(parents=True, exist_ok=True)
    safe_name = sanitize_filename(skill_name, label="skill name")
    target = ensure_within_directory(skills_base, safe_name, label="skill name")
    if target.exists():
        shutil.rmtree(target)
    copy_skill_dir(src_skill_dir, target)
    return target


def copy_skill_dir(src: Path, dst: Path) -> None:
    """Copy an installed skill dir WITHOUT dereferencing symlinks.

    ``copytree`` defaults to following links, which turns a link planted in a
    cloned repo (``leak.txt -> /etc/passwd``) into a real file in the target
    workspace. Links are copied as links; the executor's workspace confinement
    then decides whether they resolve.
    """
    shutil.copytree(src, dst, symlinks=True)


def _extract_tar_safely(tar_path: Path, target: Path) -> None:
    """Synchronous tar.gz extractor with traversal/symlink guards.
    Caller wraps in asyncio.to_thread() — extraction is CPU+IO bound."""
    with tarfile.open(tar_path, "r:gz") as tar:
        safe_members = []
        for member in tar.getmembers():
            # Regular files and directories only: links are dropped (a link
            # target is attacker-chosen), devices/FIFOs are dropped (2026-10-28
            # — they used to be extracted).
            if not (member.isfile() or member.isdir()):
                continue
            if member.name.startswith("/") or ".." in member.name.split("/"):
                continue
            safe_members.append(member)
        # The stdlib "data" filter is the second fence: it strips setuid/
        # setgid/sticky bits and refuses anything the pass above missed.
        tar.extractall(target, members=safe_members, filter="data")


# Extensions whose contents we Layer-4 scan for ID rewrite.
_TEXT_EXTENSIONS = {".md", ".txt", ".json", ".jsonl", ".yaml", ".yml", ".csv", ".log"}
_MAX_TEXT_REWRITE_SIZE = 5 * 1024 * 1024  # 5 MB per file — anything bigger we skip


def _rewrite_workspace_text_files(root: Path, id_map: Dict[str, str], user_id: str = "") -> None:
    """Walk extracted workspace, regex-rewrite known ID kinds in text files,
    and replace <original_owner> placeholder with the recipient's user_id.
    Layer 4 of PRD §8.11. Sync — caller wraps in asyncio.to_thread()."""
    if not id_map and not user_id:
        return
    rgx = build_all_id_regex()
    placeholder = "<original_owner>"

    def replace_id(m):
        old = m.group()
        return id_map.get(old, old)

    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix.lower() not in _TEXT_EXTENSIONS:
            continue
        try:
            if path.stat().st_size > _MAX_TEXT_REWRITE_SIZE:
                continue
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
        except (OSError, UnicodeDecodeError):
            continue
        new_content = rgx.sub(replace_id, content) if id_map else content
        if user_id and placeholder in new_content:
            new_content = new_content.replace(placeholder, user_id)
        if new_content != content:
            try:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(new_content)
            except OSError:
                pass


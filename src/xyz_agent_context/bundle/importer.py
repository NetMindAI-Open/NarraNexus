"""
@file_name: importer.py
@author: NetMind.AI
@date: 2026-05-08
@description: Bundle import pipeline — preflight + confirm

Pipeline (PRD §8.4):
1. Form check (size, zip, manifest)
2. Security (zip-bomb, traversal, sha256)
3. Compatibility (schema_version, embedding compat)
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
from typing import Any, Dict, List, Optional
from loguru import logger

from xyz_agent_context.utils.db_factory import get_db_client
from .id_field_map import STRUCTURED_ID_FIELDS, gen_new_id
from .id_schema import build_all_id_regex, ID_KINDS
from .security import (
    extract_zip_safely,
    file_sha256,
    bytes_sha256,
    MAX_BUNDLE_BYTES,
)


# Preflight session TTL — older sessions are pruned every preflight call.
PREFLIGHT_TTL_HOURS = 6


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
    Returns: {preflight_token, manifest, warnings, name_clashes, embedding_compat}"""
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
    from xyz_agent_context.bundle._bundle_migrations import (
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

    # Embedding compatibility — manifest's value vs current instance's default.
    # Hard-block when the dim is incompatible (the embeddings_store column would
    # mismatch and break semantic search). When provider/model differ but dim
    # matches, surface a soft warning suggesting a rebuild.
    from xyz_agent_context.settings import settings as core_settings
    from xyz_agent_context.agent_framework.model_catalog import get_embedding_dimensions

    bundle_emb = manifest.get("embedding", {}) or {}
    bundle_dim = bundle_emb.get("dim")
    bundle_provider = bundle_emb.get("provider")
    bundle_model = bundle_emb.get("model")

    local_model = core_settings.openai_embedding_model
    local_dim = get_embedding_dimensions(local_model) if local_model else None
    local_provider = "openai"  # current implementation hard-codes openai

    embedding_compat: Dict[str, Any] = {
        "manifest": bundle_emb,
        "local": {"provider": local_provider, "model": local_model, "dim": local_dim},
        "compatible": True,
        "advice": "Embeddings from the bundle should work as-is.",
    }

    if bundle_dim is not None and local_dim is not None and bundle_dim != local_dim:
        # Check whether the bundle ACTUALLY ships any embeddings. If it
        # doesn't (e.g. agents never had RAG / chat embedding rows), dim
        # mismatch is irrelevant — let the import proceed with a soft warning.
        rag_path = work_dir / "rag.json"  # legacy path (unused now but keep for compat)
        bundle_has_embeddings = False
        for adir in (work_dir / "agents").iterdir() if (work_dir / "agents").exists() else []:
            arag = adir / "rag.json"
            if arag.exists():
                try:
                    if json.loads(arag.read_text(encoding="utf-8")):
                        bundle_has_embeddings = True
                        break
                except Exception:
                    pass
        if not bundle_has_embeddings:
            embedding_compat["advice"] = (
                f"NOTE: bundle was tagged with dim={bundle_dim} but ships no actual "
                f"embedding rows. Your instance uses dim={local_dim}. Import will "
                "proceed; future embeddings will use your local model."
            )
        else:
            # Hard block — different vector dimensions means the embeddings_store
            # column cannot be reused, RAG / chat embeddings would be silently broken.
            embedding_compat["compatible"] = False
            embedding_compat["advice"] = (
                f"BLOCKED: bundle uses embeddings of dimension {bundle_dim} but this "
                f"instance is configured for dimension {local_dim} (model={local_model}). "
                "Importing would corrupt the RAG store. Either reconfigure this instance "
                "to use a model with matching dim, or ask the bundle author to re-export "
                "with a compatible model."
            )
            shutil.rmtree(work_dir, ignore_errors=True)
            raise ValueError(embedding_compat["advice"])

    if bundle_provider and bundle_provider != local_provider:
        embedding_compat["advice"] = (
            f"WARNING: bundle was exported with embedding provider '{bundle_provider}'; "
            f"this instance uses '{local_provider}'. Existing embeddings will be kept "
            "but you may want to rebuild via Settings → Embedding Index."
        )
    elif bundle_model and bundle_model != local_model:
        embedding_compat["advice"] = (
            f"WARNING: bundle was exported with embedding model '{bundle_model}'; "
            f"this instance uses '{local_model}'. Vectors are dimensionally compatible "
            f"({local_dim}), but semantic space differs slightly. Optional rebuild via "
            "Settings → Embedding Index for best results."
        )

    token = uuid.uuid4().hex
    summary = {
        "preflight_token": token,
        "manifest": manifest,
        "name_clashes": name_clashes,
        "team_clash": team_clash,
        "embedding_compat": embedding_compat,
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


async def confirm(preflight_token: str, user_id: str) -> Dict[str, Any]:
    """Execute the actual import using a previously preflighted bundle.

    Emits structured log lines prefixed `bundle_import.<event>` throughout
    so a complete import can be reconstructed from the backend log via
    `grep bundle_import.* backend.log`. Each log line is one event; values
    are key=value pairs for easy parsing.
    """
    import time
    _t0 = time.monotonic()

    db = await get_db_client()
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
        "rag_rows_created": 0,
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
        "warnings": [],
    }

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
        await db.insert("teams", {
            "team_id": new_tid,
            "owner_user_id": user_id,
            "name": team_name,
            "description": team.get("description"),
            "color": team.get("color"),
            "source": "bundle",
            "intro_md": intro,
        })
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

        original_name = agent_record["agent_name"]
        deduped_name = await dedupe_name(
            "agents", "agent_name", {"created_by": user_id}, original_name
        )
        renamed = (deduped_name != original_name)
        if renamed:
            written_summary["agents_renamed"] += 1

        # Insert agents row (new agent_id, current user_id)
        new_agent_row = rewrite_row("agents", agent_record)
        new_agent_row["agent_id"] = new_aid
        new_agent_row["agent_name"] = deduped_name
        new_agent_row["created_by"] = user_id
        new_agent_row.pop("agent_create_time", None)
        new_agent_row.pop("agent_update_time", None)
        await db.insert("agents", new_agent_row)
        written_summary["agents_created"] += 1
        rename_part = f"renamed_from={original_name!r}" if renamed else "renamed=no"
        logger.info(
            f"bundle_import.agent.created old_id={old_aid} new_id={new_aid} "
            f"name={deduped_name!r} {rename_part}"
        )

        # team_members
        if new_team_id:
            await db.insert("team_members", {"team_id": new_team_id, "agent_id": new_aid})

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
                await db.insert("narratives", new_nrow)
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
                            await db.insert("events", new_erow)
                            written_summary["events_created"] += 1

        # instances. Filter out rows whose module_class isn't registered in
        # MODULE_MAP on this side: importing them produces zombie rows the
        # runtime would log "Unknown module type X, skipping" against on every
        # agent turn (and they'd be dead weight forever, since cascade-delete
        # only fires when the agent itself is deleted). Cascade-drop their
        # instance-scoped children (jobs / social / rag / memory / narrative_links)
        # by remembering the skipped instance_ids.
        from xyz_agent_context.module import MODULE_MAP
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
                    if mclass not in MODULE_MAP:
                        skipped_instance_ids.add(irec.get("instance_id") or "")
                        skipped_by_class[mclass] = skipped_by_class.get(mclass, 0) + 1
                        continue
                    new_irow = rewrite_row("module_instances", irec)
                    new_irow["user_id"] = user_id
                    new_irow.pop("created_at", None)
                    new_irow.pop("updated_at", None)
                    await db.insert("module_instances", new_irow)
                    written_summary["instances_created"] += 1
        if skipped_by_class:
            for cls_name, n in skipped_by_class.items():
                msg = (
                    f"agent {old_aid}: skipped {n} {cls_name} instance(s) — "
                    "module class not registered in this build"
                )
                written_summary["warnings"].append(msg)
                logger.warning(f"bundle_import.skip_unknown_module agent={old_aid} class={cls_name} count={n}")

        # social entities
        se_path = adir / "social_entities.json"
        if se_path.exists():
            for srec in json.loads(se_path.read_text(encoding="utf-8")):
                if srec.get("instance_id") in skipped_instance_ids:
                    continue
                new_sr = rewrite_row("instance_social_entities", srec)
                new_sr.pop("created_at", None)
                new_sr.pop("updated_at", None)
                # entity_id might be an agent_id in our closure
                if srec.get("entity_type") == "agent":
                    eid = new_sr.get("entity_id")
                    if eid in id_map:
                        new_sr["entity_id"] = id_map[eid]
                await db.insert("instance_social_entities", new_sr)
                written_summary["social_entities_created"] += 1

        # rag store
        rag_path = adir / "rag.json"
        if rag_path.exists():
            for rrec in json.loads(rag_path.read_text(encoding="utf-8")):
                if rrec.get("instance_id") in skipped_instance_ids:
                    continue
                new_rr = rewrite_row("instance_rag_store", rrec)
                new_rr.pop("created_at", None)
                new_rr.pop("updated_at", None)
                await db.insert("instance_rag_store", new_rr)
                written_summary["rag_rows_created"] += 1

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
                    await db.insert("agent_messages", new_mr)
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
                await db.insert("instance_awareness", new_ar)
                written_summary["awareness_rows_created"] += 1

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
                await db.insert("instance_jobs", new_jr)
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
                    await db.insert("instance_narrative_links", new_nl)
                    written_summary["narrative_links_created"] += 1
                except Exception as ex:
                    # Cross-agent / cross-file duplicate (case 2) — pair was
                    # inserted by an earlier agent's loop in this same confirm().
                    # We catch broadly because the underlying driver's exception
                    # type differs by backend (pymysql.IntegrityError on MySQL,
                    # sqlite3.IntegrityError via aiosqlite on SQLite). Re-raise
                    # anything that doesn't smell like a unique-constraint hit.
                    msg = str(ex).lower()
                    if "duplicate" in msg or "unique" in msg or "1062" in msg:
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
                    await db.insert(memory_table, new_mm)
                    written_summary["memory_rows_created"] += 1
                except Exception as me:
                    logger.warning(f"insert {memory_table} failed: {me}")

        # workspace tar — offload extraction to thread.
        ws_tar = adir / "workspace.tar.gz"
        ws_size_b = ws_tar.stat().st_size if ws_tar.exists() else 0
        ws_extracted = False
        ws_target = None
        if ws_tar.exists():
            from xyz_agent_context.settings import settings as core_settings
            target = Path(core_settings.base_working_path) / f"{new_aid}_{user_id}"
            target.mkdir(parents=True, exist_ok=True)
            await asyncio.to_thread(_extract_tar_safely, ws_tar, target)
            await asyncio.to_thread(_rewrite_workspace_text_files, target, id_map, user_id)
            ws_extracted = True
            ws_target = target

        # instance_artifacts (1.1+ bundles). The bundle's file_path is
        # workspace-relative (no `{aid}_{uid}/` prefix); re-prepend the
        # recipient's prefix so the DB convention (file_path relative to
        # settings.base_working_path) is preserved. We always reset session_id
        # / original_session_id to NULL and force pinned=1 — sessions are not
        # portable across instances, and pinning ensures the user can still
        # find the artifact in the Settings → Artifacts page after import.
        art_path = adir / "artifacts.json"
        if art_path.exists():
            try:
                for arec in json.loads(art_path.read_text(encoding="utf-8")):
                    new_ar = rewrite_row("instance_artifacts", arec)
                    fp = (new_ar.get("file_path") or "").lstrip("/")
                    if fp:
                        new_ar["file_path"] = f"{new_aid}_{user_id}/{fp}"
                    new_ar["user_id"] = user_id
                    new_ar["session_id"] = None
                    new_ar["original_session_id"] = None
                    new_ar["pinned"] = 1
                    new_ar.pop("created_at", None)
                    new_ar.pop("updated_at", None)
                    try:
                        await db.insert("instance_artifacts", new_ar)
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

        # Per-agent delta log so the user can see counts attributable to THIS agent
        delta = {
            k: written_summary[k] - before.get(k, 0)
            for k in (
                "narratives_created", "events_created", "instances_created",
                "messages_created", "social_entities_created", "rag_rows_created",
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
                    await db.insert("bus_channels", new_ch)
                    written_summary["bus_channels_created"] += 1
                except Exception as e:
                    logger.warning(f"bus_channels insert failed: {e}")
            for m in (bus.get("members") or []):
                new_m = rewrite_row("bus_channel_members", m)
                try:
                    await db.insert("bus_channel_members", new_m)
                    written_summary["bus_members_created"] += 1
                except Exception as e:
                    logger.warning(f"bus_channel_members insert failed: {e}")
            for ms in (bus.get("messages") or []):
                new_ms = rewrite_row("bus_messages", ms)
                new_ms.pop("created_at", None)
                try:
                    await db.insert("bus_messages", new_ms)
                    written_summary["bus_messages_created"] += 1
                except Exception as e:
                    logger.warning(f"bus_messages insert failed: {e}")
            for r in (bus.get("registry") or []):
                new_r = rewrite_row("bus_agent_registry", r)
                try:
                    await db.insert("bus_agent_registry", new_r)
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
                    await db.insert("inbox_table", new_ib)
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
    skill_archives_dir = Path.home() / ".nexusagent" / "skill_archives" / user_id
    skill_archives_dir.mkdir(parents=True, exist_ok=True)
    skill_install_failures: List[Dict[str, str]] = []

    from xyz_agent_context.module.skill_module.skill_module import SkillModule
    from xyz_agent_context.bundle.skill_backup import (
        backup_after_api_install,
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
    from xyz_agent_context.utils.file_safety import sanitize_filename, ensure_within_directory
    from xyz_agent_context.settings import settings as core_settings

    def install_cache_key(s_entry: dict) -> Optional[str]:
        m = s_entry.get("install_method")
        if m == "url":
            return f"url::{s_entry.get('skill_name')}::{s_entry.get('source_url')}::{s_entry.get('branch') or 'main'}"
        if m == "zip":
            return f"zip::{s_entry.get('skill_name')}::{s_entry.get('archive_ref')}"
        return None

    install_cache: Dict[str, Path] = {}  # cache_key → "first installed skill dir"

    def _copy_skill_to_agent(src_skill_dir: Path, target_aid: str, skill_name: str) -> None:
        """Copy an already-installed skill dir into a target agent's skills/ dir."""
        base = Path(core_settings.base_working_path) / f"{target_aid}_{user_id}" / "skills"
        base.mkdir(parents=True, exist_ok=True)
        safe_name = sanitize_filename(skill_name, label="skill name")
        target = ensure_within_directory(base, safe_name, label="skill name")
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(src_skill_dir, target)

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
                    sm = SkillModule(agent_id=first_aid, user_id=user_id)
                    info = await asyncio.to_thread(sm.install_from_github, src_url, branch)
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
                zip_path = work_dir / archive_ref if archive_ref else None
                if not zip_path or not zip_path.exists():
                    skill_install_failures.append(
                        {"skill": skill_name, "reason": "zip archive missing in bundle"}
                    )
                    continue
                tgt = skill_archives_dir / f"{skill_name}.zip"
                if not tgt.exists():
                    await asyncio.to_thread(shutil.copy2, zip_path, tgt)
                key = install_cache_key(s)
                cached_dir = install_cache.get(key) if key else None
                first_aid = target_aids[0]
                if cached_dir is None:
                    sm = SkillModule(agent_id=first_aid, user_id=user_id)
                    info = await asyncio.to_thread(sm.install_skill, zip_path)
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
                await backup_after_api_install(
                    user_id=user_id,
                    skill_name=skill_name,
                    source_type="zip",
                    source_url=None,
                    original_zip_path=tgt,
                )
                written_summary["skills_imported"] += 1

            elif method == "full_copy":
                # full_copy is per-agent: each agent's archive_ref points at
                # skills/<agent_id>/<skill>-full.zip with that agent's own
                # .skill_meta.json + wallets.
                archive_ref = s.get("archive_ref")
                zip_path = work_dir / archive_ref if archive_ref else None
                if not zip_path or not zip_path.exists():
                    skill_install_failures.append(
                        {"skill": skill_name, "reason": "full_copy archive missing in bundle"}
                    )
                    continue
                # Stash a copy in skill_archives for re-export (uses last-seen).
                tgt = skill_archives_dir / f"{skill_name}_full.zip"
                await asyncio.to_thread(shutil.copy2, zip_path, tgt)
                for new_aid in target_aids:
                    sm = SkillModule(agent_id=new_aid, user_id=user_id)
                    await asyncio.to_thread(sm.install_skill, zip_path)
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
            try:
                await db.insert("mcp_urls", new_row)
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
        n_rag = 0
        for iid in agent_inst_ids:
            n_aware += len(await db.get("instance_awareness", {"instance_id": iid}))
            n_social += len(await db.get("instance_social_entities", {"instance_id": iid}))
            n_rag += len(await db.get("instance_rag_store", {"instance_id": iid}))
        per = {
            "agent_id": naid,
            "narratives": n_narr, "events": n_evt, "instances": n_inst,
            "messages": n_msg, "jobs": n_job, "awareness": n_aware,
            "social_entities": n_social, "rag_rows": n_rag,
        }
        verification["per_agent"].append(per)
        logger.info(
            f"bundle_import.verify.agent new_id={naid} "
            + " ".join(f"{k}={v}" for k, v in per.items() if k != "agent_id")
        )
    written_summary["verification"] = verification

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


def _extract_tar_safely(tar_path: Path, target: Path) -> None:
    """Synchronous tar.gz extractor with traversal/symlink guards.
    Caller wraps in asyncio.to_thread() — extraction is CPU+IO bound."""
    with tarfile.open(tar_path, "r:gz") as tar:
        safe_members = []
        for member in tar.getmembers():
            if member.issym() or member.islnk():
                continue
            if member.name.startswith("/") or ".." in member.name.split("/"):
                continue
            safe_members.append(member)
        tar.extractall(target, members=safe_members)


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

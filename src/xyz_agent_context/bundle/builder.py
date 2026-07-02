"""
@file_name: builder.py
@author: NetMind.AI
@date: 2026-05-08
@description: Bundle export builder — serialize closure of selected agents into a .nxbundle zip

Pipeline (PRD §8.3 + §8.12):
1. Closure: only the selected agent_ids are exported
2. Drop external references (records pointing to agents NOT in closure)
3. Strip credentials (lark_credentials, user_providers, password_hash never leave)
4. Replace user_id with placeholder
5. Serialize 25 tables → manifest + per-agent dirs
6. Skill bundling: URL / Zip / Full Copy per skill
7. Workspace tar.gz with sensitive-pattern filter
8. Compute integrity sha256, zip into .nxbundle
"""

import asyncio
import io
import json
import os
import shutil
import tarfile
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set
from loguru import logger

from xyz_agent_context.utils.db_factory import get_db_client
from .security import (
    bytes_sha256,
    file_sha256,
    is_sensitive_path,
    is_volume_path,
    scan_zip_for_sensitive,
)


BUNDLE_FORMAT_VERSION = "1.1"


def _current_app_version() -> str:
    """The live NarraNexus app version stamped into every export's manifest, so
    a bundle records exactly which build produced it (was a stale hardcoded
    literal). Sourced from the package __version__ (= pyproject [project].version)."""
    try:
        from xyz_agent_context import __version__
        return __version__
    except Exception:  # noqa: BLE001 — never let version lookup break an export
        return "0.0.0+unknown"


# Tables that store per-agent state (closure-filtered).
AGENT_SCOPED_TABLES = [
    "agents",
    "events",
    "narratives",
    "agent_messages",
    "module_instances",
    "instance_jobs",
    "instance_artifacts",
    "module_report_memory",
    "lark_trigger_audit",
]

# Tables keyed by instance_id (filter via instance closure).
INSTANCE_SCOPED_TABLES = [
    "instance_social_entities",
    "instance_narrative_links",
    "instance_awareness",
    "instance_module_report_memory",
    "instance_json_format_memory",
    "instance_json_format_memory_chat",
]

# Tables to skip (credentials).
STRIPPED_TABLES = {
    "lark_credentials",
    "user_providers",
    "user_slots",
    "user_quotas",
    "users",
    "user_password_hash",
    "lark_seen_messages",
    "lark_trigger_audit",  # contains user/sender ids
}


def _scrub_narrative_chat_content(narrative_row: Dict[str, Any]) -> Dict[str, Any]:
    """Strip chat-derived content from a narratives row.

    Called when ``include_chat_history=False``. The narrative skeleton
    (id / type / agent_id / actors / name / instance refs / timestamps)
    stays so that jobs and instance_narrative_links keep resolving on
    import, but every column that carries past-conversation content is
    reset to its empty default.

    Background: until 2026-05-18 the "Include chat history" toggle in
    the bundle export UI gated events.jsonl and agent_messages.jsonl
    but not narrative.json. Real-world narrative_info blobs already
    contain verbatim recent-N message transcripts (via the agent's
    framing-prompt copy of the Matrix conversation history), so a
    bundle exported with chat history "disabled" still leaked the most
    recent rounds of dialogue. Same for dynamic_summary / topic_hint /
    topic_keywords — all distilled from past conversation by the LLM
    during NarrativeService.update_narrative.

    Fields scrubbed inside ``narrative_info`` (JSON string column):
      - ``description`` and ``current_summary`` → ""
      - ``name`` and ``actors`` preserved (non-chat metadata)

    Standalone columns scrubbed:
      - ``dynamic_summary``  →  "[]"
      - ``topic_keywords``   →  "[]"
      - ``topic_hint``       →  ""
      - ``event_ids``        →  "[]"  (events themselves aren't exported,
                                       so leaving dangling references on
                                       the row would be misleading)
    """
    row = dict(narrative_row)
    raw_info = row.get("narrative_info")
    if raw_info:
        if isinstance(raw_info, str):
            try:
                info = json.loads(raw_info)
            except json.JSONDecodeError:
                info = {}
        elif isinstance(raw_info, dict):
            info = raw_info
        else:
            info = {}
        if not isinstance(info, dict):
            info = {}
        scrubbed_info = {
            "name": info.get("name", ""),
            "description": "",
            "current_summary": "",
            "actors": info.get("actors", []),
        }
        row["narrative_info"] = json.dumps(scrubbed_info, ensure_ascii=False)
    row["dynamic_summary"] = "[]"
    row["topic_keywords"] = "[]"
    row["topic_hint"] = ""
    row["event_ids"] = "[]"
    return row


class SensitiveZipDetected(Exception):
    """Raised when a zip-source skill's archive contains sensitive paths and
    the caller has not confirmed via `accept_sensitive_zips=True`."""

    def __init__(self, hits: List[Dict[str, Any]]):
        self.hits = hits  # [{skill, hits: [path, ...]}, ...]
        super().__init__(
            f"{len(hits)} skill(s) contain sensitive files in their zip archive. "
            "User must explicitly accept before export proceeds."
        )


class ExportSelection:
    """Agent-scoped selection for what to include in the bundle."""

    def __init__(
        self,
        agent_ids: List[str],
        team_id: Optional[str] = None,
        team_intro_md: Optional[str] = None,
        skill_methods: Optional[List[Dict[str, Any]]] = None,
        social_entity_selection: Optional[Dict[str, List[str]]] = None,
        workspace_excludes: Optional[Dict[str, List[str]]] = None,
        include_chat_history: bool = True,
        accept_sensitive_zips: bool = False,
        narrative_selection: Optional[Dict[str, List[str]]] = None,
        event_selection: Optional[Dict[str, List[str]]] = None,
        job_selection: Optional[Dict[str, List[str]]] = None,
        bus_channel_selection: Optional[List[str]] = None,
        mcp_selection: Optional[Dict[str, List[str]]] = None,
        artifact_selection: Optional[Dict[str, List[str]]] = None,
    ):
        self.agent_ids = agent_ids
        self.team_id = team_id
        self.team_intro_md = team_intro_md or ""
        # skill_methods: list of per-(agent, skill) export specs.
        # Each entry: {agent_id, skill_name, install_method, source_url?,
        #              source_type?, branch?, archive_path?, manual_zip_path?}
        # The same skill name on N agents = N entries in this list, and the
        # builder serializes one bundle entry per row.
        self.skill_methods = skill_methods or []
        # social_entity_selection: { agent_id: [entity_id, ...] }
        # If None, default to: per-agent, all entities matching (team-name fuzzy).
        # If provided, use the given list verbatim.
        self.social_entity_selection = social_entity_selection
        # workspace_excludes: { agent_id: [relative_path, ...] }  (manual user de-checks)
        self.workspace_excludes = workspace_excludes or {}
        self.include_chat_history = include_chat_history
        # B6: explicit user opt-in to ship zip-archived skills containing
        # sensitive files (e.g. .env / wallet.json). Without this, builder
        # raises SensitiveZipDetected and the route returns 409 with the hits
        # so the frontend can show a confirmation modal.
        self.accept_sensitive_zips = accept_sensitive_zips
        # B2: per-agent narrative_id allowlist (None = include all).
        self.narrative_selection = narrative_selection
        # B2: per-narrative event_id allowlist (None = include all).
        self.event_selection = event_selection
        # P7: per-agent job_id allowlist (None = include all). Jobs whose
        # parent narrative is excluded are also auto-dropped (P4) regardless
        # of this allowlist, so a job_id won't ship if its narrative didn't.
        self.job_selection = job_selection
        # Bus channel allowlist. None = ship every owner-owned channel that
        # has at least one closure agent as a member (legacy/default behavior).
        # When provided, only channels in this list ship — but they still
        # must be owned by the user and have ≥1 closure-agent member.
        self.bus_channel_selection = bus_channel_selection
        # MCP allowlist per agent: {agent_id: [mcp_id, ...]}. Unlike most other
        # selections, default = empty (no MCP shipped). MCP URLs often point at
        # private deployments; users must opt in. None and {} both mean "no MCP".
        self.mcp_selection = mcp_selection
        # Artifact allowlist per agent: {agent_id: [artifact_id, ...]}.
        # None = include all (matches social/narrative semantics). Underlying
        # files travel inside workspace.tar.gz regardless of this allowlist;
        # deselecting an artifact just drops the DB pointer row from the bundle.
        self.artifact_selection = artifact_selection


async def build_bundle(
    user_id: str,
    selection: ExportSelection,
    output_path: Path,
) -> Dict[str, Any]:
    """Build a .nxbundle file at output_path.
    Returns a manifest summary dict."""
    db = await get_db_client()

    closure_set: Set[str] = set(selection.agent_ids)
    if not closure_set:
        raise ValueError("agent_ids must not be empty")

    # 1. Validate: every agent must be owned by this user
    for aid in closure_set:
        row = await db.get_one("agents", {"agent_id": aid})
        if not row:
            raise ValueError(f"Agent not found: {aid}")
        if row["created_by"] != user_id:
            raise ValueError(f"Forbidden: agent {aid} not owned by {user_id}")

    warnings: List[str] = []
    # `info` is for expected, non-actionable events (e.g. closure dropped an
    # external agent reference). The frontend shows `len(warnings)`, not info,
    # so a clean export with thousands of dropped external edges no longer
    # looks like "1234 warnings 你完了".
    info: List[str] = []
    info_counters: Dict[str, int] = {"skipped_external_edge": 0}
    stripped_lists = ["api_keys", "lark_oauth", "user_password_hash", "user_providers"]

    # 2. Find all instance_ids belonging to closure agents
    instance_ids: Set[str] = set()
    for aid in closure_set:
        rows = await db.get("module_instances", {"agent_id": aid})
        for r in rows:
            instance_ids.add(r["instance_id"])

    # 3. Build a temp staging dir
    with tempfile.TemporaryDirectory() as tmpdir_str:
        tmpdir = Path(tmpdir_str)

        # ---- per-agent directories ----
        agents_summary: List[Dict[str, Any]] = []

        for aid in closure_set:
            agent_dir = tmpdir / "agents" / aid
            agent_dir.mkdir(parents=True, exist_ok=True)

            # agent.json
            agent_row = await db.get_one("agents", {"agent_id": aid})
            agent_record = _scrub_user_id(dict(agent_row), user_id, "agents")
            (agent_dir / "agent.json").write_text(
                json.dumps(agent_record, indent=2, ensure_ascii=False, default=str),
                encoding="utf-8",
            )

            # awareness — collected from instance_awareness rows
            awareness_rows = []
            for iid in instance_ids:
                rows = await db.get(
                    "instance_awareness",
                    {"instance_id": iid},
                )
                for r in rows:
                    inst = await db.get_one("module_instances", {"instance_id": iid})
                    if inst and inst.get("agent_id") == aid:
                        awareness_rows.append(r)
            (agent_dir / "awareness.json").write_text(
                json.dumps([_scrub_user_id(dict(r), user_id, "instance_awareness") for r in awareness_rows],
                           indent=2, ensure_ascii=False, default=str),
                encoding="utf-8",
            )

            # narratives + events — B2: respect per-agent narrative_selection
            # and per-narrative event_selection if provided. None = all.
            narratives_dir = agent_dir / "narratives"
            narratives_dir.mkdir(parents=True, exist_ok=True)
            n_rows = await db.get("narratives", {"agent_id": aid})
            allowed_nars = (
                selection.narrative_selection.get(aid)
                if selection.narrative_selection
                else None
            )
            for n in n_rows:
                if allowed_nars is not None and n["narrative_id"] not in allowed_nars:
                    continue
                ndir = narratives_dir / n["narrative_id"]
                ndir.mkdir(parents=True, exist_ok=True)
                # When the caller disabled chat history, strip every
                # chat-derived field from the narrative row before
                # writing — see _scrub_narrative_chat_content for the
                # exact list and rationale. The narrative skeleton
                # remains so jobs / instance_narrative_links still
                # resolve on import; only chat content is dropped.
                n_for_export = (
                    n
                    if selection.include_chat_history
                    else _scrub_narrative_chat_content(n)
                )
                (ndir / "narrative.json").write_text(
                    json.dumps(_scrub_user_id(dict(n_for_export), user_id, "narratives"),
                               indent=2, ensure_ascii=False, default=str),
                    encoding="utf-8",
                )
                if selection.include_chat_history:
                    e_rows = await db.get(
                        "events",
                        {"narrative_id": n["narrative_id"]},
                        order_by="created_at ASC",
                    )
                    # event_selection semantics (2026-05-18, opt-in
                    # default):
                    # - None  → ship all events (legacy "no selection"
                    #           default, kept for old clients)
                    # - {}    → ship NO events for any narrative (new
                    #           "user picked narratives but no events"
                    #           path). `if dict:` treats {} as falsy
                    #           and would silently fall back to "ship
                    #           all"; explicit `is not None` keeps
                    #           the distinction.
                    # - {nid: [...]} → ship only those event_ids for
                    #           nid; any narrative missing from the
                    #           dict ships 0 events (`.get(nid, [])`).
                    allowed_events = (
                        set(selection.event_selection.get(n["narrative_id"], []))
                        if selection.event_selection is not None
                        else None
                    )
                    e_path = ndir / "events.jsonl"
                    with open(e_path, "w", encoding="utf-8") as f:
                        for e in e_rows:
                            if allowed_events is not None and e["event_id"] not in allowed_events:
                                continue
                            scrubbed = _scrub_user_id(dict(e), user_id, "events")
                            f.write(json.dumps(scrubbed, ensure_ascii=False, default=str) + "\n")

            # module_instances + instance-scoped tables (filtered to this agent's instances)
            instances_dir = agent_dir / "instances"
            instances_dir.mkdir(parents=True, exist_ok=True)
            agent_instance_ids = []
            inst_rows = await db.get("module_instances", {"agent_id": aid})
            for r in inst_rows:
                agent_instance_ids.append(r["instance_id"])
                klass = r["module_class"]
                kdir = instances_dir / klass
                kdir.mkdir(parents=True, exist_ok=True)
                (kdir / f"{r['instance_id']}.json").write_text(
                    json.dumps(_scrub_user_id(dict(r), user_id, "module_instances"),
                               indent=2, ensure_ascii=False, default=str),
                    encoding="utf-8",
                )

            # Per-agent social entities (subject to social_entity_selection).
            # Entities live in the unified memory_entity table now — read via
            # the repo and serialize the SAME flat per-entity records the bundle
            # has always carried (content + closure/selection logic preserved;
            # only the underlying storage moved).
            from xyz_agent_context.repository import SocialNetworkRepository
            social_repo = SocialNetworkRepository(db)
            social_entities_for_agent = []
            for iid in agent_instance_ids:
                for e in await social_repo.get_all_entities(iid, limit=100000):
                    if selection.social_entity_selection is not None:
                        selected_ids = set(selection.social_entity_selection.get(aid, []))
                        if e.entity_id not in selected_ids:
                            continue
                    # Drop entities pointing to agent_ids OUTSIDE the closure
                    # (entity_type='agent'). Expected under strict closure
                    # (PRD §8.3 step 4) — count, don't flood warnings.
                    if e.entity_type == "agent" and e.entity_id not in closure_set:
                        info_counters["skipped_external_edge"] += 1
                        continue
                    social_entities_for_agent.append(_entity_to_flat(e))
            (agent_dir / "social_entities.json").write_text(
                json.dumps([_scrub_user_id(r, user_id, "social_entities") for r in social_entities_for_agent],
                           indent=2, ensure_ascii=False, default=str),
                encoding="utf-8",
            )

            # agent_messages
            msg_rows = await db.get("agent_messages", {"agent_id": aid}) if selection.include_chat_history else []
            (agent_dir / "agent_messages.jsonl").write_text(
                "\n".join(json.dumps(_scrub_user_id(dict(r), user_id, "agent_messages"),
                                     ensure_ascii=False, default=str) for r in msg_rows),
                encoding="utf-8",
            )

            # instance_jobs (per-agent jobs created by JobModule).
            # P4: drop jobs whose parent narrative is NOT in the user's
            # narrative selection (otherwise the job would point at a
            # narrative that doesn't exist in the bundle, leaving a
            # dangling FK on import).
            # P7: also honor explicit per-agent job_selection allowlist.
            allowed_jobs = (
                set(selection.job_selection.get(aid, []))
                if selection.job_selection else None
            )
            job_rows_raw = await db.get("instance_jobs", {"agent_id": aid})
            kept_jobs = []
            dropped_orphan_jobs = 0
            for jr in job_rows_raw:
                # If user gave an explicit job allowlist, honor it
                if allowed_jobs is not None and jr.get("job_id") not in allowed_jobs:
                    continue
                # If job's parent narrative is excluded, drop it
                jnar = jr.get("narrative_id")
                if jnar and allowed_nars is not None and jnar not in allowed_nars:
                    dropped_orphan_jobs += 1
                    continue
                kept_jobs.append(jr)
            if dropped_orphan_jobs:
                info.append(
                    f"agent {aid}: dropped {dropped_orphan_jobs} job(s) whose parent "
                    "narrative was not selected (auto-cascade)"
                )
            (agent_dir / "jobs.json").write_text(
                json.dumps([_scrub_user_id(dict(r), user_id, "instance_jobs") for r in kept_jobs],
                           indent=2, ensure_ascii=False, default=str),
                encoding="utf-8",
            )

            # instance_narrative_links (per-instance narrative bindings)
            nl_rows = []
            for iid in agent_instance_ids:
                nl_rows.extend(await db.get("instance_narrative_links", {"instance_id": iid}))
            (agent_dir / "instance_narrative_links.json").write_text(
                json.dumps([_scrub_user_id(dict(r), user_id, "instance_narrative_links") for r in nl_rows],
                           indent=2, ensure_ascii=False, default=str),
                encoding="utf-8",
            )

            # Per-instance memory family — keyed by instance_id.
            #
            # All four memory tables in this block carry chat-derived
            # content and are gated on `selection.include_chat_history`.
            # Until 2026-05-19 the gate was missing here, so a bundle
            # exported with "disable chat history" still leaked the
            # ChatModule's verbatim message store — exactly the leak
            # the toggle is supposed to prevent.
            #
            #   instance_json_format_memory_chat
            #     ChatModule's primary message store. `memory` is JSON
            #     `{"messages": [{role, content, timestamp}, ...]}` —
            #     query path: `get_chat_history` MCP tool
            #     (see module/chat_module/_chat_mcp_tools.py:73). On
            #     import, the message JSON travels intact even after
            #     instance_id rewrite; querying the imported agent
            #     surfaces the original user's dialogue word-for-word.
            #
            #   instance_json_format_memory
            #     Same shape, shared by Slack / Telegram / EventMemory
            #     for IM-style conversation cache.
            #
            #   instance_module_report_memory
            #     LLM-generated per-instance summaries of past dialogue.
            #
            #   module_report_memory  (handled separately below;
            #     narrative-keyed legacy version used by EventMemoryModule).
            #
            # When the gate is off we still emit empty JSON arrays so
            # the importer (which reads these files unconditionally and
            # tolerates missing files) sees well-formed input and creates
            # zero rows.
            for memory_table in (
                "instance_module_report_memory",
                "instance_json_format_memory",
                "instance_json_format_memory_chat",
            ):
                if selection.include_chat_history:
                    mem_rows = []
                    for iid in agent_instance_ids:
                        mem_rows.extend(await db.get(memory_table, {"instance_id": iid}))
                else:
                    mem_rows = []
                (agent_dir / f"{memory_table}.json").write_text(
                    json.dumps([_scrub_user_id(dict(r), user_id, memory_table) for r in mem_rows],
                               indent=2, ensure_ascii=False, default=str),
                    encoding="utf-8",
                )

            # Legacy module_report_memory — keyed by (narrative_id, module_name),
            # NOT by instance_id (the table predates per-instance memory; it's still
            # actively written by EventMemoryModule). Query per narrative instead.
            # Gated on include_chat_history for the same reason as the per-instance
            # family above — module reports are LLM-distilled past conversation.
            if selection.include_chat_history:
                mrm_rows = []
                for nrec in n_rows:
                    mrm_rows.extend(
                        await db.get("module_report_memory", {"narrative_id": nrec["narrative_id"]})
                    )
            else:
                mrm_rows = []
            (agent_dir / "module_report_memory.json").write_text(
                json.dumps([_scrub_user_id(dict(r), user_id, "module_report_memory") for r in mrm_rows],
                           indent=2, ensure_ascii=False, default=str),
                encoding="utf-8",
            )

            # instance_artifacts (pointer rows; actual files live in workspace
            # and ride along inside workspace.tar.gz). file_path is stored DB-
            # side relative to settings.base_working_path, which means it always
            # starts with `{aid}_{user_id}/...`. Strip that prefix here so the
            # bundle holds a clean workspace-relative path; the importer re-
            # prepends `{new_aid}_{recipient_uid}/` after rewrite.
            #
            # artifact_selection semantics: None = include all (matches social /
            # narrative defaults); per-agent allowlist filters by artifact_id.
            allowed_artifacts = (
                set(selection.artifact_selection.get(aid, []))
                if selection.artifact_selection is not None else None
            )
            art_rows_raw = await db.get("instance_artifacts", {"agent_id": aid})
            from xyz_agent_context.utils.workspace_paths import agent_workspace_relpath
            ws_prefix = f"{agent_workspace_relpath(aid, user_id)}/"
            legacy_flat_prefix = f"{aid}_{user_id}/"
            legacy_ws_prefix = f"{aid}_user_{user_id}/"
            artifact_rows_out: List[Dict[str, Any]] = []
            for r in art_rows_raw:
                if allowed_artifacts is not None and r.get("artifact_id") not in allowed_artifacts:
                    continue
                # Strip the workspace prefix from file_path BEFORE handing the
                # row to _scrub_user_id. The scrubber substring-replaces the
                # raw user_id with the `<original_owner>` placeholder inside
                # any non-ID string column, so doing it the other way around
                # would leave the file_path with a placeholder embedded in the
                # path segment (e.g. `agent_X_<original_owner>/...`) and our
                # prefix match would silently fail.
                raw = dict(r)
                fp = raw.get("file_path") or ""
                if fp.startswith(ws_prefix):
                    raw["file_path"] = fp[len(ws_prefix):]
                elif fp.startswith(legacy_flat_prefix):
                    raw["file_path"] = fp[len(legacy_flat_prefix):]
                elif fp.startswith(legacy_ws_prefix):
                    raw["file_path"] = fp[len(legacy_ws_prefix):]
                elif fp:
                    # Out-of-workspace pointer — should never happen under the
                    # pointer model, but if it does we keep the value verbatim
                    # and warn so the importer (and the user) can investigate.
                    warnings.append(
                        f"artifact {r.get('artifact_id')}: file_path outside agent "
                        "workspace, exported verbatim"
                    )
                rec = _scrub_user_id(raw, user_id, "instance_artifacts")
                artifact_rows_out.append(rec)
            (agent_dir / "artifacts.json").write_text(
                json.dumps(artifact_rows_out, indent=2, ensure_ascii=False, default=str),
                encoding="utf-8",
            )

            # workspace tar.gz
            ws_path = await _pack_workspace(aid, user_id, agent_dir,
                                            excludes=selection.workspace_excludes.get(aid, []))
            agents_summary.append({
                "agent_id": aid,
                "agent_name": agent_row["agent_name"],
                "narratives": len(n_rows),
                "instances": len(agent_instance_ids),
                "social_entities": len(social_entities_for_agent),
                "artifacts": len(artifact_rows_out),
                "workspace_size_bytes": ws_path.stat().st_size if ws_path else 0,
                "workspace_path": "workspace.tar.gz" if ws_path else None,
            })

        # ---- skills (per-(agent, skill) export) ----
        skills_dir = tmpdir / "skills"
        skills_dir.mkdir(parents=True, exist_ok=True)
        skills_summary = []
        zip_secrets_warnings: List[Dict[str, Any]] = []
        # De-dup zip-archive copies across multiple agents picking the same
        # zip-method skill: they'd reference the exact same source zip, so
        # one archive_ref is enough; per-agent bundle entries still each
        # point to it. Full-copy is always per-agent (different .skill_meta).
        copied_zip_ref: Dict[str, str] = {}  # skill_name → archive_ref already copied

        for cfg in (selection.skill_methods or []):
            agent_id = cfg.get("agent_id")
            skill_name = cfg.get("skill_name")
            # `skill_dir` is the filesystem-unique dir name within the agent's
            # skills/ folder. SKILL.md frontmatter `name` can duplicate across
            # multiple dirs under one agent (user installed the same skill twice
            # into different folders), so we need the dir name to disambiguate.
            skill_dir = cfg.get("skill_dir") or skill_name
            method = cfg.get("install_method")
            if not agent_id or agent_id not in closure_set:
                warnings.append(
                    f"skill {skill_name}: agent {agent_id} not in closure, skipping"
                )
                continue
            entry: Dict[str, Any] = {
                "agent_id": agent_id,
                "name": skill_name,
                # Always emit dir for the importer's reconstruction.
                "skill_dir": skill_dir,
                "install_method": method,
                "contains_secrets": method == "full_copy",
            }
            if method == "url":
                entry["source_url"] = cfg.get("source_url")
                entry["source_type"] = cfg.get("source_type", "github")
                entry["branch"] = cfg.get("branch", "main")
            elif method == "zip":
                # Two skills with the same SKILL.md `name` but different dirs
                # would collide in the de-dup cache if we keyed by name. Use
                # archive_path (or manual_zip) as part of the key so distinct
                # source bytes get distinct archive_ref entries in the bundle.
                src_zip = cfg.get("archive_path") or cfg.get("manual_zip_path")
                if not src_zip or not Path(src_zip).exists():
                    warnings.append(f"skill {skill_name} on {agent_id}: zip not found, skipping")
                    continue
                cache_key = f"{skill_name}|{src_zip}"
                if cache_key in copied_zip_ref:
                    entry["archive_ref"] = copied_zip_ref[cache_key]
                    entry["sha256"] = "shared"
                else:
                    hits = scan_zip_for_sensitive(Path(src_zip))
                    if hits:
                        zip_secrets_warnings.append({"skill": skill_name, "hits": hits})
                    # Use dir-based filename to disambiguate same-named-different-dir
                    # zips packed in the same bundle.
                    tgt_zip = skills_dir / f"{skill_dir}.zip"
                    if tgt_zip.exists():
                        # Defensive: another (different agent) entry already
                        # wrote a zip with this dir name. Append agent suffix.
                        tgt_zip = skills_dir / f"{skill_dir}__{agent_id}.zip"
                    await asyncio.to_thread(shutil.copy2, src_zip, tgt_zip)
                    archive_ref = f"skills/{tgt_zip.name}"
                    copied_zip_ref[cache_key] = archive_ref
                    entry["archive_ref"] = archive_ref
                    entry["sha256"] = await asyncio.to_thread(file_sha256, tgt_zip)
            elif method == "full_copy":
                # Per-agent: pack THIS specific agent's skill dir
                src_dir = await _find_skill_dir({agent_id}, user_id, skill_name, skill_dir)
                if not src_dir:
                    warnings.append(f"skill {skill_name} on {agent_id}: full_copy source not found")
                    continue
                # Use per-agent + dir-name path inside bundle so duplicate-named
                # skills under the same agent don't collide.
                per_agent_dir = skills_dir / agent_id
                per_agent_dir.mkdir(parents=True, exist_ok=True)
                tgt_zip = per_agent_dir / f"{skill_dir}-full.zip"
                await asyncio.to_thread(_zip_dir, src_dir, tgt_zip)
                entry["archive_ref"] = f"skills/{agent_id}/{skill_dir}-full.zip"
                entry["sha256"] = await asyncio.to_thread(file_sha256, tgt_zip)
            elif method == "builtin":
                pass
            elif method == "skip" or method is None:
                continue
            else:
                warnings.append(f"skill {skill_name} on {agent_id}: unknown install_method {method}")
                continue
            skills_summary.append(entry)

        if zip_secrets_warnings:
            if not selection.accept_sensitive_zips:
                # Force the caller (frontend) to surface a confirmation prompt
                # before we ship a bundle that contains user secrets.
                raise SensitiveZipDetected(zip_secrets_warnings)
            for w in zip_secrets_warnings:
                warnings.append(
                    f"sensitive_files_in_zip: {w['skill']} -> {','.join(w['hits'][:5])}"
                )

        # ---- README.md (Bundle Notes / team intro) ----
        if selection.team_intro_md.strip():
            (tmpdir / "README.md").write_text(selection.team_intro_md, encoding="utf-8")

        # ---- mcp_hints.json ----
        # MCP is opt-in (mcp_selection != None). When the user picks rows here
        # the bundle ships enough metadata for the importer to insert directly
        # into the recipient's mcp_urls table — connection_status is reset on
        # the import side so the poller re-validates against the new instance.
        #
        # Defaults: None / {} → no MCP shipped. Note this differs from social /
        # narrative defaults; MCP URLs frequently point at private services
        # the bundle author may not want to re-share.
        mcp_rows: List[Dict[str, Any]] = []
        mcp_allowlist = selection.mcp_selection or {}
        for aid in closure_set:
            chosen_ids = set(mcp_allowlist.get(aid) or [])
            if not chosen_ids:
                continue
            rows = await db.get("mcp_urls", {"agent_id": aid, "user_id": user_id})
            for r in rows:
                if r.get("mcp_id") not in chosen_ids:
                    continue
                mcp_rows.append({
                    "mcp_id": r.get("mcp_id"),
                    "agent_id": aid,
                    "name": r["name"],
                    "url": r["url"],
                    "description": r.get("description"),
                    "is_enabled": int(r.get("is_enabled") or 0),
                    # metadata may contain non-secret hints (display name, version);
                    # we ship it as-is. Use _scrub_user_id only via string columns.
                    "metadata": r.get("metadata"),
                })
        (tmpdir / "mcp_hints.json").write_text(
            json.dumps(mcp_rows, indent=2, ensure_ascii=False, default=str), encoding="utf-8"
        )

        # ---- bus.json (cross-agent message bus state, closure-scoped) ----
        # Channels owned by closure agents; channel members in closure;
        # bus messages whose channel is in closure; bus_agent_registry rows
        # for closure agents.
        bus_channels = []
        bus_channel_members = []
        bus_messages = []
        bus_agent_registry = []
        # Channels owned by an agent of this user. bus_channels.created_by
        # actually stores the AGENT_ID of the channel owner (see
        # local_bus.create_channel: `created_by = members[0] if members
        # else "system"`). The message_bus_trigger uses this agent_id to
        # implement "channel owner is always activated by new messages"
        # (msg_bus_trigger.py:154). So to find channels belonging to a user
        # we chain bus_channels.created_by → agents.agent_id → agents.created_by.
        # An earlier version of this query passed `user_id` as the value
        # of `created_by` — that silently dropped every agent-created
        # channel from bundles.
        owned_chs = await db.execute(
            """SELECT ch.*
               FROM bus_channels ch
               JOIN agents a ON ch.created_by = a.agent_id
               WHERE a.created_by = %s""",
            params=(user_id,),
            fetch=True,
        )
        # User-provided allowlist (channel_ids). None = include all.
        channel_allowlist: Optional[Set[str]] = (
            set(selection.bus_channel_selection)
            if selection.bus_channel_selection is not None
            else None
        )
        kept_channel_ids: Set[str] = set()
        for ch in owned_chs:
            cid = ch["channel_id"]
            if channel_allowlist is not None and cid not in channel_allowlist:
                continue
            members = await db.get("bus_channel_members", {"channel_id": cid})
            closure_members = [m for m in members if m["agent_id"] in closure_set]
            if not closure_members:
                continue
            kept_channel_ids.add(cid)
            bus_channels.append(_scrub_user_id(dict(ch), user_id, "bus_channels"))
            bus_channel_members.extend(_scrub_user_id(dict(m), user_id, "bus_channel_members") for m in closure_members)
        # Bus messages for kept channels
        for cid in kept_channel_ids:
            msgs = await db.get("bus_messages", {"channel_id": cid}, order_by="created_at ASC")
            bus_messages.extend(_scrub_user_id(dict(m), user_id, "bus_messages") for m in msgs)
        # bus_agent_registry per closure agent
        for aid in closure_set:
            row = await db.get_one("bus_agent_registry", {"agent_id": aid})
            if row:
                bus_agent_registry.append(_scrub_user_id(dict(row), user_id, "bus_agent_registry"))
        (tmpdir / "bus.json").write_text(
            json.dumps({
                "channels": bus_channels,
                "members": bus_channel_members,
                "messages": bus_messages,
                "registry": bus_agent_registry,
            }, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )

        # ---- inbox.json (per-user inbox entries linked to events in closure) ----
        # We can't migrate the whole inbox (it's per-USER not per-agent), but
        # we DO want to migrate inbox entries triggered by the closure's events.
        # On import we'll filter to event_id ∈ id_map.
        inbox_rows = await db.get("inbox_table", {"user_id": user_id})
        inbox_kept = []
        # Find which event_ids are in our closure
        closure_event_ids: Set[str] = set()
        for aid in closure_set:
            for ev in await db.get("events", {"agent_id": aid}):
                closure_event_ids.add(ev["event_id"])
        for ib in inbox_rows:
            if ib.get("event_id") and ib["event_id"] in closure_event_ids:
                inbox_kept.append(_scrub_user_id(dict(ib), user_id, "inbox_table"))
        (tmpdir / "inbox.json").write_text(
            json.dumps(inbox_kept, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )

        # ---- manifest ----
        team_meta = None
        if selection.team_id:
            team_row = await db.get_one("teams", {"team_id": selection.team_id})
            if team_row:
                team_meta = {
                    "team_id": team_row["team_id"],
                    "name": team_row["name"],
                    "description": team_row.get("description"),
                    "color": team_row.get("color"),
                    "source": "bundle",
                    "intro_md": selection.team_intro_md or team_row.get("intro_md") or "",
                }

        # Roll up info_counters into one human-readable info line per kind
        if info_counters["skipped_external_edge"]:
            info.append(
                f"skipped {info_counters['skipped_external_edge']} external entity reference(s) "
                "outside the bundle closure (expected — see PRD §8.3)"
            )

        manifest = {
            "bundle_format_version": BUNDLE_FORMAT_VERSION,
            "narranexus_version_exported": _current_app_version(),
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "owner_placeholder": "<original_owner>",
            # We DON'T store the original user_id by name — privacy. The
            # placeholder above is the canonical token in all bundle text /
            # JSON; importer swaps it for the recipient's user_id everywhere.
            "team": team_meta,
            "agents": list(closure_set),
            "agents_summary": agents_summary,
            "skills": skills_summary,
            "mcp_hints_count": len(mcp_rows),
            "artifacts_count": sum(s.get("artifacts", 0) for s in agents_summary),
            "stripped": stripped_lists,
            "warnings": warnings,
            "info": info,
            "info_counters": info_counters,
        }

        # Compute integrity sha256 over all non-manifest files (sorted) — heavy I/O,
        # offloaded to a worker thread so we don't block the event loop while
        # hashing large workspaces.
        manifest["integrity_sha256"] = await asyncio.to_thread(
            _compute_integrity_sha256, tmpdir
        )

        (tmpdir / "manifest.json").write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )

        # ---- zip into .nxbundle ---- (also CPU-heavy, off-loop)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(_zip_dir, tmpdir, output_path)

    return {
        "manifest": manifest,
        "warnings": warnings,
        "output_path": str(output_path),
    }


def _entity_to_flat(e) -> dict:
    """Serialize a SocialNetworkEntity to the flat per-entity record the bundle
    carries (same shape as the legacy instance_social_entities row; DB column
    'tags' = the entity's keywords). Keeps bundle content stable across the
    move to memory_entity storage."""
    return {
        "entity_id": e.entity_id,
        "entity_type": e.entity_type,
        "instance_id": e.instance_id,
        "entity_name": e.entity_name,
        "aliases": e.aliases,
        "entity_description": e.entity_description,
        "identity_info": e.identity_info,
        "contact_info": e.contact_info,
        "familiarity": e.familiarity,
        "relationship_strength": e.relationship_strength,
        "interaction_count": e.interaction_count,
        "last_interaction_time": e.last_interaction_time,
        "tags": e.keywords,
        "expertise_domains": e.expertise_domains,
        "related_job_ids": e.related_job_ids,
        "persona": e.persona,
        "extra_data": e.extra_data,
    }


def _scrub_user_id(row: dict, user_id: str, table: Optional[str] = None) -> dict:
    """Replace any user_id columns AND any free-text occurrences of user_id
    with the owner placeholder. Drop password_hash etc.

    The free-text substring replace must NOT touch columns that hold a
    structured ID — some legacy ID schemes embed the user's name (e.g.
    `agent_X_binliang_default_N-05` as a deterministic narrative_id). If we
    blindly replace `binliang` with `<original_owner>` inside that string,
    the dir name (used by importer's pre-collect) and the JSON content
    diverge, and the import-side rewrite misses the lookup → UNIQUE conflict
    on insert. Pass `table=` so we know which columns are ID-typed and
    leave them untouched.
    """
    from xyz_agent_context.bundle.id_field_map import STRUCTURED_ID_FIELDS
    id_cols: set = set()
    if table:
        id_cols = set(STRUCTURED_ID_FIELDS.get(table, {}).keys())

    out = dict(row)
    placeholder = "<original_owner>"
    for k in list(out.keys()):
        v = out[k]
        if k in ("password_hash", "secret", "api_key"):
            out.pop(k, None)
            continue
        if k.endswith("user_id") or k == "user_id" or k == "created_by":
            # If this column is also declared as a structured ID field
            # (e.g. bus_channels.created_by stores an agent_id, not a
            # user_id), defer to the ID-column branch below — substring
            # placeholder substitution would corrupt the cross-reference.
            # The importer's rewrite_row + free_text_regex maps the
            # agent_id from old → new on the receiving side.
            if k in id_cols:
                continue
            if v == user_id:
                out[k] = placeholder
            continue
        if k in id_cols:
            # ID columns are referenced by id_map / pre-collect. Leave them
            # exactly as stored; substring scrubbing would corrupt the
            # cross-reference on import. Free-text rewrite (Layer 4) on the
            # import side handles user_id appearance in non-ID columns.
            continue
        # Free-text: replace literal user_id substring with placeholder.
        if isinstance(v, str) and user_id and user_id in v:
            out[k] = v.replace(user_id, placeholder)
    return out


async def _pack_workspace(
    agent_id: str,
    user_id: str,
    agent_dir: Path,
    excludes: List[str],
) -> Optional[Path]:
    """Tar.gz agent's workspace dir to agent_dir/workspace.tar.gz; respects sensitive-pattern filter.
    The tarfile compression itself is offloaded to a worker thread so the main
    asyncio event loop stays responsive for other users during big workspaces."""
    # SINGLE-WORKER ASSUMPTION: workspaces live on this process's local fs.
    # Multi-pod scale requires shared volume (compose already mounts it
    # under /opt/narranexus/workspaces). See
    # .mindflow/project/references/scaling_assumptions.md §3.
    #
    # Canonical workspace path (settings.base_working_path / {agent_id}_{user_id})
    # comes from attachment_storage.get_workspace_path() and step_3_agent_loop.py.
    # Legacy `_user_<user_id>` infix kept as fallback for old install state.
    from xyz_agent_context.settings import settings as core_settings
    base = Path(core_settings.base_working_path)
    from xyz_agent_context.utils.workspace_paths import agent_workspace_relpath
    candidates = [
        base / agent_workspace_relpath(agent_id, user_id),   # canonical (current layout)
        base / f"{agent_id}_{user_id}",            # legacy flat (pre-nested migration)
        base / f"{agent_id}_user_{user_id}",       # legacy _user_ infix
    ]
    src = next((c for c in candidates if c.is_dir()), None)
    if not src:
        return None

    out = agent_dir / "workspace.tar.gz"
    excl_set = set(excludes or [])

    return await asyncio.to_thread(_pack_workspace_sync, src, out, excl_set, user_id)


def _pack_workspace_sync(src: Path, out: Path, excl_set: set, user_id: str = "") -> Path:
    text_extensions = {".md", ".txt", ".json", ".jsonl", ".yaml", ".yml", ".csv", ".log"}
    placeholder = "<original_owner>"
    # Memory cap on per-file user_id rewrite. Files larger than this are
    # added to the tar as-is (no in-memory rewrite). Picked at 5 MB so the
    # rewrite still covers awareness markdown / notes / chat logs but skips
    # giant log files / dumps that would balloon RAM during export.
    text_rewrite_max_bytes = 5 * 1024 * 1024

    def filter_func(tarinfo: tarfile.TarInfo) -> Optional[tarfile.TarInfo]:
        name = tarinfo.name
        if name.startswith("./"):
            name = name[2:]
        # never include symlinks
        if tarinfo.issym() or tarinfo.islnk():
            return None
        # user explicit excludes
        if name in excl_set:
            return None
        # sensitive default-skip
        if is_sensitive_path(name):
            return None
        # volume default-skip
        if is_volume_path(name):
            return None
        return tarinfo

    with tarfile.open(out, "w:gz") as tar:
        # If we need to scrub user_id from text files, walk manually so we can
        # rewrite their bytes before adding to tar. Otherwise, fast path.
        if user_id:
            for root, dirs, files in os.walk(src):
                for fn in files:
                    full = Path(root) / fn
                    rel_to_src = full.relative_to(src)
                    rel_str = str(rel_to_src).replace("\\", "/")
                    if rel_str in excl_set:
                        continue
                    if is_sensitive_path(rel_str) or is_volume_path(rel_str):
                        continue
                    if full.is_symlink():
                        continue
                    arcname = f"./{rel_str}"
                    if full.suffix.lower() in text_extensions:
                        try:
                            sz = full.stat().st_size
                        except OSError:
                            sz = 0
                        if 0 < sz <= text_rewrite_max_bytes:
                            try:
                                content = full.read_text(encoding="utf-8")
                            except (OSError, UnicodeDecodeError):
                                tar.add(full, arcname=arcname, filter=filter_func)
                                continue
                            if user_id in content:
                                content = content.replace(user_id, placeholder)
                                data = content.encode("utf-8")
                                ti = tarfile.TarInfo(name=arcname)
                                ti.size = len(data)
                                ti.mtime = full.stat().st_mtime
                                tar.addfile(ti, fileobj=io.BytesIO(data))
                                continue
                    tar.add(full, arcname=arcname, filter=filter_func)
        else:
            tar.add(src, arcname=".", filter=filter_func)
    return out


async def _find_skill_dir(
    agent_ids: Set[str],
    user_id: str,
    skill_name: str,
    skill_dir: Optional[str] = None,
) -> Optional[Path]:
    """Resolve the on-disk skill dir.
    Prefers the explicit `skill_dir` (filesystem-unique) over `skill_name`
    (frontmatter, can duplicate). Falls back to skill_name when callers
    don't yet pass skill_dir (legacy / pre-fix-for-duplicate-name)."""
    from xyz_agent_context.settings import settings as core_settings
    base = Path(core_settings.base_working_path)
    dir_name = skill_dir or skill_name
    for aid in agent_ids:
        from xyz_agent_context.utils.workspace_paths import agent_workspace_relpath
        candidates = [
            base / agent_workspace_relpath(aid, user_id) / "skills" / dir_name,   # canonical (current layout)
            base / f"{aid}_{user_id}" / "skills" / dir_name,              # legacy flat
            base / f"{aid}_user_{user_id}" / "skills" / dir_name,         # legacy _user_ infix
        ]
        for c in candidates:
            if c.is_dir():
                return c
    return None


def _zip_dir(src: Path, dst: Path) -> None:
    """Synchronous zip writer. Callers in async context MUST wrap in
    asyncio.to_thread() — zip compression is CPU-bound and blocks the event loop."""
    with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, _, files in os.walk(src):
            for fn in files:
                full = Path(root) / fn
                rel = full.relative_to(src)
                zf.write(full, arcname=str(rel))


def _compute_integrity_sha256(tmpdir: Path) -> str:
    """Walk tmpdir, sha256 each non-manifest file, fold into a single digest.
    Sync — caller wraps in asyncio.to_thread()."""
    all_paths = sorted(
        [p for p in tmpdir.rglob("*") if p.is_file() and p.name != "manifest.json"]
    )
    h = io.BytesIO()
    for p in all_paths:
        h.write(str(p.relative_to(tmpdir)).encode("utf-8"))
        h.write(b":")
        h.write(file_sha256(p).encode("utf-8"))
        h.write(b"\n")
    return bytes_sha256(h.getvalue())

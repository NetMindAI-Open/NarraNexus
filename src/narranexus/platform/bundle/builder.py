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

from narranexus.platform.utils.workspace_paths import refuse_symlinked_dir

from narranexus.platform.bundle.team_bulletin_transfer import (
    collect_bulletin_for_export,
)

from narranexus.platform.utils.db.db_factory import get_db_client
from narranexus.platform.utils.file_safety import ensure_within_directory, sanitize_filename
from .channel_credential_tables import CHANNEL_CREDENTIALS_KEY
from .security import (
    bytes_sha256,
    file_sha256,
    is_sensitive_path,
    is_volume_path,
    scan_zip_for_sensitive,
)
from .skill_secrets import dir_is_builtin as _dir_is_builtin
from .skill_backup import is_within_user_archive_dir


BUNDLE_FORMAT_VERSION = "1.1"


def _current_app_version() -> str:
    """The live NarraNexus app version stamped into every export's manifest, so
    a bundle records exactly which build produced it (was a stale hardcoded
    literal). Sourced from the package __version__ (= pyproject [project].version)."""
    try:
        from narranexus.platform import __version__

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
    # NB: `lark_trigger_audit` used to be listed here AND in STRIPPED_TABLES —
    # a contradiction. It is user/sender-id bearing and never exported; the
    # duplicate entry was dead + misleading, removed 2026-07-10.
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

# Tables to skip (credentials). NB: `lark_credentials` is skipped BY DEFAULT
# but shipped when ExportSelection.include_channel_credentials is set (opt-in) —
# see the per-agent channel_credentials.json export. The other user-scoped
# tables here are never exported.
STRIPPED_TABLES = {
    "lark_credentials",
    "channel_credentials",  # the generic IM credential table (opt-in via include_channel_credentials)
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


class BundleForbidden(ValueError):
    """The exporter does not own an agent or team named in the selection.

    An authorization verdict, not a fault: the export route maps this type to
    403 (by ``isinstance``, never by message text). Subclasses ``ValueError``
    so callers that already catch ``ValueError`` keep working. An UNKNOWN agent
    is deliberately not this type — it stays a plain ``ValueError``.
    """


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
        include_channel_credentials: bool = False,
        include_skill_secrets: bool = False,
    ):
        self.agent_ids = agent_ids
        self.team_id = team_id
        self.team_intro_md = team_intro_md or ""
        # skill_methods: list of per-(agent, skill) export specs.
        # Each entry: {agent_id, skill_name, skill_dir?, install_method,
        #              source_url?, source_type?, branch?}
        # The same skill name on N agents = N entries in this list, and the
        # builder serializes one bundle entry per row.
        # SEC-07: no archive path field by design — for install_method="zip"
        # the builder resolves the archive from `skill_archives` for the
        # exporting user. A caller-supplied path here was an arbitrary file
        # read (the route accepted it straight off the wire).
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
        # Opt-in: ship the IM channel credentials (Lark/Slack/Telegram/WeChat/
        # Discord/NarraMessenger) so a migrated agent's channels can be
        # re-activated in the new environment WITHOUT re-binding. Default OFF —
        # credentials are near-plaintext (base64) secrets. When on, the export
        # carries the six credential tables and the import lands them INACTIVE
        # (user must manually activate to claim the single WS slot). See
        # channel_credential_tables.py + the design doc.
        self.include_channel_credentials = include_channel_credentials
        # Opt-in (the "full mode" companion to include_channel_credentials): ship
        # skill secrets so a migrated agent's skills work without re-auth. Default
        # OFF — when off, per-skill `.skill_meta.json` env_config values are
        # scrubbed from the workspace snapshot AND full_copy skill archives are
        # sensitive-filtered, so no skill secret leaves silently. When on, both
        # ride along (base64 / raw files) and the export warns.
        self.include_skill_secrets = include_skill_secrets


async def _export_work_items(db, team_id: str) -> list:
    """The team's unfinished work board, id-free where ids would not survive.

    ``root_run_id`` is deliberately DROPPED: it names a run in the SOURCE
    environment, and carrying it over would let a cascade stop in the target
    match items it never produced. The recipient's board simply starts with no
    tree association, which is the honest state.

    ``assignee_id`` is kept and remapped by the importer along with every other
    agent id; an assignee outside the bundle closure is cleared there.
    """
    from narranexus.platform.schema.team_work_schema import WorkItemStatus

    try:
        rows = await db.get("team_work_items", {"team_id": team_id})
    except Exception:  # noqa: BLE001 — a missing board is not an export failure
        return []
    keep = (*WorkItemStatus.ACTIVE, WorkItemStatus.PAUSED)
    return [
        {
            "title": r["title"],
            "assignee_id": r.get("assignee_id"),
            "status": r["status"],
            "created_by": r.get("created_by") or "",
        }
        for r in rows or []
        if r.get("status") in keep
    ]


def assert_team_owned(team_row: Optional[dict], team_id: str, user_id: str) -> None:
    """A bundle may carry a team only if the exporter owns it.

    The agent list is owner-checked a few lines earlier; the team was read by
    id alone, so a foreign team_id exported someone else's name, intro,
    bulletin and work board (2026-10-28). Same verdict type as the agent check.
    """
    if team_row is not None and team_row.get("owner_user_id") != user_id:
        raise BundleForbidden(f"Forbidden: team {team_id} not owned by {user_id}")


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
            raise BundleForbidden(f"Forbidden: agent {aid} not owned by {user_id}")

    warnings: List[str] = []
    # `info` is for expected, non-actionable events (e.g. closure dropped an
    # external agent reference). The frontend shows `len(warnings)`, not info,
    # so a clean export with thousands of dropped external edges no longer
    # looks like "1234 warnings 你完了".
    info: List[str] = []
    info_counters: Dict[str, int] = {"skipped_external_edge": 0}
    # Manifest `stripped` list — surfaced verbatim in the import preview's
    # "not present in bundle" section, so it MUST reflect what actually left.
    # api_keys / password hashes / LLM providers are always stripped. IM channel
    # credentials are stripped ONLY when the user did not opt in — otherwise they
    # ride along (see channel_credentials.json + contains_channel_credentials),
    # and listing them as "stripped" would contradict the preview.
    stripped_lists = ["api_keys", "user_password_hash", "user_providers"]
    if not selection.include_channel_credentials:
        stripped_lists.append("im_channel_credentials")
    if not selection.include_skill_secrets:
        stripped_lists.append("skill_secrets")
    # Count of IM channel credential rows shipped (opt-in). Drives the manifest
    # `contains_channel_credentials` flag so the import wizard can warn.
    channel_cred_count = 0

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
                json.dumps(
                    [_scrub_user_id(dict(r), user_id, "instance_awareness") for r in awareness_rows],
                    indent=2,
                    ensure_ascii=False,
                    default=str,
                ),
                encoding="utf-8",
            )

            # narratives + events — B2: respect per-agent narrative_selection
            # and per-narrative event_selection if provided. None = all.
            narratives_dir = agent_dir / "narratives"
            narratives_dir.mkdir(parents=True, exist_ok=True)
            n_rows = await db.get("narratives", {"agent_id": aid})
            allowed_nars = selection.narrative_selection.get(aid) if selection.narrative_selection else None
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
                n_for_export = n if selection.include_chat_history else _scrub_narrative_chat_content(n)
                (ndir / "narrative.json").write_text(
                    json.dumps(
                        _scrub_user_id(dict(n_for_export), user_id, "narratives"),
                        indent=2,
                        ensure_ascii=False,
                        default=str,
                    ),
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
                    json.dumps(
                        _scrub_user_id(dict(r), user_id, "module_instances"), indent=2, ensure_ascii=False, default=str
                    ),
                    encoding="utf-8",
                )

            # Per-agent social entities (subject to social_entity_selection).
            # Entities live in the unified memory_entity table now — read via
            # the repo and serialize the SAME flat per-entity records the bundle
            # has always carried (content + closure/selection logic preserved;
            # only the underlying storage moved).
            from narranexus.platform.repository import SocialNetworkRepository

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
                json.dumps(
                    [_scrub_user_id(r, user_id, "social_entities") for r in social_entities_for_agent],
                    indent=2,
                    ensure_ascii=False,
                    default=str,
                ),
                encoding="utf-8",
            )

            # agent_messages
            msg_rows = await db.get("agent_messages", {"agent_id": aid}) if selection.include_chat_history else []
            (agent_dir / "agent_messages.jsonl").write_text(
                "\n".join(
                    json.dumps(_scrub_user_id(dict(r), user_id, "agent_messages"), ensure_ascii=False, default=str)
                    for r in msg_rows
                ),
                encoding="utf-8",
            )

            # instance_jobs (per-agent jobs created by JobModule).
            # P4: drop jobs whose parent narrative is NOT in the user's
            # narrative selection (otherwise the job would point at a
            # narrative that doesn't exist in the bundle, leaving a
            # dangling FK on import).
            # P7: also honor explicit per-agent job_selection allowlist.
            allowed_jobs = set(selection.job_selection.get(aid, [])) if selection.job_selection else None
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
                json.dumps(
                    [_scrub_user_id(dict(r), user_id, "instance_jobs") for r in kept_jobs],
                    indent=2,
                    ensure_ascii=False,
                    default=str,
                ),
                encoding="utf-8",
            )

            # instance_narrative_links (per-instance narrative bindings)
            nl_rows = []
            for iid in agent_instance_ids:
                nl_rows.extend(await db.get("instance_narrative_links", {"instance_id": iid}))
            (agent_dir / "instance_narrative_links.json").write_text(
                json.dumps(
                    [_scrub_user_id(dict(r), user_id, "instance_narrative_links") for r in nl_rows],
                    indent=2,
                    ensure_ascii=False,
                    default=str,
                ),
                encoding="utf-8",
            )

            # IM channel credentials (opt-in — see include_channel_credentials).
            # Grouped by table so the importer knows the destination of each row.
            # These are near-plaintext secrets; they only leave the machine when
            # the user explicitly opts in. agent_id is kept verbatim (import
            # remaps it via STRUCTURED_ID_FIELDS); everything else is IM-side and
            # preserved. On import each row lands INACTIVE.
            if selection.include_channel_credentials:
                from narranexus.platform.channel.credential_store import GenericCredentialStore

                # Generic rows: public view + DECRYPTED secrets (the key is per
                # install). The exporter's NarraNexus user id is scrubbed like
                # every other table (`<original_owner>`); IM-side ids survive
                # because channel_credentials' ID columns are declared in
                # STRUCTURED_ID_FIELDS. A row whose secret this install cannot
                # decrypt is skipped rather than exported as an empty secret.
                cred_rows = []
                for record in await GenericCredentialStore(db).list_for_agent(aid):
                    if record.secret_error:
                        logger.warning(f"bundle export: skipping {record.channel} credential of {aid}: {record.secret_error}")
                        continue
                    cred_rows.append(_scrub_user_id(record.to_raw_dict(), user_id, "channel_credentials"))
                channel_cred_count += len(cred_rows)
                (agent_dir / "channel_credentials.json").write_text(
                    json.dumps({CHANNEL_CREDENTIALS_KEY: cred_rows}, indent=2, ensure_ascii=False, default=str),
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
                    json.dumps(
                        [_scrub_user_id(dict(r), user_id, memory_table) for r in mem_rows],
                        indent=2,
                        ensure_ascii=False,
                        default=str,
                    ),
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
                    mrm_rows.extend(await db.get("module_report_memory", {"narrative_id": nrec["narrative_id"]}))
            else:
                mrm_rows = []
            (agent_dir / "module_report_memory.json").write_text(
                json.dumps(
                    [_scrub_user_id(dict(r), user_id, "module_report_memory") for r in mrm_rows],
                    indent=2,
                    ensure_ascii=False,
                    default=str,
                ),
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
                set(selection.artifact_selection.get(aid, [])) if selection.artifact_selection is not None else None
            )
            art_rows_raw = await db.get("instance_artifacts", {"agent_id": aid})
            from narranexus.platform.utils.workspace_paths import agent_workspace_relpath

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
                    raw["file_path"] = fp[len(ws_prefix) :]
                elif fp.startswith(legacy_flat_prefix):
                    raw["file_path"] = fp[len(legacy_flat_prefix) :]
                elif fp.startswith(legacy_ws_prefix):
                    raw["file_path"] = fp[len(legacy_ws_prefix) :]
                elif fp:
                    # Out-of-workspace pointer — should never happen under the
                    # pointer model, but if it does we keep the value verbatim
                    # and warn so the importer (and the user) can investigate.
                    warnings.append(
                        f"artifact {r.get('artifact_id')}: file_path outside agent workspace, exported verbatim"
                    )
                rec = _scrub_user_id(raw, user_id, "instance_artifacts")
                artifact_rows_out.append(rec)
            (agent_dir / "artifacts.json").write_text(
                json.dumps(artifact_rows_out, indent=2, ensure_ascii=False, default=str),
                encoding="utf-8",
            )

            # workspace tar.gz
            ws_path = await _pack_workspace(
                aid,
                user_id,
                agent_dir,
                excludes=selection.workspace_excludes.get(aid, []),
                scrub_skill_secrets=not selection.include_skill_secrets,
                warnings=warnings,
            )
            agents_summary.append(
                {
                    "agent_id": aid,
                    "agent_name": agent_row["agent_name"],
                    "narratives": len(n_rows),
                    "instances": len(agent_instance_ids),
                    "social_entities": len(social_entities_for_agent),
                    "artifacts": len(artifact_rows_out),
                    "workspace_size_bytes": ws_path.stat().st_size if ws_path else 0,
                    "workspace_path": "workspace.tar.gz" if ws_path else None,
                }
            )

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
        packed_zip_names: Set[str] = set()  # in-bundle zip filenames already used

        # SEC-07: server-side archive resolution. Load THIS user's archive rows
        # once, and key the zip-method lookup off them — the export request no
        # longer carries a path for us to open.
        from narranexus.platform.repository import SkillArchiveRepository

        # Keep `source_type` alongside the path: a github-installed skill's
        # archive is a real `.tar.gz`, and telling that user "your archive is
        # not a readable zip" would send them off to re-upload a file that is
        # perfectly fine. The actual mistake there is method vs source_type.
        archive_rows_by_skill: Dict[str, tuple[str, Optional[str]]] = {
            a.skill_name: (a.archive_path, a.source_type)
            for a in await SkillArchiveRepository(db).list_for_user(user_id)
            if a.archive_path
        }

        for cfg in selection.skill_methods or []:
            agent_id = cfg.get("agent_id")
            skill_name = cfg.get("skill_name")
            # `skill_dir` is the filesystem-unique dir name within the agent's
            # skills/ folder. SKILL.md frontmatter `name` can duplicate across
            # multiple dirs under one agent (user installed the same skill twice
            # into different folders), so we need the dir name to disambiguate.
            #
            # SEC-07, third field: this arrives in the export request body and is
            # used to decide which directory to READ (`_find_skill_dir`, the
            # built-in guard below, the full_copy branch) and which file to
            # WRITE. `skill_dir="../../.."` resolved to `base_working_path`
            # itself — every user's every agent workspace — and full_copy zipped
            # that whole tree into the requester's download. Cross-tenant read,
            # strictly worse than the single-file read SEC-07 closed.
            #
            # Validated HERE, at the one place it is derived, and not in the
            # branches: a per-branch gate is what let full_copy stay open while
            # zip was fixed. Everything downstream (built-in guard,
            # `_safe_bundle_zip_name`, `_find_skill_dir`, the manifest entry)
            # now sees the sanitized value, so export and import agree on what
            # this string means.
            #
            # Note this gate applies to ALL methods, including `url` / `builtin`
            # which never touch the filesystem with it. That is intentional, not
            # an oversight: `entry["skill_dir"]` goes into the manifest and the
            # importer uses it as a directory name, so a value we would refuse
            # to write must not be shipped either. The visible cost is narrow —
            # a skill whose SKILL.md frontmatter `name` contains a separator,
            # exported by url with no explicit skill_dir, is now skipped with a
            # warning instead of silently exported.
            raw_skill_dir = cfg.get("skill_dir") or skill_name
            method = cfg.get("install_method")
            if not agent_id or agent_id not in closure_set:
                warnings.append(f"skill {skill_name}: agent {agent_id} not in closure, skipping")
                continue
            try:
                skill_dir = sanitize_filename(raw_skill_dir or "", label="skill dir")
            except ValueError as e:
                warnings.append(
                    f"skill {skill_name} on {agent_id}: unusable skill_dir ({e}), skipping"
                )
                logger.warning(
                    f"rejected skill_dir for user={user_id} skill={skill_name}: "
                    f"{raw_skill_dir!r} ({e})"
                )
                continue
            # Server-side built-in guard. The workspace-tar path already strips
            # built-in skills via `_builtin_skill_relpaths`, but this explicit
            # skill_methods path is driven by the client's `install_method`. A
            # bypassed/buggy frontend could request `zip`/`full_copy` for a
            # built-in and ship it as user data inside `archive_ref`. Built-ins
            # ship with the app and re-materialize on the target, so we detect
            # them on disk here and force them onto the `builtin` (no-payload)
            # path regardless of what the client asked for.
            if method in ("zip", "full_copy"):
                on_disk = await _find_skill_dir({agent_id}, user_id, skill_name, skill_dir)
                if on_disk and _dir_is_builtin(on_disk):
                    warnings.append(
                        f"skill {skill_name} on {agent_id}: built-in, forced to 'builtin' method (not exported as user data)"
                    )
                    method = "builtin"
            entry: Dict[str, Any] = {
                "agent_id": agent_id,
                "name": skill_name,
                # Always emit dir for the importer's reconstruction.
                "skill_dir": skill_dir,
                "install_method": method,
                "contains_secrets": method == "full_copy" and selection.include_skill_secrets,
            }
            if method == "url":
                entry["source_url"] = cfg.get("source_url")
                entry["source_type"] = cfg.get("source_type", "github")
                entry["branch"] = cfg.get("branch", "main")
            elif method == "zip":
                # SEC-07: the archive path is resolved HERE, from this user's
                # own `skill_archives` rows — never taken from the request.
                # The client used to pass `archive_path` and we copied it
                # verbatim into the bundle, which made any readable file on
                # the host exportable. Same stance as the built-in guard
                # above: the client picks the *method*, the server resolves
                # the *bytes*.
                src_zip, src_type = archive_rows_by_skill.get(skill_name, (None, None))
                if not src_zip or not Path(src_zip).exists():
                    warnings.append(f"skill {skill_name} on {agent_id}: zip not found, skipping")
                    continue
                # Rows written before SEC-07 was sealed can still carry a
                # traversing path (the QA repro left one on the dev env).
                # Anchor on THIS user's archive dir, not on the archives root:
                # the QA row's stored string resolves to `{root}/marker.zip`,
                # i.e. inside the root, so a root-anchored check would wave it
                # through — as would `{root}/{victim}/x.zip`.
                if not is_within_user_archive_dir(user_id, src_zip):
                    warnings.append(
                        f"skill {skill_name} on {agent_id}: archive path escapes the "
                        f"user's archive dir, skipping (row needs cleanup)"
                    )
                    logger.warning(
                        f"SEC-07: refused out-of-dir archive_path for "
                        f"user={user_id} skill={skill_name}: {src_zip}"
                    )
                    continue
                # `skill_archives` is unique on (user_id, skill_name), so
                # `src_zip` is a pure function of `skill_name` — one skill name
                # means exactly one archive, no matter how many dirs or agents
                # reference it. They therefore share one archive_ref / one copy
                # of the bytes. (Pre-SEC-07 the key also carried the client's
                # path, back when two same-named skills could name different
                # zips; that distinction is now structurally impossible.)
                # NOTE: the `skill_dir`-based filename below is a *different*
                # de-dup layer — bundle-internal filename collisions — keep both.
                cache_key = skill_name
                if cache_key in copied_zip_ref:
                    entry["archive_ref"] = copied_zip_ref[cache_key]
                    entry["sha256"] = "shared"
                else:
                    # `skill_dir` was sanitized where it was derived, so this is
                    # only about picking a unique name (and re-anchoring, as
                    # depth).
                    tgt_zip = _safe_bundle_zip_name(
                        skills_dir, skill_dir, taken=packed_zip_names
                    )
                    # Everything that touches the source archive lives in this
                    # try. One unreadable or unreadable-mid-copy file must not
                    # fail the whole export — the caller loses this skill and is
                    # told which one, same as "zip not found" above. Before this,
                    # `BadZipFile` escaped to the route as a 500 naming neither
                    # skill nor file. Scoping the guard to the scan alone would
                    # leave `copy2` / `file_sha256` (same file, later) able to
                    # take the export down the moment anything shifts between
                    # the two.
                    try:
                        hits = scan_zip_for_sensitive(Path(src_zip))
                        await asyncio.to_thread(shutil.copy2, src_zip, tgt_zip)
                        sha = await asyncio.to_thread(file_sha256, tgt_zip)
                    except (zipfile.BadZipFile, OSError) as e:
                        # Drop a half-written tgt_zip so the manifest can never
                        # reference a truncated archive_ref.
                        tgt_zip.unlink(missing_ok=True)
                        # Only blame the source type when the file itself is the
                        # thing that failed to parse. An OSError here means I/O
                        # died mid-copy, and telling that user "your archive is a
                        # github source" would be the same misdirection this
                        # branch was added to remove, pointing the other way.
                        if isinstance(e, zipfile.BadZipFile) and src_type and src_type != "zip":
                            detail = (
                                f"archive is a {src_type} source, not a zip — "
                                f"export it with install_method='url' instead"
                            )
                        else:
                            detail = f"archive is not a readable zip ({e})"
                        warnings.append(f"skill {skill_name} on {agent_id}: {detail}, skipping")
                        logger.warning(
                            f"unusable skill archive for user={user_id} "
                            f"skill={skill_name} source_type={src_type}: {src_zip} ({e})"
                        )
                        continue
                    if hits:
                        zip_secrets_warnings.append({"skill": skill_name, "hits": hits})
                    archive_ref = f"skills/{tgt_zip.name}"
                    copied_zip_ref[cache_key] = archive_ref
                    entry["archive_ref"] = archive_ref
                    entry["sha256"] = sha
            elif method == "full_copy":
                # Per-agent: pack THIS specific agent's skill dir
                src_dir = await _find_skill_dir({agent_id}, user_id, skill_name, skill_dir)
                if not src_dir:
                    warnings.append(f"skill {skill_name} on {agent_id}: full_copy source not found")
                    continue
                # Use per-agent + dir-name path inside bundle so duplicate-named
                # skills under the same agent don't collide.
                per_agent_dir = skills_dir / agent_id
                # Path computation only — needs no directory to exist, and must
                # stay outside the try because the except below unlinks it.
                tgt_zip = per_agent_dir / f"{skill_dir}-full.zip"
                # Same degrade contract as the zip branch above — this PR's whole
                # claim is "one bad skill must not fail the whole export", and it
                # was only true on that side. `_zip_dir` walks the entire skill
                # directory: one unreadable file (permissions, deleted
                # concurrently, dangling symlink), a full disk, or a non-UTF-8
                # `.skill_meta.json` (it is read as text) all reached the route's
                # `except Exception` and became a 500 that named neither the
                # skill nor the file — the exact shape this PR opened with.
                try:
                    per_agent_dir.mkdir(parents=True, exist_ok=True)
                    await asyncio.to_thread(
                        _zip_dir,
                        src_dir,
                        tgt_zip,
                        not selection.include_skill_secrets,
                    )
                    full_sha = await asyncio.to_thread(file_sha256, tgt_zip)
                except (OSError, ValueError, zipfile.BadZipFile) as e:
                    # ValueError covers a non-UTF-8 `.skill_meta.json`
                    # (UnicodeDecodeError) and a skill dir swapped for a
                    # symlink (`_zip_dir` refuses it) — both are one bad skill.
                    # Deliberately not `except Exception`: SensitiveZipDetected
                    # and friends are control flow and must keep propagating.
                    tgt_zip.unlink(missing_ok=True)
                    warnings.append(
                        f"skill {skill_name} on {agent_id}: full_copy failed ({e}), skipping"
                    )
                    logger.warning(
                        f"full_copy pack failed for user={user_id} "
                        f"skill={skill_name} dir={src_dir}: {e}"
                    )
                    continue
                entry["archive_ref"] = f"skills/{agent_id}/{skill_dir}-full.zip"
                entry["sha256"] = full_sha
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
                warnings.append(f"sensitive_files_in_zip: {w['skill']} -> {','.join(w['hits'][:5])}")

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
                mcp_rows.append(
                    {
                        "mcp_id": r.get("mcp_id"),
                        "agent_id": aid,
                        "name": r["name"],
                        "url": r["url"],
                        "description": r.get("description"),
                        "is_enabled": int(r.get("is_enabled") or 0),
                        # metadata may contain non-secret hints (display name, version);
                        # we ship it as-is. Use _scrub_user_id only via string columns.
                        # SECURITY INVARIANT: the "headers" column (Authorization
                        # bearer tokens etc.) must NEVER be added here — bundles
                        # leave the author's account; recipients configure their
                        # own credentials.
                        "metadata": r.get("metadata"),
                    }
                )
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
            set(selection.bus_channel_selection) if selection.bus_channel_selection is not None else None
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
            json.dumps(
                {
                    "channels": bus_channels,
                    "members": bus_channel_members,
                    "messages": bus_messages,
                    "registry": bus_agent_registry,
                },
                indent=2,
                ensure_ascii=False,
                default=str,
            ),
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
            assert_team_owned(team_row, selection.team_id, user_id)
            if team_row:
                team_meta = {
                    "team_id": team_row["team_id"],
                    "name": team_row["name"],
                    "description": team_row.get("description"),
                    "color": team_row.get("color"),
                    "source": "bundle",
                    "intro_md": selection.team_intro_md or team_row.get("intro_md") or "",
                    # The team's standing rules travel with it: a bundle is how
                    # a team is handed over, and without them the recipient gets
                    # a team that has forgotten how it works. The auto-summary
                    # and author ids are dropped in the collector — see
                    # team_bulletin_transfer for why each is wrong over there.
                    "bulletin": await collect_bulletin_for_export(
                        db, selection.team_id
                    ),
                    # The work board travels with the team: it IS the team's
                    # outstanding work, and a bundle that restored the room but
                    # not what it owes would look like a finished team.
                    #
                    # Only unfinished items ship. `done`/`cancelled` are history
                    # and belong with the chat log; `paused` DOES ship (it is a
                    # real pending decision the recipient inherits).
                    "work_items": await _export_work_items(db, selection.team_id),
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
            # True iff the export carried ≥1 IM channel credential (opt-in). The
            # import wizard surfaces a "this bundle contains live secrets" notice
            # and lands every credential inactive.
            "contains_channel_credentials": channel_cred_count > 0,
            # True iff the export opted into skill secrets (env_config + full_copy
            # secret files travel). When False they were scrubbed on export.
            "contains_skill_secrets": selection.include_skill_secrets,
            "stripped": stripped_lists,
            "warnings": warnings,
            "info": info,
            "info_counters": info_counters,
        }

        # Compute integrity sha256 over all non-manifest files (sorted) — heavy I/O,
        # offloaded to a worker thread so we don't block the event loop while
        # hashing large workspaces.
        manifest["integrity_sha256"] = await asyncio.to_thread(_compute_integrity_sha256, tmpdir)

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


def _safe_bundle_zip_name(
    skills_dir: Path,
    skill_dir: str,
    *,
    taken: Set[str],
) -> Path:
    """Pick a unique in-bundle filename for a skill's zip, and claim it.

    `skill_dir` is already sanitized at its single derivation point in
    `build_bundle` — the `ensure_within_directory` call here is depth, not the
    primary gate, so that a future caller that forgets cannot escape silently.

    Uniqueness matters because the old `{dir}.zip` → `{dir}__{agent_id}.zip`
    fallback only disambiguated *different agents*. Two `skill_name`s sharing
    one `skill_dir` on the SAME agent both landed on `{dir}__{agent}.zip`: the
    second copy overwrote the first (whose manifest sha256 then described the
    wrong bytes), and on failure the cleanup deleted it outright.

    The name is added to `taken` here rather than by the caller: the whole point
    of this function is "the name is unique", and half an invariant maintained
    by whoever remembers to call `.add()` is not that.

    Note the scope: uniqueness holds *inside the bundle*. The importer pins the
    install directory by `skill_dir` (`importer.py`), so two skills sharing one
    `skill_dir` still merge into one `skills/{dir}/` on the receiving side.

    Args:
        skills_dir: Staging dir inside the bundle; the result stays under it.
        skill_dir: Sanitized directory name.
        taken: Names already used in this bundle; mutated here.

    Returns:
        A path inside `skills_dir` whose name was not in `taken`.

    Raises:
        ValueError: If the name is not a usable single path segment.
    """
    candidate = ensure_within_directory(skills_dir, f"{skill_dir}.zip", label="skill dir")
    n = 1
    while candidate.name in taken or candidate.exists():
        candidate = ensure_within_directory(
            skills_dir, f"{skill_dir}__{n}.zip", label="skill dir"
        )
        n += 1
    taken.add(candidate.name)
    return candidate


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
    from narranexus.platform.bundle.id_field_map import STRUCTURED_ID_FIELDS

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
    scrub_skill_secrets: bool = False,
    warnings: Optional[List[str]] = None,
) -> Optional[Path]:
    """Tar.gz agent's workspace dir to agent_dir/workspace.tar.gz; respects sensitive-pattern filter.
    The tarfile compression itself is offloaded to a worker thread so the main
    asyncio event loop stays responsive for other users during big workspaces.

    A workspace dir swapped for a symlink is refused PER AGENT — logged, named
    in ``warnings`` and exported without a workspace (returns None) — the same
    shape as a symlinked full_copy skill dir, so one bad agent cannot fail a
    multi-agent export with an unattributed 500. Only that one check is caught:
    any other error from the packer (disk full, permissions) still propagates
    rather than masquerading as an empty workspace."""
    # SINGLE-WORKER ASSUMPTION: workspaces live on this process's local fs.
    # Multi-pod scale requires shared volume (compose already mounts it
    # under /opt/narranexus/workspaces). See
    # .mindflow/project/references/scaling_assumptions.md §3.
    #
    # Canonical workspace path (settings.base_working_path / {agent_id}_{user_id})
    # comes from attachment_storage.get_workspace_path() and step_3_agent_loop.py.
    # Legacy `_user_<user_id>` infix kept as fallback for old install state.
    from narranexus.platform.settings import settings as core_settings

    base = Path(core_settings.base_working_path)
    from narranexus.platform.utils.workspace_paths import agent_workspace_relpath

    candidates = [
        base / agent_workspace_relpath(agent_id, user_id),  # canonical (current layout)
        base / f"{agent_id}_{user_id}",  # legacy flat (pre-nested migration)
        base / f"{agent_id}_user_{user_id}",  # legacy _user_ infix
    ]
    src = next((c for c in candidates if c.is_dir()), None)
    if not src:
        return None
    try:
        refuse_symlinked_dir(src, label="workspace")
    except ValueError as e:
        logger.warning(f"bundle export: agent {agent_id}: {e}; workspace not exported")
        if warnings is not None:
            warnings.append(f"agent {agent_id}: workspace is a symlink, not exported")
        return None

    out = agent_dir / "workspace.tar.gz"
    excl_set = set(excludes or [])

    return await asyncio.to_thread(_pack_workspace_sync, src, out, excl_set, user_id, scrub_skill_secrets)


def _builtin_skill_relpaths(src: Path) -> set:
    """Return workspace-relative roots of built-in skill dirs under `skills/`.

    A skill dir is built-in when its `.skill_meta.json` has `builtin: true`.
    These ship with the app and are excluded from workspace exports.
    """
    roots: set = set()
    skills_dir = src / "skills"
    if not skills_dir.is_dir():
        return roots
    for d in skills_dir.iterdir():
        if not d.is_dir() or d.name.startswith("."):
            continue
        if _dir_is_builtin(d):
            roots.add(f"skills/{d.name}")
    return roots


def _pack_workspace_sync(
    src: Path, out: Path, excl_set: set, user_id: str = "", scrub_skill_secrets: bool = False
) -> Path:
    # os.walk follows the TOP-LEVEL path even with followlinks=False: a
    # workspace dir swapped for a symlink would pack another tenant's tree.
    refuse_symlinked_dir(src, label="workspace")
    from narranexus.platform.bundle.skill_secrets import SKILL_META_FILENAME, scrub_skill_meta

    text_extensions = {".md", ".txt", ".json", ".jsonl", ".yaml", ".yml", ".csv", ".log"}
    placeholder = "<original_owner>"
    # Memory cap on per-file user_id rewrite. Files larger than this are
    # added to the tar as-is (no in-memory rewrite). Picked at 5 MB so the
    # rewrite still covers awareness markdown / notes / chat logs but skips
    # giant log files / dumps that would balloon RAM during export.
    text_rewrite_max_bytes = 5 * 1024 * 1024

    # Built-in skills ship with the app and are re-materialized on the target;
    # they must not travel inside the exported workspace tar as user data.
    builtin_skill_roots = _builtin_skill_relpaths(src)

    def _is_builtin_skill_path(name: str) -> bool:
        return any(name == root or name.startswith(root + "/") for root in builtin_skill_roots)

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
        # built-in skills (shipped with the app, not user data)
        if _is_builtin_skill_path(name):
            return None
        # sensitive default-skip
        if is_sensitive_path(name):
            return None
        # volume default-skip
        if is_volume_path(name):
            return None
        return tarinfo

    with tarfile.open(out, "w:gz") as tar:
        # If we need to rewrite file bytes before adding (user_id scrub and/or
        # skill env_config scrub), walk manually. Otherwise, fast path.
        if user_id or scrub_skill_secrets:
            for root, dirs, files in os.walk(src):
                for fn in files:
                    full = Path(root) / fn
                    rel_to_src = full.relative_to(src)
                    rel_str = str(rel_to_src).replace("\\", "/")
                    if rel_str in excl_set:
                        continue
                    if _is_builtin_skill_path(rel_str):
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
                            original = content
                            # Skill secrets: blank .skill_meta.json env_config
                            # values when the export didn't opt into them.
                            if scrub_skill_secrets and fn == SKILL_META_FILENAME:
                                scrubbed = scrub_skill_meta(content)
                                if scrubbed is not None:
                                    content = scrubbed
                            # user_id → owner placeholder.
                            if user_id and user_id in content:
                                content = content.replace(user_id, placeholder)
                            if content != original:
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
    from narranexus.platform.settings import settings as core_settings

    base = Path(core_settings.base_working_path)
    dir_name = skill_dir or skill_name
    for aid in agent_ids:
        from narranexus.platform.utils.workspace_paths import agent_workspace_relpath

        candidates = [
            base / agent_workspace_relpath(aid, user_id) / "skills" / dir_name,  # canonical (current layout)
            base / f"{aid}_{user_id}" / "skills" / dir_name,  # legacy flat
            base / f"{aid}_user_{user_id}" / "skills" / dir_name,  # legacy _user_ infix
        ]
        for c in candidates:
            if c.is_dir():
                return c
    return None


def _zip_dir(src: Path, dst: Path, scrub_secrets: bool = False) -> None:
    """Synchronous zip writer. Callers in async context MUST wrap in
    asyncio.to_thread() — zip compression is CPU-bound and blocks the event loop.

    ``scrub_secrets=True`` (full_copy without include_skill_secrets): drop
    sensitive files (credentials.json / *_token* / .env …) and blank the
    ``env_config`` values inside ``.skill_meta.json``, so a full_copy skill does
    not leak secrets when the exporter didn't opt in.

    Raises ValueError when ``src`` itself is a symlink: ``os.walk`` follows the
    top-level path even with ``followlinks=False``."""
    refuse_symlinked_dir(src, label="skill directory")
    from narranexus.platform.bundle.skill_secrets import SKILL_META_FILENAME, scrub_skill_meta

    with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, _, files in os.walk(src):
            for fn in files:
                full = Path(root) / fn
                rel = full.relative_to(src)
                if full.is_symlink():
                    # Never follow a link into the archive: its target can be
                    # anything the backend can read (2026-10-28).
                    continue
                if scrub_secrets:
                    if is_sensitive_path(str(rel).replace("\\", "/")):
                        continue
                    if fn == SKILL_META_FILENAME:
                        scrubbed = scrub_skill_meta(full.read_text(encoding="utf-8"))
                        if scrubbed is not None:
                            zf.writestr(str(rel), scrubbed)
                            continue
                zf.write(full, arcname=str(rel))


def _compute_integrity_sha256(tmpdir: Path) -> str:
    """Walk tmpdir, sha256 each non-manifest file, fold into a single digest.
    Sync — caller wraps in asyncio.to_thread()."""
    all_paths = sorted([p for p in tmpdir.rglob("*") if p.is_file() and p.name != "manifest.json"])
    h = io.BytesIO()
    for p in all_paths:
        h.write(str(p.relative_to(tmpdir)).encode("utf-8"))
        h.write(b":")
        h.write(file_sha256(p).encode("utf-8"))
        h.write(b"\n")
    return bytes_sha256(h.getvalue())

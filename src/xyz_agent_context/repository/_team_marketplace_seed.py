"""
@file_name: _team_marketplace_seed.py
@author: NetMind.AI
@date: 2026-07-21
@description: Bootstrap seed for the Team Marketplace (9 official templates).

Ports the curated catalog from the unmerged feat/in-app-marketplace branch,
but diverges on hosting: instead of pointing bundle_url at narra.nexus static
hosting, the seed FETCHES each .nxbundle once, stores it in OUR template
artifact store (get_template_store — own S3 prefix / local subfolder), and
writes a catalog row with the resulting store_key + verified sha256.

Idempotent: upsert is keyed on template_id; a template whose blob is already
in the store is not re-uploaded. Per-entry try/except so one unreachable
source never aborts the rest. The narra.nexus source URLs are the MIGRATION
input only — once seeded, install reads exclusively from our own store.
"""

import hashlib
import tempfile
from pathlib import Path
from typing import Any, Dict, List

import httpx
from loguru import logger

from xyz_agent_context._skill_marketplace_impl.artifact_store import get_template_store
from xyz_agent_context.repository.team_catalog_repository import TeamCatalogRepository
from xyz_agent_context.schema.team_marketplace_schema import TeamTemplate
from xyz_agent_context.team_marketplace_service import store_key_for

# Migration source: narra.nexus static templates (the original ee1db871
# catalog). `source_url` is used only to fetch-and-restore into our store.
SEED_TEMPLATES: List[Dict[str, Any]] = [
    {
        "template_id": "financial-morning-briefing",
        "name": "Financial Morning Briefing",
        "description": "A 6-agent analyst team that delivers an analyst-grade HTML market briefing every day at 08:00 Asia/Shanghai — every item layered data → context → deep read.",
        "categories": ["finance", "team"],
        "author": "NarraNexus team",
        "agent_count": 6,
        "source_url": "https://www.narra.nexus/templates/briefing_team.nxbundle",
        "bundle_sha256": "58316c737c7d37f26b4ed17e9911e0a920b1aeec5fb35f7e15056349b3f3c4bd",
        "sort_order": 0,
    },
    {
        "template_id": "marketing-team",
        "name": "Marketing Team",
        "description": "A 4-agent marketing team that automates the sponsorship pipeline end-to-end: parsing inbound sponsor emails, tracking each deal in a CRM, and listening for brand mentions across X, Reddit, Hacker News, and Product Hunt.",
        "categories": ["marketing", "sponsorship", "team"],
        "author": "NarraNexus team contributor",
        "agent_count": 4,
        "source_url": "https://www.narra.nexus/templates/marketing_team-20260527.nxbundle",
        "bundle_sha256": "0fa87e83c9397b447184bcc3a621a5c95827f516e7dbf058cf535a8046c7782c",
        "sort_order": 1,
    },
    {
        "template_id": "pm-bridge-bot",
        "name": "PM Bridge Bot",
        "description": "A single bot that bridges your internal team and external clients — files every conversation into a dual-folder knowledge base and answers with role-aware personas.",
        "categories": ["productivity", "knowledge-base"],
        "author": "NarraNexus team contributor",
        "agent_count": 1,
        "source_url": "https://www.narra.nexus/templates/pm-bridge-bot.nxbundle",
        "bundle_sha256": "5c76ca3780e87f6aecb0eb53831467fef030c9b4ec03f6de8acbbb0fb8e06391",
        "sort_order": 2,
    },
    {
        "template_id": "web-development-team",
        "name": "Web Development Team",
        "description": "A 4-agent build-and-ship pipeline — PM scopes the work, Web Developer builds it, Design Reviewer signs off, Vercel Deployment Agent ships it.",
        "categories": ["development", "web"],
        "author": "NarraNexus team contributor",
        "agent_count": 4,
        "source_url": "https://www.narra.nexus/templates/Web_Development-20260603.nxbundle",
        "bundle_sha256": "8bb5e3a55ec37885a9251ce5e6486e01c234c65f9fea59299968d1e797d9fb8e",
        "sort_order": 3,
    },
    {
        "template_id": "overnight-coder",
        "name": "Overnight Coder",
        "description": "An autonomous developer that picks up your TODO list at midnight and lands reviewable PRs by 7 AM — full test runs, meaningful commits, decisions logged.",
        "categories": ["development", "automation"],
        "author": "Community contributor",
        "agent_count": 1,
        "source_url": "https://www.narra.nexus/templates/overnight-coder.nxbundle",
        "bundle_sha256": "d8401fac845faf5e731d66cb8a93f1aecdcca8cd6361af2b3b759d38308caf28",
        "sort_order": 4,
    },
    {
        "template_id": "sql-assistant",
        "name": "SQL Assistant",
        "description": "Ask in plain English, get optimized SQL with an explanation and index suggestions — works across PostgreSQL, MySQL, SQLite.",
        "categories": ["development", "data"],
        "author": "Community contributor",
        "agent_count": 1,
        "source_url": "https://www.narra.nexus/templates/sql-assistant.nxbundle",
        "bundle_sha256": "dce3590e3856657055e5fdf16d058bc16b93d5e1ab65fa6c32592e81a65e21f2",
        "sort_order": 5,
    },
    {
        "template_id": "travel-planner",
        "name": "Travel Planner",
        "description": "Plans trips like a local — day-by-day itineraries with timing, costs in USD, transit between stops, and 2-3 options at every price tier.",
        "categories": ["personal", "lifestyle"],
        "author": "Community contributor",
        "agent_count": 1,
        "source_url": "https://www.narra.nexus/templates/travel-planner.nxbundle",
        "bundle_sha256": "4cdcb237cccd1bb82fc72082bbed10152accdb79b5303fc9f49ed56617b5d2d9",
        "sort_order": 6,
    },
    {
        "template_id": "phishing-detector",
        "name": "Phishing Detector",
        "description": "Paste a suspicious email or URL — get a 0-100 phishing score with specific red flags, recommended action, and a safe analysis (no clicks).",
        "categories": ["security", "productivity"],
        "author": "Community contributor",
        "agent_count": 1,
        "source_url": "https://www.narra.nexus/templates/phishing-detector.nxbundle",
        "bundle_sha256": "1f5c539c06bb2861ccafa53dfc640f9c2de57d8303d5746b38b9ee34e88ff5c1",
        "sort_order": 7,
    },
    {
        "template_id": "gaokao-team",
        "name": "Gaokao Grading Team",
        "description": "A 5-agent exam-review team that simulates a candidate workflow and grades Chinese, Math, and English submissions before producing an overall assessment.",
        "categories": ["education", "team"],
        "author": "NarraNexus team",
        "agent_count": 5,
        "source_url": "https://www.narra.nexus/templates/gaokao-team.nxbundle",
        "bundle_sha256": "26b86d2c1e443ced39e2c8a68c03dacd9c71673eaad9dbd193109fba5d568a77",
        "sort_order": 8,
    },
]


async def seed_team_marketplace(db_client) -> int:
    """Fetch-and-restore each official template into our store + catalog.

    Returns the number of templates present after seeding. Best-effort:
    fetch/verify failures skip that entry and log, never abort. Safe to run
    at every startup (upsert keyed on template_id; blob re-upload skipped when
    the store already has the key)."""
    store = get_template_store()
    repo = TeamCatalogRepository(db_client)
    ok = 0
    for entry in SEED_TEMPLATES:
        tid = entry["template_id"]
        try:
            existing = await repo.get(tid)
            key = store_key_for(tid, entry["bundle_sha256"])
            if existing and existing.store_key == key and store.exists(key):
                ok += 1
                continue  # already restored — nothing to fetch

            tmp = Path(tempfile.mkdtemp(prefix="nx-team-seed-"))
            try:
                dest = tmp / f"{tid}.nxbundle"
                # Async client — this runs on the event loop (lifespan seed
                # task); a sync httpx.Client would freeze it for the whole
                # download.
                async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
                    resp = await client.get(entry["source_url"])
                    resp.raise_for_status()
                    dest.write_bytes(resp.content)
                actual = hashlib.sha256(dest.read_bytes()).hexdigest()
                if actual.lower() != entry["bundle_sha256"].lower():
                    logger.warning(
                        f"team seed: sha256 mismatch for {tid} "
                        f"(expected {entry['bundle_sha256'][:12]}, got {actual[:12]}) — skipping"
                    )
                    continue
                store.put_file(key, dest)
            finally:
                import shutil

                shutil.rmtree(tmp, ignore_errors=True)

            await repo.save_template(
                TeamTemplate(
                    template_id=tid,
                    name=entry["name"],
                    description=entry["description"],
                    categories=entry["categories"],
                    author=entry["author"],
                    agent_count=entry["agent_count"],
                    store_key=key,
                    bundle_sha256=entry["bundle_sha256"],
                    enabled=True,
                    sort_order=entry["sort_order"],
                )
            )
            ok += 1
            logger.info(f"team seed: restored {tid} ({entry['agent_count']} agents)")
        except Exception as e:  # noqa: BLE001
            logger.warning(f"team seed: {tid} skipped — {type(e).__name__}: {e}")
    logger.info(f"team seed: {ok}/{len(SEED_TEMPLATES)} templates present")
    return ok

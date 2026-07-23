"""
@file_name: team_marketplace_schema.py
@author: NetMind.AI
@date: 2026-07-21
@description: Pydantic model for the Team Marketplace catalog.

A TeamTemplate is a catalog INDEX row: presentation metadata + a pointer
(store_key + bundle_sha256) to a `.nxbundle` in OUR artifact store (S3/local,
separate from skills). Install fetches the blob (directly on the registry
host, over HTTP on a desktop client) and runs the existing bundle importer
(fork semantics). Design:
reference/self_notebook/specs/2026-07-21-team-marketplace-tech-design.md
"""

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field


class TeamTemplate(BaseModel):
    """One browsable/installable team (or single-agent) bundle template."""

    id: Optional[int] = None
    template_id: str  # stable slug, e.g. "financial-morning-briefing"
    name: str
    description: str = ""
    categories: List[str] = Field(default_factory=list)  # chips, e.g. ["finance","team"]
    author: str = "NarraNexus team"
    agent_count: int = 1  # card badge — number of agents the bundle creates
    thumbnail_url: Optional[str] = None
    store_key: str = ""  # artifact-store key of the .nxbundle
    bundle_sha256: str = ""  # integrity pin, verified after fetch
    enabled: bool = True
    sort_order: int = 0
    downloads: int = 0
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

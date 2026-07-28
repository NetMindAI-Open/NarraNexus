"""
@file_name: skill_marketplace_schema.py
@author: NetMind.AI
@date: 2026-07-20
@description: Pydantic models for the Skill Marketplace (catalog, installations, scans).

JSON-shaped fields (capabilities, tags, dependencies, ...) are Python-native here;
serialization to the *_json TEXT columns happens in the repositories.
"""

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class SkillCatalogEntry(BaseModel):
    """One published version of a skill in the marketplace catalog."""

    id: Optional[int] = None
    skill_id: str
    version: str
    name: str
    description: Optional[str] = None
    author: Optional[Dict[str, Any]] = None
    license: Optional[str] = None
    category: Optional[str] = None
    capabilities: List[str] = Field(default_factory=list)
    tags: List[str] = Field(default_factory=list)
    config_schema: Optional[Dict[str, Any]] = None
    dependencies: Dict[str, str] = Field(default_factory=dict)
    compatibility: Optional[Dict[str, str]] = None
    s3_key: str
    package_hash: str
    publisher: Optional[str] = None
    scan_status: str = "passed"  # passed | warning | rejected
    status: str = "published"  # published | deprecated | unlisted
    downloads: int = 0
    is_default: bool = False  # auto-installed on agent creation
    avg_rating: Optional[float] = None
    published_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class SkillInstallationRecord(BaseModel):
    """Audit row for one skill in one (agent_id, user_id) workspace.

    Follower of the filesystem truth (skills/ + .skill_meta.json); never
    authoritative on its own.
    """

    id: Optional[int] = None
    agent_id: str
    user_id: str
    skill_id: str
    version: Optional[str] = None
    source_type: str  # marketplace | url | github | zip | builtin | manual
    source_url: Optional[str] = None
    package_hash: Optional[str] = None
    status: str = "installed"  # installed | uninstalled | external_removed | modified | disabled
    last_event: Optional[str] = None  # install | update | rollback | uninstall | reconcile
    installed_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class SkillScanResult(BaseModel):
    """One security-scan run over one published skill version (append-only)."""

    id: Optional[int] = None
    skill_id: str
    version: str
    status: str  # passed | warning | rejected
    high_issues: int = 0
    low_issues: int = 0
    issues: List[Dict[str, Any]] = Field(default_factory=list)
    scanner_version: Optional[str] = None
    scanned_at: Optional[datetime] = None

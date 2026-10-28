"""
@file_name: skill_archive_schema.py
@author: NetMind.AI
@date: 2026-05-08
@description: SkillArchive Pydantic model

Subproject 2: Bundle Export/Import — backs every installed skill with a reproducible source.
- source_type: "github" or "zip"
- source_url: github URL when source_type="github", null otherwise
- archive_path: local path to the archived tarball/zip file
- sha256: integrity hash of the archive payload
"""

from typing import Optional
from datetime import datetime
from pydantic import BaseModel


class SkillArchive(BaseModel):
    id: Optional[int] = None
    user_id: str
    skill_name: str
    source_type: str  # "github" | "zip"
    source_url: Optional[str] = None
    archive_path: Optional[str] = None
    sha256: str
    created_at: Optional[datetime] = None

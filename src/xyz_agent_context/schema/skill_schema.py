"""
@file_name: skill_schema.py
@author: NetMind.AI
@date: 2026-02-03
@description: Skill related data models

Defines data structures used by SkillModule, including:
- SkillInfo: Skill basic information
"""

from typing import Optional
from pydantic import BaseModel, Field


class SkillInfo(BaseModel):
    """
    Skill information

    Describes the basic information of an installed Skill.
    All Skills are under the user's workspace ({agent_id}_{user_id}/skills/).
    """
    name: str = Field(..., description="Skill name")
    description: str = Field(default="", description="Skill description")
    path: str = Field(..., description="Full path to the Skill directory")
    disabled: bool = Field(
        default=False,
        description="Whether disabled"
    )
    version: Optional[str] = Field(
        default=None,
        description="Skill version (from SKILL.md frontmatter)"
    )
    author: Optional[str] = Field(
        default=None,
        description="Skill author (from SKILL.md frontmatter)"
    )
    source_url: Optional[str] = Field(
        default=None,
        description="Installation source URL (saved during GitHub installation)"
    )
    installed_at: Optional[str] = Field(
        default=None,
        description="Installation time (ISO format)"
    )
    # Environment requirements (detected from frontmatter + study)
    requires_env: Optional[list[str]] = Field(
        default=None,
        description="Required environment variable names"
    )
    requires_bins: Optional[list[str]] = Field(
        default=None,
        description="Required binary dependencies"
    )
    env_configured: Optional[bool] = Field(
        default=None,
        description="Whether all required env vars have values configured"
    )
    # Study status related fields
    study_status: Optional[str] = Field(
        default=None,
        description="Study status: idle/studying/completed/failed"
    )
    study_result: Optional[str] = Field(
        default=None,
        description="Study result (Agent's natural language summary)"
    )
    study_error: Optional[str] = Field(
        default=None,
        description="Error message when study fails"
    )
    studied_at: Optional[str] = Field(
        default=None,
        description="Study completion time (ISO format)"
    )

    class Config:
        """Pydantic configuration"""
        json_schema_extra = {
            "example": {
                "name": "sales-expert",
                "description": "Provides professional sales techniques and customer communication skills",
                "path": "/workspace/agent_001_user_binliang/skills/sales-expert",
                "disabled": False
            }
        }


class SkillListResponse(BaseModel):
    """Skill list response"""
    skills: list[SkillInfo] = Field(default_factory=list, description="Skill list")
    total: int = Field(default=0, description="Total count")


class SkillOperationResponse(BaseModel):
    """Skill operation response (install/remove/disable/enable)"""
    success: bool = Field(..., description="Whether the operation succeeded")
    message: str = Field(default="", description="Operation result message")
    skill: Optional[SkillInfo] = Field(default=None, description="Operated Skill information")


class SkillStudyResponse(BaseModel):
    """Skill study response (for triggering study and polling status)"""
    success: bool = Field(..., description="Whether the operation succeeded")
    message: str = Field(default="", description="Operation result message")
    study_status: str = Field(default="idle", description="Study status")
    study_result: Optional[str] = Field(default=None, description="Study result")


class SkillEnvConfigResponse(BaseModel):
    """Skill environment configuration response"""
    success: bool = Field(..., description="Whether the operation succeeded")
    requires_env: list[str] = Field(default_factory=list, description="Required env var names")
    env_configured: dict[str, bool] = Field(
        default_factory=dict,
        description="Env var name -> whether it is configured (never returns actual values)"
    )

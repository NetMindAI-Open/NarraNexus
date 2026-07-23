"""
@file_name: test_artifact_store_layout.py
@author: NetMind.AI
@date: 2026-07-21
@description: S3 key-layout resolution for the marketplace stores.

Verifies the dev/prod × skills/teams layout: MARKETPLACE_S3_ENV composes
"<env>/skills" and "<env>/teams" prefixes in the shared bucket; explicit
*_S3_PREFIX overrides; absent env falls back to the flat defaults.
"""

import pytest

from xyz_agent_context._skill_marketplace_impl.artifact_store import (
    S3ArtifactStore,
    get_artifact_store,
    get_template_store,
)

BUCKET = "nexus-marketplace-891377017161"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for k in ("SKILL_S3_BUCKET", "TEMPLATE_S3_BUCKET", "SKILL_S3_PREFIX",
              "TEMPLATE_S3_PREFIX", "MARKETPLACE_S3_ENV", "SKILL_S3_REGION",
              "TEMPLATE_S3_REGION"):
        monkeypatch.delenv(k, raising=False)


def test_env_segment_dev_layout(monkeypatch):
    monkeypatch.setenv("SKILL_S3_BUCKET", BUCKET)
    monkeypatch.setenv("SKILL_S3_REGION", "eu-west-2")
    monkeypatch.setenv("MARKETPLACE_S3_ENV", "dev")

    skills = get_artifact_store()
    teams = get_template_store()
    assert isinstance(skills, S3ArtifactStore) and isinstance(teams, S3ArtifactStore)
    assert skills.bucket == BUCKET and teams.bucket == BUCKET
    assert skills.prefix == "dev/skills"
    assert teams.prefix == "dev/teams"
    assert skills.region == "eu-west-2" and teams.region == "eu-west-2"


def test_env_segment_prod_layout(monkeypatch):
    monkeypatch.setenv("SKILL_S3_BUCKET", BUCKET)
    monkeypatch.setenv("MARKETPLACE_S3_ENV", "prod")
    assert get_artifact_store().prefix == "prod/skills"
    assert get_template_store().prefix == "prod/teams"


def test_explicit_prefix_overrides_env(monkeypatch):
    monkeypatch.setenv("SKILL_S3_BUCKET", BUCKET)
    monkeypatch.setenv("MARKETPLACE_S3_ENV", "dev")
    monkeypatch.setenv("SKILL_S3_PREFIX", "custom/skills")
    monkeypatch.setenv("TEMPLATE_S3_PREFIX", "custom/teams")
    assert get_artifact_store().prefix == "custom/skills"
    assert get_template_store().prefix == "custom/teams"


def test_flat_default_without_env(monkeypatch):
    monkeypatch.setenv("SKILL_S3_BUCKET", BUCKET)
    assert get_artifact_store().prefix == "narranexus-skills"
    assert get_template_store().prefix == "narranexus-teams"


def test_keys_never_collide_between_objects(monkeypatch):
    """dev/skills and dev/teams share a bucket but never overlap."""
    monkeypatch.setenv("SKILL_S3_BUCKET", BUCKET)
    monkeypatch.setenv("MARKETPLACE_S3_ENV", "dev")
    skills, teams = get_artifact_store(), get_template_store()
    skill_key = skills._key("web-search-fallback/1.0.0/web-search-fallback.zip")
    team_key = teams._key("gaokao-team/26b86d2c/gaokao-team.nxbundle")
    assert skill_key.startswith("dev/skills/")
    assert team_key.startswith("dev/teams/")

"""
@file_name: test_embedding_migration_multi_tenant.py
@author: Bin Liang
@date: 2026-04-20
@description: Multi-tenant correctness tests for EmbeddingMigrationService.

Bug 11 (cloud) regression: the migration service and its global `_progress`
singleton were written for single-user desktop. On cloud the service must:
  - Count / rebuild only rows that belong to a specific user_id
  - Keep a per-user progress snapshot so concurrent rebuilds don't stomp
  - Resolve the embedding model from that user's provider slots, not the
    last-loaded global `embedding_config`

These tests pin those invariants in place.
"""
from __future__ import annotations

from typing import Any
from datetime import datetime, timezone

import pytest

from xyz_agent_context.agent_framework.api_config import EmbeddingConfig
from xyz_agent_context.services import embedding_migration_service as mig_mod
from xyz_agent_context.services.embedding_migration_service import (
    EmbeddingMigrationService,
    MigrationProgress,
    get_migration_progress,
)


async def _seed_agent(db, *, agent_id: str, created_by: str) -> None:
    await db.insert(
        "agents",
        {
            "agent_id": agent_id,
            "agent_name": agent_id,
            "created_by": created_by,
            "agent_type": "general",
            "is_public": 0,
        },
    )


async def _seed_narrative(db, *, narrative_id: str, agent_id: str) -> None:
    await db.insert(
        "narratives",
        {
            "narrative_id": narrative_id,
            "type": "chat",
            "agent_id": agent_id,
            "narrative_info": "{}",
            "topic_hint": f"hint-{narrative_id}",
        },
    )


async def _seed_event(
    db, *, event_id: str, agent_id: str, user_id: str, text: str
) -> None:
    await db.insert(
        "events",
        {
            "event_id": event_id,
            "trigger": "chat",
            "trigger_source": "test",
            "agent_id": agent_id,
            "user_id": user_id,
            "embedding_text": text,
            "final_output": text,
        },
    )


async def _seed_job(
    db, *, job_id: str, instance_id: str, agent_id: str, user_id: str
) -> None:
    await db.insert(
        "instance_jobs",
        {
            "instance_id": instance_id,
            "job_id": job_id,
            "agent_id": agent_id,
            "user_id": user_id,
            "title": f"job {job_id}",
            "description": "desc",
            "payload": "",
            "job_type": "test",
            "status": "pending",
        },
    )


async def _seed_instance(
    db, *, instance_id: str, agent_id: str, user_id: str
) -> None:
    await db.insert(
        "module_instances",
        {
            "instance_id": instance_id,
            "agent_id": agent_id,
            "module_class": "SocialNetworkModule",
            "user_id": user_id,
            "status": "active",
        },
    )


async def _seed_entity(
    db, *, instance_id: str, entity_id: str, name: str
) -> None:
    await db.insert(
        "instance_social_entities",
        {
            "instance_id": instance_id,
            "entity_id": entity_id,
            "entity_type": "person",
            "entity_name": name,
            "entity_description": f"desc of {name}",
        },
    )


@pytest.fixture
def patched_embedding(monkeypatch):
    """Replace the live embedding call with a deterministic stub.

    The service now embeds through a dedicated EmbeddingClient instance
    (pinned to the user's provider), so we stub the instance method rather
    than the old module-level get_embedding helper.
    """

    async def _fake_embed(self, text: str):  # noqa: ARG001
        # 4-dim vector, values derived from text length so collisions are unlikely
        n = len(text) or 1
        return [float(n), float(n) + 1, float(n) + 2, float(n) + 3]

    monkeypatch.setattr(mig_mod.EmbeddingClient, "embed", _fake_embed)


@pytest.fixture(autouse=True)
def reset_progress():
    mig_mod._reset_progress_for_tests()
    yield
    mig_mod._reset_progress_for_tests()


@pytest.fixture
def force_new_embedding_path(monkeypatch):
    """Make `use_embedding_store(user_id)` return True in migration context."""
    monkeypatch.setattr(
        mig_mod,
        "_resolve_use_embedding_store",
        lambda user_id: True,
    )


@pytest.fixture
def patched_embedding_cfg(monkeypatch):
    """Stub the per-user embedding provider resolution.

    Returns a concrete EmbeddingConfig (model + api_key + base_url) so the
    service can build a real EmbeddingClient without hitting a provider — the
    api_key is non-empty so AsyncOpenAI constructs cleanly.
    """

    async def _resolve(user_id, resolver=None, db=None, *, raise_on_gating=True):  # noqa: ARG001
        model = {
            "alice": "model-a",
            "bob": "model-b",
        }.get(user_id, "model-default")
        return EmbeddingConfig(api_key="test-key", base_url="", model=model)

    monkeypatch.setattr(mig_mod, "_resolve_user_embedding_cfg", _resolve)


@pytest.mark.asyncio
async def test_status_sees_only_caller_user_data(
    db_client, patched_embedding, force_new_embedding_path, patched_embedding_cfg
):
    await _seed_agent(db_client, agent_id="agent_a", created_by="alice")
    await _seed_agent(db_client, agent_id="agent_b", created_by="bob")
    # Alice gets 2 narratives, Bob 1
    await _seed_narrative(db_client, narrative_id="nar_alice_1", agent_id="agent_a")
    await _seed_narrative(db_client, narrative_id="nar_alice_2", agent_id="agent_a")
    await _seed_narrative(db_client, narrative_id="nar_bob_1", agent_id="agent_b")

    svc_alice = EmbeddingMigrationService(db_client, user_id="alice")
    svc_bob = EmbeddingMigrationService(db_client, user_id="bob")

    status_alice = await svc_alice.get_status()
    status_bob = await svc_bob.get_status()

    assert status_alice["model"] == "model-a"
    assert status_bob["model"] == "model-b"
    assert status_alice["stats"]["narrative"]["total"] == 2
    assert status_bob["stats"]["narrative"]["total"] == 1


@pytest.mark.asyncio
async def test_status_filters_events_by_user_id(
    db_client, patched_embedding, force_new_embedding_path, patched_embedding_cfg
):
    await _seed_agent(db_client, agent_id="agent_a", created_by="alice")
    await _seed_agent(db_client, agent_id="agent_b", created_by="bob")
    await _seed_event(db_client, event_id="evt_1", agent_id="agent_a", user_id="alice", text="a1")
    await _seed_event(db_client, event_id="evt_2", agent_id="agent_a", user_id="alice", text="a2")
    await _seed_event(db_client, event_id="evt_3", agent_id="agent_b", user_id="bob",   text="b1")

    stats_alice = (await EmbeddingMigrationService(db_client, user_id="alice").get_status())["stats"]
    stats_bob   = (await EmbeddingMigrationService(db_client, user_id="bob").get_status())["stats"]

    assert stats_alice["event"]["total"] == 2
    assert stats_bob["event"]["total"] == 1


@pytest.mark.asyncio
async def test_status_filters_jobs_by_user_id(
    db_client, patched_embedding, force_new_embedding_path, patched_embedding_cfg
):
    await _seed_agent(db_client, agent_id="agent_a", created_by="alice")
    await _seed_agent(db_client, agent_id="agent_b", created_by="bob")
    await _seed_job(db_client, job_id="job_1", instance_id="inst_a", agent_id="agent_a", user_id="alice")
    await _seed_job(db_client, job_id="job_2", instance_id="inst_b", agent_id="agent_b", user_id="bob")

    stats_alice = (await EmbeddingMigrationService(db_client, user_id="alice").get_status())["stats"]
    stats_bob   = (await EmbeddingMigrationService(db_client, user_id="bob").get_status())["stats"]

    assert stats_alice["job"]["total"] == 1
    assert stats_bob["job"]["total"] == 1


@pytest.mark.asyncio
async def test_status_filters_entities_via_instance_user(
    db_client, patched_embedding, force_new_embedding_path, patched_embedding_cfg
):
    await _seed_agent(db_client, agent_id="agent_a", created_by="alice")
    await _seed_agent(db_client, agent_id="agent_b", created_by="bob")
    await _seed_instance(db_client, instance_id="inst_a", agent_id="agent_a", user_id="alice")
    await _seed_instance(db_client, instance_id="inst_b", agent_id="agent_b", user_id="bob")
    await _seed_entity(db_client, instance_id="inst_a", entity_id="ent_1", name="Alice's friend")
    await _seed_entity(db_client, instance_id="inst_a", entity_id="ent_2", name="Alice's colleague")
    await _seed_entity(db_client, instance_id="inst_b", entity_id="ent_3", name="Bob's contact")

    stats_alice = (await EmbeddingMigrationService(db_client, user_id="alice").get_status())["stats"]
    stats_bob   = (await EmbeddingMigrationService(db_client, user_id="bob").get_status())["stats"]

    assert stats_alice["entity"]["total"] == 2
    assert stats_bob["entity"]["total"] == 1


@pytest.mark.asyncio
async def test_rebuild_only_touches_caller_user(
    db_client, patched_embedding, force_new_embedding_path, patched_embedding_cfg
):
    await _seed_agent(db_client, agent_id="agent_a", created_by="alice")
    await _seed_agent(db_client, agent_id="agent_b", created_by="bob")
    await _seed_narrative(db_client, narrative_id="nar_alice_1", agent_id="agent_a")
    await _seed_narrative(db_client, narrative_id="nar_bob_1", agent_id="agent_b")

    svc_alice = EmbeddingMigrationService(db_client, user_id="alice")
    await svc_alice.rebuild_all()

    rows = await db_client.get(
        "embeddings_store",
        filters={"entity_type": "narrative"},
    )
    entity_ids = {r["entity_id"] for r in rows}
    assert "nar_alice_1" in entity_ids
    assert "nar_bob_1" not in entity_ids, (
        "Alice's rebuild must not touch Bob's narratives"
    )

    # Model recorded must be Alice's
    models_for_alice = {r["model"] for r in rows if r["entity_id"] == "nar_alice_1"}
    assert models_for_alice == {"model-a"}


@pytest.mark.asyncio
async def test_progress_is_isolated_per_user():
    prog_alice = get_migration_progress("alice")
    prog_bob = get_migration_progress("bob")

    assert prog_alice is not prog_bob
    prog_alice.is_running = True
    assert get_migration_progress("alice").is_running is True
    assert get_migration_progress("bob").is_running is False


@pytest.mark.asyncio
async def test_missing_user_id_raises(db_client):
    with pytest.raises(ValueError, match="user_id"):
        EmbeddingMigrationService(db_client, user_id="")  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_rebuild_uses_resolved_provider_not_global(db_client, monkeypatch):
    """Regression (debug/20260521 bug #1): the rebuild background task ran on
    /api/providers (quota-bypassed), so the request ContextVar carrying the
    user's embedding provider was never set — get_embedding fell back to the
    global OpenAI config with an empty key and every embed 401'd (0/N done).

    The fix resolves the user's provider explicitly and pins a dedicated
    EmbeddingClient to it. This test asserts the client is constructed from the
    resolver-provided (base_url, api_key, model), not the global config.
    """
    captured: dict = {}

    def spy_init(self, model=None, api_key=None, base_url=None, enable_cache=True):  # noqa: ARG001
        captured["model"] = model
        captured["api_key"] = api_key
        captured["base_url"] = base_url
        self.model = model  # minimal attr; skip real AsyncOpenAI construction

    async def fake_embed(self, text: str):  # noqa: ARG001
        return [1.0, 2.0, 3.0, 4.0]

    monkeypatch.setattr(mig_mod.EmbeddingClient, "__init__", spy_init)
    monkeypatch.setattr(mig_mod.EmbeddingClient, "embed", fake_embed)

    class FakeResolver:
        async def resolve(self, user_id: str):
            emb = EmbeddingConfig(
                api_key="user-key",
                base_url="https://user.example/v1",
                model="user-emb-model",
            )
            return (None, None, emb, "user")

    await _seed_agent(db_client, agent_id="agent_a", created_by="alice")
    await _seed_narrative(db_client, narrative_id="nar_a1", agent_id="agent_a")

    svc = EmbeddingMigrationService(db_client, user_id="alice", resolver=FakeResolver())
    await svc.rebuild_all()

    assert captured["api_key"] == "user-key"
    assert captured["base_url"] == "https://user.example/v1"
    assert captured["model"] == "user-emb-model"

    rows = await db_client.get("embeddings_store", filters={"entity_type": "narrative"})
    assert {r["model"] for r in rows} == {"user-emb-model"}, (
        "vectors must be stored under the resolved per-user model"
    )


@pytest.mark.asyncio
async def test_local_no_resolver_reads_user_providers(monkeypatch):
    """Gap B (debug/20260521): in local/desktop mode the resolver is disabled,
    but provider config still lives in user_providers (the table Settings
    writes to). `set_user_config` only primes a ContextVar a background task
    can't see, so the migration must read the user's embedding SLOT straight
    from the DB. It must read only the embedding slot (not the all-or-nothing
    get_user_llm_configs), so a user with only the embedding slot configured
    still works."""
    from types import SimpleNamespace
    from xyz_agent_context.agent_framework import user_provider_service as ups_mod

    fake = SimpleNamespace(
        slots={"embedding": SimpleNamespace(provider_id="prov_x", model="BAAI/bge-m3")},
        providers={"prov_x": SimpleNamespace(api_key="db-key", base_url="https://db.example/v1")},
    )

    async def fake_get(self, user_id):  # noqa: ARG001, ARG002
        return fake

    monkeypatch.setattr(ups_mod.UserProviderService, "get_user_config", fake_get)

    cfg = await mig_mod._resolve_user_embedding_cfg("alice", resolver=None, db=object())
    assert cfg.api_key == "db-key"
    assert cfg.base_url == "https://db.example/v1"
    assert cfg.model == "BAAI/bge-m3"


@pytest.mark.asyncio
async def test_status_swallows_resolver_gating_error(db_client, monkeypatch, force_new_embedding_path):
    """Status is display-only: a resolver gating error (no provider / quota)
    must not 500 — it falls back to the global model for display."""
    from xyz_agent_context.agent_framework.provider_resolver import (
        NoProviderConfiguredError,
    )

    class GatingResolver:
        async def resolve(self, user_id: str):
            raise NoProviderConfiguredError(user_id)

    svc = EmbeddingMigrationService(db_client, user_id="alice", resolver=GatingResolver())
    status = await svc.get_status()  # must not raise
    assert "model" in status
    # No env fallback: an unconfigured user renders an empty model, never a
    # value scavenged from the global embedding_config.
    assert status["model"] == ""


@pytest.mark.asyncio
async def test_no_embedding_provider_resolves_to_none(db_client):
    """No env / llm_config.json fallback: an unconfigured user resolves to
    None instead of scavenging credentials from the global holder."""
    cfg = await mig_mod._resolve_user_embedding_cfg("nobody", resolver=None, db=db_client)
    assert cfg is None


@pytest.mark.asyncio
async def test_rebuild_errors_clearly_when_no_provider(db_client):
    """Rebuild with no embedding provider must fail with a clear error
    recorded in progress — never silently embed against an env key."""
    await _seed_agent(db_client, agent_id="agent_a", created_by="alice")
    await _seed_narrative(db_client, narrative_id="nar_a1", agent_id="agent_a")

    svc = EmbeddingMigrationService(db_client, user_id="alice", resolver=None)
    await svc.rebuild_all()

    prog = get_migration_progress("alice")
    assert prog.error and "embedding provider" in prog.error.lower()
    assert prog.is_running is False
    # Nothing was embedded against a fallback key.
    rows = await db_client.get("embeddings_store", filters={"entity_type": "narrative"})
    assert rows == []


@pytest.mark.asyncio
async def test_status_counts_distinct_entity_ids_across_instances(
    db_client, patched_embedding, force_new_embedding_path, patched_embedding_cfg
):
    """An entity_id that exists under multiple module_instances must be
    counted ONCE, not once per instance.

    Regression (debug/20260521-embedding-rebuild-retry): embeddings_store is
    keyed on (entity_type, entity_id, model) — one vector per entity_id. But
    the entity count used COUNT(*) over instance_social_entities JOIN
    module_instances, which fans out to one row per (entity_id, instance_id).
    A user whose social-network entity appears in N instances therefore
    showed total=N but migrated=1 → a permanent "N-1 missing" that no rebuild
    could ever close (rebuild embeds distinct ids, all already done).
    """
    await _seed_agent(db_client, agent_id="agent_a", created_by="alice")
    await _seed_instance(db_client, instance_id="inst_a1", agent_id="agent_a", user_id="alice")
    await _seed_instance(db_client, instance_id="inst_a2", agent_id="agent_a", user_id="alice")

    # Same entity_id present in BOTH instances (the fan-out source) ...
    await _seed_entity(db_client, instance_id="inst_a1", entity_id="ent_dup", name="Dup")
    await _seed_entity(db_client, instance_id="inst_a2", entity_id="ent_dup", name="Dup")
    # ... plus one that lives in a single instance.
    await _seed_entity(db_client, instance_id="inst_a1", entity_id="ent_solo", name="Solo")

    svc = EmbeddingMigrationService(db_client, user_id="alice")

    # 2 distinct entities, not 3 (the JOIN would otherwise report 3).
    status_before = await svc.get_status()
    assert status_before["stats"]["entity"]["total"] == 2

    await svc.rebuild_all()

    status_after = await svc.get_status()
    assert status_after["stats"]["entity"]["total"] == 2
    assert status_after["stats"]["entity"]["migrated"] == 2
    # The bug surfaced as a permanent missing>0 here.
    assert status_after["stats"]["entity"]["missing"] == 0
    assert status_after["all_done"] is True

    # Exactly one embedding row per distinct entity_id (no per-instance dupes).
    emb = await db_client.get("embeddings_store", filters={"entity_type": "entity"})
    assert sorted(r["entity_id"] for r in emb) == ["ent_dup", "ent_solo"]

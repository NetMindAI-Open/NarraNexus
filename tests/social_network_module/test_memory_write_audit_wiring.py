"""
@file_name: test_memory_write_audit_wiring.py
@author:
@date: 2026-08-25
@description: The CALLER-side memory-write failures leave audit rows too.

`_entity_updater`'s own eight handlers are covered by
`test_entity_updater_alerts.py`. But memory writing does not only happen in
that file: `social_network_module` creates the primary entity and every
mentioned third-party entity itself, and those failures used to be
log-only — invisible to the `agent_runtime` alert layer because the hook
swallows at its outer edge.

Those two reporting call sites shipped without tests, which is the same
"new glue, zero coverage" gap this PR had already been sent back for one
layer down. Hence this file.

**Patch target matters.** `social_network_module.py` imports
`_report_write_failure` at module top level, so it holds a BOUND reference:
`monkeypatch.setattr(_entity_updater, "_report_write_failure", ...)` — what
the sibling test file does — has no effect here and would leave the
assertions silently passing against an empty list. Patch the attribute on
`social_network_module` instead.
"""
from __future__ import annotations

import pytest

import narranexus_plugins.social_network_module.social_network_module as snm
from narranexus_plugins.social_network_module._entity_updater import (
    ExtractedEntity,
)
from narranexus_plugins.social_network_module.social_network_module import (
    SocialNetworkModule,
)


@pytest.fixture
def audited(monkeypatch):
    rows = []

    async def _fake(*, operation, error, entity_id, instance_id, agent_id=""):
        rows.append(
            {
                "operation": operation,
                "entity_id": entity_id,
                "instance_id": instance_id,
                "agent_id": agent_id,
                "error": error,
            }
        )

    # NOT `_entity_updater._report_write_failure` — see the module docstring.
    monkeypatch.setattr(snm, "_report_write_failure", _fake)
    return rows


def _module() -> SocialNetworkModule:
    """``_process_mentioned_entities`` takes ``repo`` as an explicit
    argument, so the fake goes in through the front door. (Assigning it to
    the typed ``_social_repo`` attribute and reading it back would be one
    extra hop, and would put a stand-in on a declared
    ``Optional[SocialNetworkRepository]``.)"""
    return SocialNetworkModule(
        agent_id="agent_mine", database_client=None, instance_id="sn_1"
    )


class _StageOneBoom:
    """Stage-1 lookup fails — the quiet one: no match found reads as "brand
    new", so every turn forks another duplicate node."""

    async def search_by_name_or_alias(self, **kwargs):
        raise RuntimeError("database is locked")


async def test_mentioned_entity_failure_is_audited(audited):
    module, repo = _module(), _StageOneBoom()

    await module._process_mentioned_entities(
        repo, "sn_1", [ExtractedEntity(name="Bob")]
    )

    assert len(audited) == 1
    row = audited[0]
    assert row["operation"] == "process_mentioned_entity"
    assert row["agent_id"] == "agent_mine"
    assert row["instance_id"] == "sn_1"
    assert row["entity_id"] == "entity_bob", (
        "the audit row must name the entity that failed — not the previous "
        "one, and not blow up because the id was assigned inside the try"
    )


async def test_each_failing_entity_gets_its_own_row_with_its_own_id(audited):
    """Pins the `entity_id_candidate` binding: a stale value here would file
    every failure under whichever entity happened to be processed first."""
    module, repo = _module(), _StageOneBoom()

    await module._process_mentioned_entities(
        repo,
        "sn_1",
        [ExtractedEntity(name="Bob"), ExtractedEntity(name="Alice Smith")],
    )

    assert [r["entity_id"] for r in audited] == ["entity_bob", "entity_alice_smith"]


async def test_one_failing_entity_does_not_abort_the_batch(audited):
    """The loop must keep going — and must keep auditing — after one entity
    fails, or a single bad row silently drops the rest of the batch."""
    seen = []

    class _BoomOnBob:
        async def search_by_name_or_alias(self, **kwargs):
            # ``kwargs["name"]`` on purpose, not a ``.get`` chain: if the
            # real call's keyword is ever renamed this must raise, not
            # quietly record "" and let `len(seen) == 2` below decay from
            # "both entities were looked up" into "it was called twice".
            name = kwargs["name"]
            seen.append(name)
            if name == "Bob":
                raise RuntimeError("database is locked")
            return []

        async def add_entity(self, **kwargs):
            return None

    module, repo = _module(), _BoomOnBob()
    await module._process_mentioned_entities(
        repo,
        "sn_1",
        [ExtractedEntity(name="Bob"), ExtractedEntity(name="Carol")],
    )

    assert [r["entity_id"] for r in audited] == ["entity_bob"]
    assert len(seen) == 2, "the batch must continue past the failing entity"


async def test_the_two_caller_side_operations_are_the_expected_names():
    """`operation` is what an operator greps weeks later, so pin both
    spellings by VALUE.

    This used to grep ``inspect.getsource(snm)``, which is green when the
    call is deleted but the literal survives in a comment, and red when the
    reporting moves into a helper. Asserting the constants also removes the
    duplicate-knowledge of writing each string in the source and again here.

    ``create_primary_entity`` still has no behaviour test — its call site
    sits inside ``after_turn``'s ``if not entity:`` branch,
    which needs the whole hook stood up. This pins the spelling and that
    the constant is what the call site uses; the branch itself is not
    exercised.
    """
    assert snm._OP_CREATE_PRIMARY_ENTITY == "create_primary_entity"
    assert snm._OP_PROCESS_MENTIONED_ENTITY == "process_mentioned_entity"

"""
@file_name: test_agent_profile_tool.py
@author: NarraNexus
@date: 2026-08-04
@description: Setting an agent's name/description must be one transaction:
the DB row, the agent's own memory, and the peer-discovery row.

Two prod failures (P1 section 02, 2026-08-03) meet in this tool:

① Identity memory residue. The user named their first agent 「凑企鹅」, then at
   08:42/43 handed that same name to a SECOND agent with the rename tool. The
   tool wrote ``agents.agent_name`` and nothing else, so the first agent's
   long-term memory still asserted the old identity and it introduced itself as
   "凑企鹅 is actually my own agent name" (evt_1f9c6680) — while the DB said
   otherwise, and a second agent now legitimately held that name.

② A2A discovery death. ``agents.agent_description`` was only ever written at
   creation (as a placeholder) — no rename, config or skill install touched it,
   and the bus registry snapshotted whatever was there.

So the tool that establishes identity has to: write both fields, leave a
machine-written correction in the memory that is injected every turn, tell the
model when a name is already taken (rather than silently creating a duplicate),
and refresh discovery immediately.
"""
from __future__ import annotations

import pytest

from narranexus_plugins.awareness_module.awareness_module import AwarenessModule
# The identity-note helpers moved to the shared _awareness_writes when
# update_agent_profile was routed through the AgentDataStore seam; import from
# the package surface (which re-exports them).
from narranexus_plugins.awareness_module import (
    IDENTITY_CHANGE_SECTION,
    MAX_IDENTITY_CHANGE_ENTRIES,
    build_identity_change_note,
    merge_identity_change_note,
)

OWNER = "user_tc"


# ---------------------------------------------------------------------------
# The memory correction (pure functions — no DB)
# ---------------------------------------------------------------------------


def test_note_states_both_names_and_that_the_old_one_is_not_ours():
    note = build_identity_change_note("凑企鹅", "咕咕嘎嘎", when="2026-08-04")

    assert "凑企鹅" in note and "咕咕嘎嘎" in note
    assert "2026-08-04" in note
    # The residue case: memory keeps asserting the old name, so the correction
    # must explicitly retire it rather than only announce the new one.
    assert "no longer" in note.lower()


def test_the_note_is_appended_to_a_profile_not_rewritten_over_it():
    profile = "# Agent Awareness Profile\n\n## 1. Preferences\n- likes short answers\n"

    merged = merge_identity_change_note(profile, build_identity_change_note("A", "B"))

    assert "## 1. Preferences" in merged, "existing profile must survive verbatim"
    assert "- likes short answers" in merged
    assert IDENTITY_CHANGE_SECTION in merged


def test_a_chain_of_renames_leaves_one_current_record():
    """Each note supersedes the ones naming a different name, so a chain
    collapses to the note that is true now.

    Nothing is lost: the survivor names the previous name itself ("renamed from
    X to Y"). What goes is the contradiction — the incident showed one
    platform-voiced line is enough for the agent to introduce itself by the
    wrong name and defend it, so keeping five mutually exclusive ones and
    trusting the model to prefer the last is the same bet with worse odds.
    """
    profile = "# Agent Awareness Profile\n"
    for i in range(MAX_IDENTITY_CHANGE_ENTRIES + 4):
        profile = merge_identity_change_note(
            profile, build_identity_change_note(f"name{i}", f"name{i + 1}")
        )

    section = profile.split(IDENTITY_CHANGE_SECTION, 1)[1]
    entries = [ln for ln in section.splitlines() if ln.strip().startswith("- ")]
    last = f"name{MAX_IDENTITY_CHANGE_ENTRIES + 4}"
    assert len(entries) == 1, f"superseded records were kept: {entries}"
    assert last in entries[0]
    assert profile.count(IDENTITY_CHANGE_SECTION) == 1, "one section, not one per rename"


def test_records_agreeing_on_the_current_name_are_still_capped():
    """Pruning removes contradictions, not the growth bound.

    Entries asserting the SAME name survive each other — a rename followed by
    reconciliations of the same name is the real case — so the cap is still the
    only thing keeping the section from growing into the context window it
    lives in. Testing it with a chain of DIFFERENT names would pass on a build
    with no cap at all, since pruning alone leaves one entry.
    """
    profile = "# Agent Awareness Profile\n"
    for _ in range(MAX_IDENTITY_CHANGE_ENTRIES + 4):
        profile = merge_identity_change_note(
            profile, build_identity_change_note("old", "same")
        )

    section = profile.split(IDENTITY_CHANGE_SECTION, 1)[1]
    entries = [ln for ln in section.splitlines() if ln.strip().startswith("- ")]
    assert len(entries) == MAX_IDENTITY_CHANGE_ENTRIES


def test_an_empty_profile_still_gets_a_valid_section():
    merged = merge_identity_change_note("", build_identity_change_note("A", "B"))
    assert IDENTITY_CHANGE_SECTION in merged and "- " in merged


def test_content_after_the_section_survives_a_second_rename():
    """The docstring's promise, tested where it can actually break.

    ``update_awareness`` has the model rewrite the WHOLE profile and the prompt
    tells it to keep the full structured format, so the identity section
    routinely ends up in the MIDDLE. A merge that treats "everything after the
    marker" as the section silently ate the sections below it — the exact
    long-term-memory loss this function claims never to cause (found in review,
    2026-08-05: the first three cases all happened to put the section last).
    """
    profile = "\n".join([
        "# Agent Awareness Profile",
        "",
        "## 1. Preferences",
        "- likes short answers",
        "",
        IDENTITY_CHANGE_SECTION,
        "- 2026-08-04: renamed by your creator from A to B.",
        "",
        "## 2. Working Style",
        "- prefers async updates",
        "free-form observation that is not a bullet",
        "",
        "## 3. Role and Identity",
        "- owns the weekly report",
    ])

    merged = merge_identity_change_note(profile, build_identity_change_note("B", "C"))

    # Everything outside the identity section is preserved verbatim.
    for kept in (
        "## 1. Preferences", "- likes short answers",
        "## 2. Working Style", "- prefers async updates",
        "free-form observation that is not a bullet",
        "## 3. Role and Identity", "- owns the weekly report",
    ):
        assert kept in merged, f"lost: {kept!r}"

    # And the rename log is still one section holding both entries, in order.
    assert merged.count(IDENTITY_CHANGE_SECTION) == 1
    section = merged.split(IDENTITY_CHANGE_SECTION, 1)[1].split("\n## ", 1)[0]
    entries = [ln for ln in section.splitlines() if ln.strip().startswith("- ")]
    assert len(entries) == 2
    assert "from A to B" in entries[0], "the pre-existing entry keeps its wording"
    assert "B" in entries[1] and "C" in entries[1], "newest rename appended last"
    # A later section's bullets must not be absorbed as rename entries.
    assert all("async updates" not in e for e in entries)


# ---------------------------------------------------------------------------
# The tool, against a real database
# ---------------------------------------------------------------------------


@pytest.fixture
async def db(monkeypatch):
    from narranexus.platform.utils.db.database import AsyncDatabaseClient
    from narranexus.platform.utils.db.db_backend_sqlite import SQLiteBackend
    from narranexus.platform.utils.db.schema_registry import auto_migrate

    backend = SQLiteBackend(":memory:")
    await backend.initialize()
    await auto_migrate(backend)
    client = await AsyncDatabaseClient.create_with_backend(backend)

    # The MCP tools open their own connection; point it at this one. The tool
    # now delegates to the AgentDataStore seam, whose DirectStore resolves the
    # db via XYZBaseModule.get_mcp_db_client (the base classmethod) — so patch it
    # on the base, which also covers the AwarenessModule-inherited call.
    async def _client():
        return client

    from narranexus.platform.module_system.base import XYZBaseModule
    monkeypatch.setattr(XYZBaseModule, "get_mcp_db_client", _client)
    try:
        yield client
    finally:
        await client.close()


async def _seed_agent(db, agent_id: str, name: str, description: str = "") -> str:
    await db.insert("agents", {
        "agent_id": agent_id, "agent_name": name, "created_by": OWNER,
        "agent_description": description, "is_public": 0,
    })
    instance_id = f"aware_{agent_id[-4:]}"
    await db.insert("module_instances", {
        "instance_id": instance_id, "agent_id": agent_id, "user_id": OWNER,
        "module_class": "AwarenessModule", "status": "active",
    })
    await db.insert("instance_awareness", {
        "instance_id": instance_id,
        "awareness": "# Agent Awareness Profile\n\n## 4. Role and Identity\n- I am "
                     f"{name}\n",
    })
    return instance_id


def _tool(name: str):
    """Pull one registered MCP tool's function out of the module's server."""
    mcp = AwarenessModule(agent_id="agent_a").create_mcp_server()
    for tool in mcp._tool_manager.list_tools():
        if tool.name == name:
            return tool.fn
    raise AssertionError(f"tool {name} not registered")


@pytest.mark.asyncio
async def test_profile_update_writes_both_fields(db):
    await _seed_agent(db, "agent_a", "New Agent")

    result = await _tool("update_agent_profile")(
        agent_id="agent_a", new_name="咕咕嘎嘎",
        new_description="Reviews lesson plans and Q&A.",
    )

    assert "success" in result.lower()
    row = await db.get_one("agents", {"agent_id": "agent_a"})
    assert row["agent_name"] == "咕咕嘎嘎"
    assert row["agent_description"] == "Reviews lesson plans and Q&A."


@pytest.mark.asyncio
async def test_description_alone_can_be_set_without_touching_the_name(db):
    await _seed_agent(db, "agent_a", "咕咕嘎嘎")

    await _tool("update_agent_profile")(
        agent_id="agent_a", new_description="Reviews lesson plans."
    )

    row = await db.get_one("agents", {"agent_id": "agent_a"})
    assert row["agent_name"] == "咕咕嘎嘎"
    assert row["agent_description"] == "Reviews lesson plans."


@pytest.mark.asyncio
async def test_a_rename_corrects_the_identity_memory(db):
    """① the residue case. The profile is injected verbatim every turn, so the
    correction has to land IN it — otherwise the old self-description keeps
    winning."""
    instance_id = await _seed_agent(db, "agent_a", "凑企鹅")

    await _tool("update_agent_profile")(agent_id="agent_a", new_name="咕咕嘎嘎")

    awareness = (await db.get_one("instance_awareness",
                                  {"instance_id": instance_id}))["awareness"]
    assert IDENTITY_CHANGE_SECTION in awareness
    assert "凑企鹅" in awareness and "咕咕嘎嘎" in awareness
    assert "no longer" in awareness.lower()
    # Pre-existing profile content is untouched.
    assert "## 4. Role and Identity" in awareness


@pytest.mark.asyncio
async def test_setting_only_a_description_writes_no_rename_note(db):
    instance_id = await _seed_agent(db, "agent_a", "咕咕嘎嘎")

    await _tool("update_agent_profile")(
        agent_id="agent_a", new_description="Reviews lesson plans."
    )

    awareness = (await db.get_one("instance_awareness",
                                  {"instance_id": instance_id}))["awareness"]
    assert IDENTITY_CHANGE_SECTION not in awareness


@pytest.mark.asyncio
async def test_renaming_to_the_same_name_is_not_an_identity_change(db):
    instance_id = await _seed_agent(db, "agent_a", "咕咕嘎嘎")

    await _tool("update_agent_profile")(agent_id="agent_a", new_name="咕咕嘎嘎")

    awareness = (await db.get_one("instance_awareness",
                                  {"instance_id": instance_id}))["awareness"]
    assert IDENTITY_CHANGE_SECTION not in awareness


@pytest.mark.asyncio
async def test_a_name_already_held_by_a_sibling_is_reported_not_blocked(db):
    """The hand-off is legitimate (the user did it on purpose), but doing it
    SILENTLY is what produced two agents answering to one name. Allow it, and
    say so — the model can then check with the owner."""
    await _seed_agent(db, "agent_a", "凑企鹅")
    await _seed_agent(db, "agent_b", "New Agent")

    result = await _tool("update_agent_profile")(
        agent_id="agent_b", new_name="凑企鹅"
    )

    assert "agent_a" in result, "the current holder must be named"
    row = await db.get_one("agents", {"agent_id": "agent_b"})
    assert row["agent_name"] == "凑企鹅", "not blocked — the owner may intend this"


@pytest.mark.asyncio
async def test_another_users_agent_never_counts_as_a_name_clash(db):
    await db.insert("agents", {
        "agent_id": "agent_other", "agent_name": "凑企鹅",
        "created_by": "user_someone_else", "agent_description": "", "is_public": 0,
    })
    await _seed_agent(db, "agent_b", "New Agent")

    result = await _tool("update_agent_profile")(agent_id="agent_b", new_name="凑企鹅")

    assert "agent_other" not in result


@pytest.mark.asyncio
async def test_the_profile_update_refreshes_peer_discovery_immediately(db):
    """② target 2: a peer must be able to find the new description without
    waiting for this agent to take another turn."""
    from narranexus.platform.repository.agent_registry_repository import (
        AgentRegistryRepository,
    )

    await _seed_agent(db, "agent_a", "New Agent")

    await _tool("update_agent_profile")(
        agent_id="agent_a", new_name="咕咕嘎嘎",
        new_description="Reviews lesson plans.",
    )

    profile = await AgentRegistryRepository(db).get_profile("agent_a")
    assert profile is not None
    assert "咕咕嘎嘎" in profile.description
    assert "Reviews lesson plans." in profile.description


@pytest.mark.asyncio
async def test_rewriting_the_same_description_is_not_an_error(db):
    """Dialect trap: ``update_agent`` returns ``cursor.rowcount``, and MySQL
    (dev/prod — the pool sets no CLIENT_FOUND_ROWS) counts CHANGED rows while
    SQLite counts MATCHED ones. Without an equality short-circuit, re-saving an
    identical description returns 0 there and the agent is told
    "Error: the update did not apply" — for a write that was simply a no-op.

    The §5 prompt actively invites repeat calls ("whenever the answer
    changes"), and a model that reads an error usually retries or rewrites,
    so this would be a routine false failure on cloud only (review 2026-08-05).
    """
    await _seed_agent(db, "agent_a", "咕咕嘎嘎", description="Reviews lesson plans.")

    result = await _tool("update_agent_profile")(
        agent_id="agent_a", new_description="Reviews lesson plans."
    )

    assert "error" not in result.lower(), result
    row = await db.get_one("agents", {"agent_id": "agent_a"})
    assert row["agent_description"] == "Reviews lesson plans."


@pytest.mark.asyncio
async def test_same_name_and_same_description_together_are_a_no_op(db):
    await _seed_agent(db, "agent_a", "咕咕嘎嘎", description="Reviews lesson plans.")

    result = await _tool("update_agent_profile")(
        agent_id="agent_a", new_name="咕咕嘎嘎",
        new_description="  Reviews lesson plans.  ",   # padding is not a change
    )

    assert "error" not in result.lower(), result
    assert "no changes" in result.lower()


@pytest.mark.asyncio
async def test_calling_with_nothing_to_change_is_an_explicit_error(db):
    await _seed_agent(db, "agent_a", "咕咕嘎嘎")

    result = await _tool("update_agent_profile")(agent_id="agent_a")

    assert "error" in result.lower()


@pytest.mark.asyncio
async def test_unknown_agent_is_an_error_not_a_silent_success(db):
    result = await _tool("update_agent_profile")(
        agent_id="agent_missing", new_name="X"
    )
    assert "error" in result.lower()


def test_the_old_name_only_tool_is_gone():
    """No compat shim (iron rule #2): a tool that writes the name alone is the
    bug. If it comes back, the identity-memory correction is bypassable."""
    mcp = AwarenessModule(agent_id="agent_a").create_mcp_server()
    names = {t.name for t in mcp._tool_manager.list_tools()}
    assert "update_agent_profile" in names
    assert "update_agent_name" not in names


def test_every_identity_note_states_the_current_name_readably():
    """Whatever a note says, `identity_note_asserts` must find the name in it.

    Superseding a stale record depends on reading back what each record claims,
    so a note phrased outside that shape silently asserts nothing: it is
    appended beside the record it was written to replace, and the contradiction
    the whole mechanism exists to remove survives. That is exactly what the
    reconciliation note did on its first draft ("Your name is 「X」").
    """
    from narranexus_plugins.awareness_module import (
        build_identity_reconciliation_note,
        identity_note_asserts,
    )

    assert identity_note_asserts(build_identity_change_note("A", "B")) == "B"
    assert (
        identity_note_asserts(build_identity_reconciliation_note("B", "A")) == "B"
    )


class TestSelfNameLine:
    """A rename must also retire the agent's own "my name is X" line.

    Measured, 2026-08-19: with every platform-owned source corrected — the row,
    BasicInfo's `Agent Name`, the identity record, the peer directory — a real
    two-turn run still answered with the OLD name, because the profile's Role
    and Identity section still read `- 名称：美食家` and sits ABOVE the
    correction. Iron rule #15 decides this: the name is machine-knowable, so
    the platform derives it rather than hoping the model prefers one line over
    another. The 2026-08-04 "not ours to edit" principle protects the agent's
    observations about its OWNER; its own name is not one of those.
    """

    def test_the_self_name_line_is_rewritten(self):
        from narranexus_plugins.awareness_module import retire_self_name

        profile = (
            "# Agent Awareness Profile\n\n"
            "## 4. Role and Identity\n"
            "- 名称：美食家；精通各地美食推荐\n"
        )
        out = retire_self_name(profile, "美食家", "小绿")
        assert "- 名称：小绿；精通各地美食推荐" in out
        assert "美食家" not in out

    @pytest.mark.parametrize(
        "line",
        [
            "- 名称：美食家",
            "- 名字：美食家",
            "- Name: 美食家",
            "* name: 美食家",
            "名称: 美食家",
        ],
    )
    def test_the_common_spellings_are_covered(self, line):
        from narranexus_plugins.awareness_module import retire_self_name

        out = retire_self_name(f"## Role\n{line}\n", "美食家", "小绿")
        assert "小绿" in out and "美食家" not in out

    def test_prose_about_the_owner_is_untouched(self):
        """The line match is narrow on purpose. An owner observation that
        happens to contain the old name is not a self-name declaration, and
        losing it to a rename would be a worse bug than the one being fixed."""
        from narranexus_plugins.awareness_module import retire_self_name

        profile = (
            "## 4. Role and Identity\n- 名称：美食家\n\n"
            "## 5. Owner observations\n"
            "- owner 说他上次在美食家那家店吃过饭\n"
            "- owner 更喜欢简短回答\n"
        )
        out = retire_self_name(profile, "美食家", "小绿")
        assert "- 名称：小绿" in out
        assert "owner 说他上次在美食家那家店吃过饭" in out, (
            "an owner observation was rewritten"
        )

    def test_a_name_that_is_not_declared_anywhere_changes_nothing(self):
        from narranexus_plugins.awareness_module import retire_self_name

        profile = "## 4. Role and Identity\n- 我擅长推荐美食\n"
        assert retire_self_name(profile, "美食家", "小绿") == profile


    @pytest.mark.parametrize(
        "line",
        [
            "- name: 美食家 是 owner 最近常去的那家店",
            "- 名称：美食家上个月换了老板",
            "- Name: 美食家的推荐一向很准",
        ],
    )
    def test_a_line_that_only_starts_with_the_marker_is_not_a_declaration(
        self, line
    ):
        """The narrow match is the whole safety property.

        A value that keeps talking after the name is prose about something
        else, and rewriting it is the content loss this area promises never to
        cause. Only "the name IS the value" — optionally followed by a
        separator that opens a description — counts.
        """
        from narranexus_plugins.awareness_module import retire_self_name

        profile = f"## 5. Owner observations\n{line}\n"
        assert retire_self_name(profile, "美食家", "小绿") == profile

    @pytest.mark.parametrize("sep", ["；", ";", "，", ",", "、", "(", "（"])
    def test_a_description_opener_still_opens_a_description(self, sep):
        """Openers only. `/` and `-` were in this list and are the reason three
        rounds produced corrupted names: they live inside names, so the name now
        runs through them to the first opener."""
        from narranexus_plugins.awareness_module import retire_self_name

        out = retire_self_name(f"- 名称：美食家{sep}精通各地美食\n", "美食家", "小绿")
        assert out == f"- 名称：小绿{sep}精通各地美食\n"


    # A profile shaped like the template the prompt actually prescribes:
    # sections 1-3 are the OWNER's preferences and observations, section 4 is
    # the agent's own identity. Every other test in this class passes bare
    # fragments, so none of them can see a scoping regression.
    FULL_PROFILE = (
        "# Agent Awareness Profile\n"
        "\n"
        "## 1. Narrative Management Preferences (Topic Organization)\n"
        "### Topic Continuity Style\n"
        "- owner 习惯一个话题聊到底\n"
        "\n"
        "## 3. Communication Style Preferences (Interaction)\n"
        "### Tone and Voice\n"
        "- 姓名：美食家\n"          # the OWNER's own name, as they are addressed
        "- 偏好简短回答\n"
        "\n"
        "## 4. Role and Identity\n"
        "### Role Definition\n"
        "- 名称：美食家；精通各地美食推荐\n"
    )

    def test_only_the_identity_section_is_rewritten(self):
        """Sections 1-3 are about the OWNER.

        An owner recorded as `- 姓名：美食家` in Communication Style, for an
        agent that also happened to be called 美食家, would have their name
        silently replaced by the agent's new one — unrecoverable, because
        instance_awareness is overwritten by upsert and nothing logged it.
        """
        from narranexus_plugins.awareness_module import retire_self_name

        out = retire_self_name(self.FULL_PROFILE, "美食家", "小绿")

        assert "- 名称：小绿；精通各地美食推荐" in out, "the identity line was not retired"
        assert "- 姓名：美食家" in out, "an owner observation in section 3 was rewritten"

    def test_scoping_survives_a_renumbered_identity_section(self):
        """Excluded by negation, not by matching section 4's title.

        The model writes these headings; requiring an exact "## 4. Role and
        Identity" would make retirement stop silently the first time it drifts.
        """
        from narranexus_plugins.awareness_module import retire_self_name

        drifted = (
            "## 1. Narrative Management Preferences\n- 姓名：美食家\n\n"
            "## 5. 身份与角色\n- 名称：美食家\n"
        )
        out = retire_self_name(drifted, "美食家", "小绿")
        assert "## 5. 身份与角色\n- 名称：小绿" in out
        assert "## 1. Narrative Management Preferences\n- 姓名：美食家" in out


    def test_an_owner_name_line_in_an_unnumbered_section_is_still_safe(self):
        """The scope must fail toward NOT editing.

        This PR's own integration fixtures put owner content in `## 5. Owner
        observations` while a unit fixture treated `## 5. 身份与角色` as the
        agent's — two opposite answers for the same section number, with the
        code implementing the dangerous one. The harms are not symmetric:
        skipping a stale self-name line leaves a visible, recoverable symptom
        that the identity record still corrects, while rewriting an owner's
        name is unrecoverable (upsert overwrites, and a log line is not a
        record). So the identity section is matched POSITIVELY and everything
        else is owner territory.
        """
        from narranexus_plugins.awareness_module import retire_self_name

        profile = (
            "## 4. Role and Identity\n- 名称：美食家\n\n"
            "## 5. Owner observations\n- 姓名：美食家\n"
        )
        out = retire_self_name(profile, "美食家", "小绿")
        assert "## 4. Role and Identity\n- 名称：小绿" in out
        assert "## 5. Owner observations\n- 姓名：美食家" in out, (
            "an owner name line outside the identity section was rewritten"
        )


@pytest.mark.asyncio
async def test_a_model_rewrite_cannot_delete_the_platform_record(db):
    """`update_awareness` hands the model the whole document to rewrite, and its
    prescribed format lists four sections — none of them the platform record.

    So the transaction wrote the correction, and the very next time the model
    reorganised its profile (which §5 and the tool's own "When to Update"
    actively encourage) the record went with it: the agent back to a stale
    self-name line with nothing contradicting it, and no way to tell it had
    ever been fixed. Iron rule #15 decides the shape of the fix: prompt text is
    a supplement, never the mechanism, so the section is carried over in code.
    """
    from narranexus.platform.module_system.data_access.store import DirectStore
    from narranexus_plugins.awareness_module import (
        IDENTITY_CHANGE_SECTION, build_identity_change_note,
        merge_identity_change_note,
    )
    from narranexus.platform.repository import InstanceAwarenessRepository

    instance_id = await _seed_agent(db, "agent_rw", "小绿")
    kept = merge_identity_change_note(
        "# Agent Awareness Profile\n\n## 4. Role and Identity\n- 名称：小绿\n",
        build_identity_change_note("美食家", "小绿"),
    )
    await InstanceAwarenessRepository(db).upsert(instance_id, kept)

    # What the model sends back: the four prescribed sections, record dropped.
    await DirectStore().update_awareness(
        "agent_rw",
        "# Agent Awareness Profile\n\n## 4. Role and Identity\n- 名称：小绿\n",
    )

    after = (await db.get_one("instance_awareness", {"instance_id": instance_id}))["awareness"]
    assert IDENTITY_CHANGE_SECTION in after, "the platform record was rewritten away"
    assert "You are 「小绿」" in after


class TestNamesContainingSeparators:
    """A name may contain the characters used to spot where a name ends.

    `declared_self_name` guessed the boundary by splitting on the first
    separator, so an agent called `小绿-2` read as `小绿`, disagreed with its own
    row, and every call "corrected" the line by prefixing the name again:
    小绿-2 → 小绿-2-2 → 小绿-2-2-2. Self-amplifying corruption of the one
    document the platform edits on the agent's behalf, and `upsert` keeps no
    earlier copy.
    """

    def test_a_line_already_holding_the_current_name_is_not_stale(self):
        """Given the name we already know, the boundary is not guessed at all.

        Without it the function can only split on the first separator — which is
        exactly why the caller passes it. The two-argument form is the contract;
        the one-argument form is a best effort and says so.
        """
        from narranexus_plugins.awareness_module import declared_self_name

        profile = "## 4. Role and Identity\n- 名称：小绿-2\n"
        assert declared_self_name(profile, "小绿-2") == "小绿-2"
        # A hyphen is not a boundary, so the whole name comes back either way —
        # which is what stops `小绿-2` from being read as `小绿` and re-prefixed.
        assert declared_self_name(profile, "美食家") == "小绿-2"

    def test_the_description_tail_still_comes_off(self):
        from narranexus_plugins.awareness_module import declared_self_name

        profile = "## 4. Role and Identity\n- 名称：美食家；精通各地美食推荐\n"
        assert declared_self_name(profile) == "美食家"

    @pytest.mark.asyncio
    async def test_reconciling_a_separator_name_neither_fires_nor_corrupts(self, db):
        from narranexus.platform.agent_profile import apply_agent_profile_change

        instance_id = await _seed_agent(db, "agent_sep", "小绿-2")
        from narranexus.platform.repository import InstanceAwarenessRepository
        await InstanceAwarenessRepository(db).upsert(
            instance_id, "# Agent Awareness Profile\n\n## 4. Role and Identity\n- 名称：小绿-2\n"
        )

        for _ in range(3):
            r = await apply_agent_profile_change(db, "agent_sep", new_name="小绿-2")
            assert r.identity_reconciled is None, "false-positive reconcile"

        after = (await db.get_one("instance_awareness", {"instance_id": instance_id}))["awareness"]
        assert "- 名称：小绿-2\n" in after, f"the name line was mangled: {after!r}"

    def test_a_weak_separator_line_is_simply_not_a_declaration(self):
        """This used to assert a LOUD refusal for `小绿` vs `小绿-2`.

        That guard existed because a hyphen might be the end of a name. It is
        not, so `- 名称：小绿-3` does not declare `小绿` at all — there is nothing
        to refuse, and nothing to rewrite. The refusal was rejecting exact
        renames (小绿 → 小绿2) as collateral.
        """
        from narranexus_plugins.awareness_module import retire_self_name

        profile = "## 4. Role and Identity\n- 名称：小绿-3\n"
        assert retire_self_name(profile, "小绿", "小绿-2") == profile


class TestAppendingToAName:
    """`小绿` → `小绿2` is one of the most ordinary renames there is.

    A guard added in round 10 refused any rewrite where the two names were
    prefixes of each other, because a hyphen could then be mistaken for the end
    of a name. Round 13 removed that premise — weak characters are no longer
    boundaries — which left the guard rejecting renames it can now do exactly,
    and reporting identity_record_updated=False on the most common shape. A
    signal that cries wolf on the common case is not a signal.
    """

    def test_a_suffix_rename_retires_the_line(self):
        from narranexus_plugins.awareness_module import retire_self_name

        out = retire_self_name("## Role and Identity\n- 名称：小绿\n", "小绿", "小绿2")
        assert "- 名称：小绿2\n" in out

    def test_a_suffix_rename_keeps_the_description(self):
        from narranexus_plugins.awareness_module import retire_self_name

        out = retire_self_name(
            "## Role and Identity\n- 名称：小绿；精通各地美食推荐\n", "小绿", "小绿2"
        )
        assert "- 名称：小绿2；精通各地美食推荐\n" in out

    def test_a_line_naming_something_else_is_still_left_alone(self):
        """The property the guard was protecting, now carried by the opener rule:
        `- 名称：小绿-3` does not declare `小绿`, because a hyphen does not end a
        name — so there is nothing here to retire."""
        from narranexus_plugins.awareness_module import retire_self_name

        profile = "## Role and Identity\n- 名称：小绿-3\n"
        assert retire_self_name(profile, "小绿", "小绿2") == profile


class TestInferredNameIsNeverTrusted:
    """The repair path infers the old name from the line; the rename path is
    told it by the row. Only the second may drive a rewrite unconditionally.

    Fourth corruption from the same inference, and the worst: absent a strong
    opener, `_name_part` returns the WHOLE value, which then satisfies
    `startswith(old)` and an empty remainder — so the entire text after the name
    was replaced by the current name. Everything after 名称： was deleted, on a
    plain same-name save, with `identity_record_updated: true` and no warning.

    The rule now: an inferred old name may drive a rewrite only when the
    inference actually found a boundary (a strong opener) or the value is a bare
    name. Otherwise refuse — loudly, so the caller reports it.
    """

    @pytest.mark.parametrize(
        "line",
        [
            "- 名称：美食家 是 owner 最近常去的那家店",
            "- 名称：小绿 - 美食推荐专家",
            "- 名称：小绿 Xiao Lv",
        ],
    )
    def test_a_value_whose_boundary_was_not_found_is_refused(self, line):
        from narranexus_plugins.awareness_module._awareness_writes import (
            _AmbiguousSelfName,
        )
        from narranexus_plugins.awareness_module import (
            declared_self_name, retire_self_name,
        )

        profile = f"## 4. Role and Identity\n{line}\n"
        declared = declared_self_name(profile, "小绿")
        with pytest.raises(_AmbiguousSelfName) as caught:
            retire_self_name(profile, declared, "小绿", inferred=True)
        assert caught.value.profile == profile, "the text must survive untouched"

    def test_an_opener_boundary_is_still_trusted(self):
        """The shape the template produces and prod held — unchanged."""
        from narranexus_plugins.awareness_module import (
            declared_self_name, retire_self_name,
        )

        profile = "## 4. Role and Identity\n- 名称：美食家；精通各地美食推荐\n"
        declared = declared_self_name(profile, "小绿")
        out = retire_self_name(profile, declared, "小绿", inferred=True)
        assert "- 名称：小绿；精通各地美食推荐" in out

    def test_the_rename_path_is_unaffected(self):
        """Told the old name by the row, a rewrite needs no inference — and a
        line that merely starts with it is still not a declaration."""
        from narranexus_plugins.awareness_module import retire_self_name

        exact = "## 4. Role and Identity\n- 名称：美食家\n"
        assert "- 名称：小绿" in retire_self_name(exact, "美食家", "小绿")

        prose = "## 4. Role and Identity\n- 名称：美食家 是 owner 常去那家店\n"
        assert retire_self_name(prose, "美食家", "小绿") == prose


class TestMarkerOnlyLinesInsideTheIdentitySection:
    """The same invariant, inside the EDITABLE region.

    `test_a_line_that_only_starts_with_the_marker_is_not_a_declaration` used
    `## 5. Owner observations`, so it was protected by the section scope as well
    and could not see a failure of the predicate itself.
    """

    @pytest.mark.parametrize(
        "line",
        [
            "- name: 美食家 是 owner 最近常去的那家店",
            "- 名称：美食家上个月换了老板",
            "- Name: 美食家的推荐一向很准",
        ],
    )
    def test_prose_in_the_identity_section_is_still_not_a_declaration(self, line):
        from narranexus_plugins.awareness_module import retire_self_name

        profile = f"## 4. Role and Identity\n{line}\n"
        assert retire_self_name(profile, "美食家", "小绿") == profile


class TestHeadingLevels:
    """Scope must track any heading level, but only reset at the same level or
    higher.

    `_ANY_H2` matched `## ` only, so a profile the model organised with H1 or H3
    never left the "preamble" — where editable defaults to True. The positive
    identity match, whose whole purpose is "default to not touching the owner's
    text", was inert on that entire class of document, and every test but one
    used bare fragments or all-`##` profiles.
    """

    def test_an_h1_owner_section_is_not_editable(self):
        from narranexus_plugins.awareness_module import retire_self_name

        profile = (
            "# 1. Owner preferences\n- 姓名：美食家\n\n"
            "# 4. Role and Identity\n- 名称：美食家\n"
        )
        out = retire_self_name(profile, "美食家", "小绿")
        assert "# 1. Owner preferences\n- 姓名：美食家" in out, (
            "an owner name line under an H1 heading was rewritten"
        )
        assert "# 4. Role and Identity\n- 名称：小绿" in out

    def test_an_h3_owner_subsection_is_not_editable(self):
        from narranexus_plugins.awareness_module import retire_self_name

        profile = (
            "## 3. Communication Style\n### Tone\n- 姓名：美食家\n\n"
            "## 4. Role and Identity\n- 名称：美食家\n"
        )
        out = retire_self_name(profile, "美食家", "小绿")
        assert "### Tone\n- 姓名：美食家" in out, "an owner line under an H3 was rewritten"
        assert "- 名称：小绿" in out

    def test_a_subsection_does_not_close_its_identity_section(self):
        """`### Role Definition` sits INSIDE `## 4. Role and Identity`.

        Resetting scope on every heading would close the identity section at its
        own subsection — the template has exactly this shape — and retirement
        would silently stop. Only a heading at the same level or higher may end
        it, and this one names no keyword on purpose.
        """
        from narranexus_plugins.awareness_module import retire_self_name

        profile = "## 4. Role and Identity\n### Definition\n- 名称：美食家\n"
        out = retire_self_name(profile, "美食家", "小绿")
        assert "- 名称：小绿" in out, "the identity section was closed by its own subsection"


class TestScopeAfterAShallowerFirstHeading:
    """`level` was pinned by the document's FIRST heading.

    `# Agent Awareness Profile` set level=1; `## 4. Role and Identity` opened
    the identity section through the deeper-heading branch WITHOUT updating
    level, so `## 5. Owner observations` needed depth <= 1 to close it — never
    true. The identity section, once opened by a deeper heading, could not be
    closed, and everything after it became editable. On dev the H1 was simply
    ignored, so this was a regression introduced by the H1/H3 fix.

    FULL_PROFILE could not see it: its owner sections sit BEFORE the identity
    section, and the leak only affects what comes after.
    """

    HEADED = (
        "# Agent Awareness Profile\n"
        "\n## 4. Role and Identity\n- 名称：美食家\n"
        "\n## 5. Owner observations\n- 姓名：美食家\n"
    )

    def test_an_owner_section_after_the_identity_section_stays_closed(self):
        from narranexus_plugins.awareness_module import retire_self_name

        out = retire_self_name(self.HEADED, "美食家", "小绿")
        assert "## 5. Owner observations\n- 姓名：美食家" in out, (
            "the owner's own name line after the identity section was rewritten"
        )
        assert "- 名称：小绿" in out

    def test_declared_self_name_cannot_read_from_a_leaked_owner_section(self):
        """The repair-path half: the identity section has no name line, so the
        first declaration the scan finds comes from the leaked `## 5` — and the
        owner's name (bare, no separator) passes every inferred-name check."""
        from narranexus_plugins.awareness_module import declared_self_name

        profile = (
            "# Agent Awareness Profile\n"
            "\n## 4. Role and Identity\n### Role Definition\n- 负责美食推荐\n"
            "\n## 5. Owner observations\n- 姓名：张三\n"
        )
        assert declared_self_name(profile, "小绿") is None, (
            "the owner's name was read as the agent's declaration"
        )

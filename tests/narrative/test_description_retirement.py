"""
@file_name: test_description_retirement.py
@date: 2026-08-20
@description: `narrative_info.description` is a birth certificate, not a
medical record: it may be read ONLY while `current_summary` is still empty.

THE DEFECT (prod, whole `narratives` table, 2026-08-20)

`description` is written once at creation from the raw triggering input, is
never rewritten by the updater, and yet it is in `searchable_text()` — i.e. in
the BM25 index — and in the continuity prompt. A thread born on a 5KB scheduled
task prompt therefore has that 5KB welded into its retrieval surface forever.

Magnitude, measured on all 1,381 non-default prod narratives (NOT the 146-row
"chosen lines" fetch, which understated the tail by ~8x):

    description length   0 | 1-200 | 201-512 | 513-1500 | 1501-6000 | >6000
    narratives         259 |   675 |      80 |       76 |       216 |    75
                                                          -------------------
                                                          291 = 21.1% > 1500

`max = 198,398 characters`. Total description text in the index: 2.57 MB.

Why 21% matters more than it sounds: BM25 computes IDF and avgdl over the
candidate pool ITSELF, so one 6KB document both raises avgdl (crushing every
normal document's length normalisation) and hands itself a large pool of
matchable tokens. Offline re-scoring of the two PR②-v2 arms (630 scorable
decisions, byte-exact replay) shows the bypass rate for sequences containing a
bloated thread at **41.0%** against **14.5%** for those without — a 3.7x effect
that swamped the arm-level measurement it was supposed to serve.

THE RULE

    description is read ONLY while current_summary is empty.

Full retirement once the summary exists, not truncate-and-keep-reading. The
condition is on the summary rather than on "the updater has run" because the
updater is async and can fail: a thread born during a helper outage never gets
a summary. A conditional rule self-heals — record written, birth certificate
retires; record stillborn, birth certificate keeps standing in so the thread
does not go invisible. Prod today: 191 non-default threads have no summary, and
all 191 also have an empty description, so the safety branch currently protects
nobody — it is a net, not a load-bearing path. Meanwhile all 291 bloated
threads DO have a summary, so the rule retires 100% of the bloat.

Dry run before implementing (`tools/description_retirement_dryrun.py`):

    variant        bloat-group bypass   max top1   gold rows made unretrievable
    (today)                     41.0%      548.9   -
    retirement                   8.8%      152.6   19
    truncate 512                15.3%      252.1   0
    truncate 200                12.8%      204.1   0

All 19 were fossil-only hits: with the description removed the thread matched
ZERO query terms, 15 of the 19 scored below RAW_FLOOR to begin with (0.29-1.39,
a single incidental character like `好` / `叫` / `you`), and the ones the live
decision actually landed on landed via `no_topic_anchored`, i.e. the session
anchor, not the BM25 menu. One class is genuine and named in the report: a
thread whose summary has drifted away from its founding question loses that
founding question as a retrieval signal (`p05:18`).
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from narranexus.platform.narrative.models import (
    Narrative,
    NarrativeInfo,
    NarrativeType,
)


def _narrative(*, name: str, summary: str, description: str,
               keywords: list[str] | None = None) -> Narrative:
    now = datetime.now(timezone.utc)
    return Narrative(
        id="nar_x",
        type=NarrativeType.CHAT,
        agent_id="agent_x",
        narrative_info=NarrativeInfo(
            name=name, description=description, current_summary=summary, actors=[]
        ),
        topic_keywords=keywords or [],
        event_ids=[],
        created_at=now,
        updated_at=now,
    )


# ---------------- the accessor -----------------------------------------------


def test_the_birth_certificate_retires_once_a_summary_exists() -> None:
    n = _narrative(name="Lark 绑定闭环", summary="Gmail 修复完成；Lark 授权已绑定",
                   description="Created based on query: " + "泡沫" * 2000)
    assert n.description_if_unsummarised() == ""


def test_the_birth_certificate_stands_in_while_there_is_no_summary() -> None:
    """The updater is async AND can fail — a thread must not go invisible."""
    n = _narrative(name="Untitled", summary="",
                   description="Created based on query: 帮我排查部署脚本报错")
    assert n.description_if_unsummarised() == (
        "Created based on query: 帮我排查部署脚本报错"
    )


def test_a_whitespace_only_summary_does_not_count_as_a_record() -> None:
    n = _narrative(name="x", summary="   \n\t ", description="the founding question")
    assert n.description_if_unsummarised() == "the founding question"


# ---------------- read site 1: the BM25 retrieval surface --------------------


def test_the_fossil_leaves_the_retrieval_surface() -> None:
    """The p16 specimen shape: a 5,324-char description on a summarised thread."""
    fossil = "Created based on query: " + "定时任务上下文" * 760   # ~5.3k chars
    n = _narrative(name="北郡STEAM海报", summary="海报可爱版已交付", description=fossil,
                   keywords=["海报", "北郡"])
    surface = n.searchable_text()
    assert fossil not in surface
    assert "定时任务上下文" not in surface
    # and the living record is still all there
    assert "北郡STEAM海报" in surface
    assert "海报可爱版已交付" in surface
    assert "北郡" in surface
    assert len(surface) < 200, f"surface still carries the fossil: {len(surface)}"


def test_an_unsummarised_thread_keeps_its_whole_surface() -> None:
    n = _narrative(name="Untitled", summary="",
                   description="Created based on query: 一英里等于多少公里",
                   keywords=["单位换算"])
    surface = n.searchable_text()
    assert "一英里等于多少公里" in surface, (
        "a thread with no summary lost its only description of itself"
    )
    assert "单位换算" in surface


def test_the_bloated_thread_stops_dominating_a_pool() -> None:
    """End to end through the real ranker — the defect's actual signature.

    A 6KB fossil both inflates avgdl and hands itself matchable tokens, so it
    outranks the thread the query is really about. That is what made the bypass
    rate 3.7x higher in sequences containing one.
    """
    from narranexus.platform.narrative._narrative_impl.retrieval import NarrativeRetrieval

    fossil = _narrative(
        name="Firstrade 入金方式攻略",
        summary="入金方式及流程已整理",
        description="Created based on query: " + (
            "本周末适合家庭聚餐的意大利餐厅 营业时间 评分 来源链接 " * 120
        ),
    )
    real = _narrative(
        name="纽约餐厅推荐", summary="用户询问纽约本周末适合家庭聚餐的意大利餐厅",
        description="Created based on query: 纽约餐厅",
    )
    pool = [("nar_fossil", fossil.searchable_text(), False),
            ("nar_real", real.searchable_text(), False)]

    ranked = NarrativeRetrieval.rank_pool(
        "推荐纽约本周末适合家庭聚餐的意大利餐厅", pool, 3
    )
    assert ranked, "the query must still match something"
    assert ranked[0].narrative_id == "nar_real", (
        "the fossil still outranks the thread the query is about"
    )


# ---------------- read site 2: the continuity prompt -------------------------


@pytest.mark.asyncio
async def test_the_continuity_prompt_does_not_carry_the_fossil() -> None:
    """The second read site. Only the SDK edge is doubled — the prompt that
    reaches it is assembled by the real production code."""
    from narranexus.platform.narrative._narrative_impl.continuity import ContinuityDetector

    fossil = "Created based on query: " + "历史包袱" * 1000
    n = _narrative(name="Lark 绑定闭环", summary="授权已绑定", description=fossil)

    captured: dict = {}

    class _SDK:
        async def llm_function(self, **kw):
            captured.update(kw)
            out = type("O", (), {"is_continuous": False, "confidence": 0.5,
                                 "reason": "stubbed"})()
            return type("R", (), {"final_output": out})()

    detector = ContinuityDetector.__new__(ContinuityDetector)
    detector.sdk = _SDK()

    await detector._call_llm(
        previous_query="上一句", previous_response="上一条回复",
        current_query="那第二步呢", time_elapsed_minutes=1.0,
        current_narrative=n,
    )

    blob = str(captured.get("user_input") or "")
    assert blob, "the prompt was not captured — the double is wired wrong"
    assert "历史包袱" not in blob, "the continuity prompt still carries the fossil"
    assert "授权已绑定" in blob, "the living summary must still reach the prompt"
    assert "- Description:" not in blob, (
        "an empty `- Description:` label reads as 'this thread has no "
        "description', which is a different claim than not mentioning it"
    )


@pytest.mark.asyncio
async def test_the_continuity_prompt_keeps_the_description_while_unsummarised() -> None:
    from narranexus.platform.narrative._narrative_impl.continuity import ContinuityDetector

    n = _narrative(name="Untitled", summary="",
                   description="Created based on query: 帮我排查部署脚本报错")
    captured: dict = {}

    class _SDK:
        async def llm_function(self, **kw):
            captured.update(kw)
            out = type("O", (), {"is_continuous": True, "confidence": 0.9,
                                 "reason": "stubbed"})()
            return type("R", (), {"final_output": out})()

    detector = ContinuityDetector.__new__(ContinuityDetector)
    detector.sdk = _SDK()
    await detector._call_llm(
        previous_query="上一句", previous_response="上一条回复",
        current_query="那第二步呢", time_elapsed_minutes=1.0,
        current_narrative=n,
    )
    blob = str(captured.get("user_input") or "")
    assert "- Description: Created based on query: 帮我排查部署脚本报错" in blob


# ---------------- write site: never mint a new fossil ------------------------


@pytest.mark.asyncio
async def test_a_new_narrative_never_stores_an_unbounded_description() -> None:
    """Creation truncated the two siblings and not this one — prod max 198,398."""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from narranexus.platform.narrative._narrative_impl.retrieval import NarrativeRetrieval
    from narranexus.platform.narrative.config import config

    retrieval = NarrativeRetrieval.__new__(NarrativeRetrieval)
    captured: dict = {}

    async def _create(**kwargs):
        captured.update(kwargs)
        return _narrative(name=kwargs["title"], summary="",
                          description=kwargs["description"])

    retrieval._crud = SimpleNamespace(create=_create, save=AsyncMock())

    huge = "定时任务上下文说明 " * 900          # ~9k chars
    await retrieval.create_from_query(
        query=huge, user_id="u", agent_id="a", narrative_type=NarrativeType.CHAT
    )

    assert len(captured["description"]) <= config.DESCRIPTION_MAX_LENGTH + 64, (
        f"description stored at {len(captured['description'])} chars"
    )
    assert len(captured["title"]) <= 30 + 8


@pytest.mark.asyncio
async def test_every_creation_door_is_bounded_not_just_the_routing_one(db_client) -> None:
    """`create_from_query` is one of THREE doors that write a description.

    The others are the LLM's `create_narrative` signal
    (`step_4_persist_results.py`, description straight from tool args) and the
    HTTP route. Clamping in `crud.create` covers all three with one line; a fix
    on the routing door alone leaves two open.
    """
    from narranexus.platform.narrative._narrative_impl.crud import NarrativeCRUD
    from narranexus.platform.narrative.config import config

    crud = NarrativeCRUD("agent_x")
    crud.set_database_client(db_client)

    narrative = await crud.create(
        agent_id="agent_x", user_id="u", title="t",
        description="X" * 50_000, save_to_db=False,
    )
    assert len(narrative.narrative_info.description) <= config.DESCRIPTION_MAX_LENGTH


def test_the_bound_does_not_clip_a_curated_default_bucket() -> None:
    """The guard against someone later "aligning" this to SUMMARY_MAX_LENGTH.

    The bucket descriptions reach the LLM judge. `GreetingAndCourtesy` is 206
    chars, so a 200-char bound would silently truncate curated prompt content —
    exactly the kind of quiet change the P1 prompt freeze exists to stop.
    """
    from narranexus.platform.narrative._narrative_impl.default_narratives import (
        DEFAULT_NARRATIVES_CONFIG,
    )
    from narranexus.platform.narrative.config import config

    longest = max(len(c.get("description") or "") for c in DEFAULT_NARRATIVES_CONFIG)
    assert longest <= config.DESCRIPTION_MAX_LENGTH, (
        f"a curated bucket description ({longest} chars) would be truncated"
    )


# ---------------- the guard against a fourth read site -----------------------


def test_every_raw_description_read_is_on_the_allow_list() -> None:
    """A bounded accessor only helps if new read sites cannot quietly bypass it.

    `prompt_builder` is on the list deliberately: it renders `description` into
    the AGENT's own context prompt, including the cacheable stable prefix.
    Applying the retirement rule there is a bigger behavioural surface than
    BM25 and needs its own decision, so it is EXEMPT-AND-DOCUMENTED rather than
    silently different. Any NEW entry must be classified before this passes.
    """
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[2] / "src" / "narranexus" / "platform" / "narrative"
    allowed = {
        # renders into the AGENT's own context prompt, including the cacheable
        # stable prefix. Applying retirement there is a bigger behavioural
        # surface than BM25 and needs its own decision — exempt AND documented,
        # not silently different. Tracked as an open item.
        "_narrative_impl/prompt_builder.py",
        # shows the CURRENT description to the update LLM as context, which is
        # the one place the raw value is the subject rather than evidence.
        "_narrative_impl/updater.py",
        # the DEFAULT BUCKET candidates handed to the judge. A bucket's
        # description is curated copy, not a creation-time fossil, and the
        # bucket's placeholder summary means the accessor would return it
        # anyway — so this reads raw to keep the judge prompt byte-stable.
        "_narrative_impl/retrieval.py",
        # human-facing markdown export, not a routing or prompt surface.
        "exporters.py",
    }
    pattern = re.compile(r"narrative_info\.description")
    scanned = list(root.rglob("*.py"))
    assert len(scanned) >= 20, (
        f"scan root {root} yielded only {len(scanned)} files — the narrative "
        "package moved again and this guard is scanning the wrong directory"
    )
    found = set()
    for f in scanned:
        for line in f.read_text(encoding="utf-8").splitlines():
            if line.lstrip().startswith("#"):
                continue
            if pattern.search(line):
                found.add(str(f.relative_to(root)))
                break
    assert found <= allowed, (
        f"unclassified raw `description` read site(s): {sorted(found - allowed)} — "
        f"use Narrative.description_if_unsummarised() or add it to the allow-list "
        f"with a reason"
    )


# ---------------- the placeholder trap --------------------------------------
#
# `NarrativeCRUD.create` does NOT leave `current_summary` empty — it writes
# `"Newly created Narrative: {title}"`. So a naive "summary is non-empty"
# condition retires the birth certificate at the instant of birth, and the
# self-healing branch the rule exists for (D-9: the async updater never
# succeeds, so no real summary is ever written) would never fire once.
#
# That is not a hypothetical: prod has 191 non-default threads with no summary
# at all, and the default buckets carry their own `"This is a default ..."`
# placeholder on 8,296 rows.


def test_the_creation_placeholder_is_not_a_medical_record() -> None:
    """A template summary means "the updater has not written one yet"."""
    n = _narrative(
        name="部署脚本报错",
        summary="Newly created Narrative: 部署脚本报错",
        description="Created based on query: 帮我排查部署脚本报错的原因",
    )
    assert n.description_if_unsummarised() == (
        "Created based on query: 帮我排查部署脚本报错的原因"
    ), "the birth certificate retired while the medical record was still blank"
    assert "帮我排查部署脚本报错的原因" in n.searchable_text()


def test_the_default_bucket_placeholder_is_not_a_medical_record_either() -> None:
    """Bucket descriptions are curated, not fossils — they must stay readable."""
    n = _narrative(
        name="GreetingAndCourtesy",
        summary="This is a default GreetingAndCourtesy Narrative",
        description="Pure greetings and courtesy with no durable topic.",
    )
    assert n.description_if_unsummarised().startswith("Pure greetings")


def test_the_placeholder_prefix_has_exactly_one_definition() -> None:
    """crud writes it, the accessor recognises it — two literals would rot.

    This coupling is real, so it gets one home. If someone edits the string in
    `crud.create` and not here, the retirement rule silently starts firing at
    birth again and nothing else fails.
    """
    import inspect

    from narranexus.platform.narrative import models
    from narranexus.platform.narrative._narrative_impl import crud as crud_mod

    assert hasattr(models, "PROVISIONAL_SUMMARY_PREFIXES")
    src = inspect.getsource(crud_mod.NarrativeCRUD.create)
    assert 'f"Newly created Narrative:' not in src, (
        "crud.create still hard-codes the placeholder instead of importing it"
    )
    assert "PROVISIONAL_SUMMARY_PREFIXES" in src


@pytest.mark.asyncio
async def test_a_freshly_created_narrative_can_still_be_found_by_its_query(db_client) -> None:
    """End to end: creation -> the thread is retrievable before any updater run.

    This is the regression the placeholder trap would have caused: a brand-new
    thread whose only self-description is its creation query would score zero
    against that very query.
    """
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from narranexus.platform.narrative._narrative_impl.crud import NarrativeCRUD
    from narranexus.platform.narrative._narrative_impl.retrieval import NarrativeRetrieval

    crud = NarrativeCRUD("agent_x")
    crud.set_database_client(db_client)
    created = await crud.create(
        agent_id="agent_x", user_id="u", title="部署脚本报错排查",
        description="Created based on query: 帮我排查一下灰度环境部署脚本报错",
        save_to_db=False,
    )

    pool = [(created.id, created.searchable_text(), False),
            ("nar_other", "纽约餐厅推荐 用户询问意大利餐厅", False)]
    ranked = NarrativeRetrieval.rank_pool("灰度环境部署脚本报错怎么排查", pool, 3)
    assert ranked and ranked[0].narrative_id == created.id, (
        "a new thread is invisible to the query that created it"
    )

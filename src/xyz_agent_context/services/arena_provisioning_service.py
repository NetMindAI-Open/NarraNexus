"""
@file_name: arena_provisioning_service.py
@author: Bin Liang
@date: 2026-06-15
@description: Idempotent server-side provisioning of a NetMind Agent Arena
              competitor agent. One call turns a user into a ready-to-play
              agent: register on Arena, create the local agent (named with the
              Arena gamertag), default instances, Arena awareness persona, the
              installed `arena` skill, three PAUSED routines, and an Arena-
              flavored Bootstrap.md + first-turn greeting.

Idempotency keys on the `agents` table: the user's agent tagged
`agent_metadata.provisioned_source == "arena"`. A repeat call returns that agent
fast, never double-registers. There is no separate credentials table — Arena is
the external source of truth for the identity, and the api_key lives only in the
agent workspace (the backend never calls Arena itself).

Design: reference/self_notebook/specs/2026-06-15-arena-onboarding-design.md (§11).
Honors 铁律 #4 (scenario logic lives in awareness), #14 (paused = consent gate,
not a time ceiling), #8 (sweep adjacent: agent creation funnels through
InstanceFactory here).
"""

from __future__ import annotations

import os
from pathlib import Path
from time import perf_counter
from typing import Any, Dict
from uuid import uuid4

from loguru import logger

from xyz_agent_context.settings import settings
from xyz_agent_context.utils.arena_onboarding import ArenaOnboarder, arena_auth_directive
from xyz_agent_context.bootstrap.profiles import (
    BootstrapProfile,
    BootstrapContext,
    WelcomeArtifact,
    apply_bootstrap,
    get_profile,
    register_profile,
)
from xyz_agent_context.bootstrap.welcome_templates import bilingual_html, feature_card

# ── Per-agent content (铁律 #4: the Arena scenario lives in these templates,
# not in any generic module/prompt) ─────────────────────────────────────────

ARENA_AWARENESS = """\
You are {gamertag}, a competitor on the NetMind Agent Arena (arena42.ai).

IDENTITY
- Arena name: {gamertag}   Arena id: {arena_agent_id}
- You are ALREADY registered. Never register again.

{auth_directive}

MISSION
- Enter competitions and play them well: debate, forum, prediction, art, and
  the other Arena game types. You compete on your creator's behalf — confirm
  before spending credits or entering paid / ticket competitions.

HOW YOU OPERATE
- The full API and per-game rules live in skills/arena/SKILL.md — read it for
  endpoints, but authenticate per the rule above (env key, no CLI). Per-game
  rules: arena42.ai/games/{{type}}.md.
- Loop: discover joinable competitions -> join -> poll game-state -> submit the
  right action each round.

YOUR STAGED ROUTINES
- You have pre-created background jobs in PAUSED state (heartbeat, competition
  scan, inbox check, dashboard refresh). They do nothing until you activate them.
  When your creator wants you to start, review them with the job tools and switch
  on the ones you want (job_update status="active").

YOUR DASHBOARD
- You have a pinned "dashboard" artifact ("{gamertag} · Arena Guide") — the first
  thing your creator sees. Treat it as a LIVING dashboard, not a static intro:
  keep it current. Whenever your Arena state changes (after a competition, when
  credits / standings / open competitions move), regenerate the dashboard HTML
  (visual + bilingual: EN default, 中文 toggle) and re-register it over the same
  artifact via register_artifact (target the existing artifact_id). A paused
  daily 08:00 job will also refresh it once you activate it.

TEMPERAMENT
- Think before you move; talk the move through with your creator; spend credits
  deliberately.
"""

ARENA_GREETING = (
    "Hey — I'm {gamertag}, your Arena competitor. I'm registered and ready to "
    "play. Want me to scan for a competition to jump into, or should I walk you "
    "through how I work first?"
)

ARENA_BOOTSTRAP_MD = """\
# Bootstrap — You're {gamertag}, an Arena competitor

This is your first conversation with your creator. You're already set up:
registered on the NetMind Agent Arena, your `arena` skill and API key are
configured, and three routines (heartbeat / competition scan / inbox) are
sitting PAUSED — they do nothing until you switch them on.

Your opening greeting was already shown. Don't repeat it; pick up naturally.

## Cover these in this first chat (talk, don't interrogate):
1. How should you address them? Ask what they'd like to be called.
2. Your name: you're {gamertag} by default — say so, and offer to rename
   yourself if they'd prefer something else (you can change it: Arena-side via
   `PATCH /agents/me` from the arena skill, and your local name too).
3. Ask whether they'd like to start their Arena journey now.
4. Optional setup vs. play now — some Arena bindings aren't done yet and are
   entirely OPTIONAL. Mention them and ask if they want any:
     - owner email  (account recovery / ownership)
     - Twitter verify  (+800 credits)
     - wallet  (only for paid / ticket competitions)
   Be clear: none are required — they can jump straight into a free competition
   right now if they'd rather.
5. If they want to start: switch on the routines they pick (your paused jobs),
   and/or go find a joinable competition.

## When you're done
Delete this file — you're set up now. (It also auto-clears after a few turns.)
"""

# Three routines, pre-created PAUSED. interval in seconds; timezone UTC (display
# only — the agent surfaces results in chat). The agent flips these to active.
ARENA_JOBS = (
    {
        "title": "Arena heartbeat",
        "description": "Periodic Arena heartbeat: status, credits, pending actions.",
        "interval_seconds": 7200,  # every 2h (Arena's recommended cadence)
        "payload": (
            "Run your Arena heartbeat. Read skills/arena/SKILL.md (heartbeat "
            "section). Call GET /agents/me to check status and credits, and "
            "check any competitions you have joined for pending actions — handle "
            "them. Surface anything noteworthy (opportunities, results) to your "
            "creator; stay quiet if nothing needs attention."
        ),
    },
    {
        "title": "Arena competition scan",
        "description": "Daily scan for joinable Arena competitions that fit you.",
        "interval_seconds": 86400,  # daily
        "payload": (
            "Scan Arena for joinable competitions that fit your strengths "
            "(GET /api/competitions?joinable=true). Pick the 1-3 best and tell "
            "your creator about them, asking if they'd like you to enter. Do not "
            "join without confirmation."
        ),
    },
    {
        "title": "Arena inbox check",
        "description": "Daily check of the Arena inbox (DMs, follows, messages).",
        "interval_seconds": 86400,  # daily
        "payload": (
            "Check your Arena inbox (GET /api/v1/agents/me/inbox?status=unread). "
            "Relay anything important (DMs, follow events, competition messages) "
            "to your creator and mark them read."
        ),
    },
    {
        "title": "Arena dashboard refresh",
        "description": "Daily 08:00 refresh of your Arena dashboard artifact.",
        "cron": "0 8 * * *",  # 08:00 in the creator's timezone
        "payload": (
            "Refresh your Arena dashboard artifact. Fetch your current state "
            "(GET /api/v1/agents/me for credits + profile; recent competitions, "
            "standings and results via the Arena API), then regenerate the "
            "dashboard HTML — keep it visual and bilingual (EN default + 中文 "
            "toggle) — and re-register it OVER your existing dashboard artifact "
            "(register_artifact with the same target artifact_id; find it among "
            "your pinned artifacts). Message your creator a one-line summary only "
            "if something notable changed."
        ),
    },
)


def _arena_welcome_en(g: str) -> str:
    return f"""
<div class="hero">
  <div class="dots"><i></i><i></i><i></i><i></i><i></i><i></i></div>
  <p class="kicker">NetMind Agent Arena</p>
  <h1>{g}</h1>
  <p class="lead">Your Arena competitor — registered, configured, and ready to play,
  with 200 credits in the bank.</p>
</div>

<p class="label">Your dashboard</p>
<div class="grid">
  {feature_card("c-rose", "social", "Control room", f"This chat is where you direct {g}. It already holds its Arena identity and key.")}
  {feature_card("c-sky", "jobs", "Four routines, paused", "Heartbeat, competition scan, inbox, and a daily dashboard refresh — all off until you switch them on.")}
  {feature_card("c-indigo", "memory", "Playbook loaded", f"The full Arena skill is installed; {g} knows the rules and the API.")}
  {feature_card("c-violet", "artifact", "A living card", f"This page is a dashboard, not a one-off intro — {g} keeps it current as it plays.")}
</div>

<p class="label">How the Arena works</p>
<div class="grid">
  {feature_card("c-emerald", "team", "Competitions", "Debate, prediction, art, forum and more — pick one and play to win.")}
  {feature_card("c-amber", "coin", "Credits", "You start with 200. Free competitions cost nothing; paid ones spend credits.")}
</div>
<div class="callout"><p>Nothing runs on its own. <b>{g}</b> competes only when you say so —
and confirms before spending any credits.</p></div>

<p class="label">Try saying</p>
<div class="prompt"><span class="q">"What can you do, and who are you on the Arena?"</span></div>
<div class="prompt"><span class="q">"Scan for a competition that suits you, and walk me through it."</span></div>
<div class="prompt"><span class="q">"Turn on your daily routines and keep this dashboard fresh."</span></div>

<p class="label">Start competing</p>
<ol class="steps">
  <li>Say hi — ask {g} what it can do.</li>
  <li>Have it scan for a competition to join.</li>
  <li>Pick one together, and let it play.</li>
</ol>

<p class="foot">Welcome to the Arena — {g} is warmed up and waiting.<br>
Official site: <a href="https://arena42.ai" target="_blank" rel="noopener">arena42.ai</a></p>
"""


def _arena_welcome_zh(g: str) -> str:
    return f"""
<div class="hero">
  <div class="dots"><i></i><i></i><i></i><i></i><i></i><i></i></div>
  <p class="kicker">NetMind Agent Arena</p>
  <h1>{g}</h1>
  <p class="lead">你的 Arena 选手 —— 已注册、已配置、随时可上场，账上还有 200 积分。</p>
</div>

<p class="label">你的控制台</p>
<div class="grid">
  {feature_card("c-rose", "social", "指挥室", f"这个对话就是你指挥 {g} 的地方。它已握有自己的 Arena 身份和 key。")}
  {feature_card("c-sky", "jobs", "四个例程，已暂停", "心跳、赛事扫描、收件箱，以及每日刷新 dashboard —— 全部关着，你打开才生效。")}
  {feature_card("c-indigo", "memory", "玩法已装载", f"完整的 Arena skill 已安装；{g} 懂规则、懂 API。")}
  {feature_card("c-violet", "artifact", "会生长的卡片", f"这一页是 dashboard，不是一次性介绍 —— {g} 会随着比赛持续更新它。")}
</div>

<p class="label">Arena 怎么玩</p>
<div class="grid">
  {feature_card("c-emerald", "team", "比赛", "辩论、预测、艺术、论坛等等 —— 挑一个，奔着赢去打。")}
  {feature_card("c-amber", "coin", "积分", "初始 200 分。免费赛不花钱，付费赛才消耗积分。")}
</div>
<div class="callout"><p>没有任何东西会自作主张。<b>{g}</b> 只在你发话时才参赛，
花积分前一定先跟你确认。</p></div>

<p class="label">不妨这样说</p>
<div class="prompt"><span class="q">「你能做什么？在 Arena 上你是谁？」</span></div>
<div class="prompt"><span class="q">「扫一场适合你的比赛，给我讲讲。」</span></div>
<div class="prompt"><span class="q">「把你的每日例程打开，把这个 dashboard 保持更新。」</span></div>

<p class="label">开始参赛</p>
<ol class="steps">
  <li>打个招呼 —— 问问 {g} 能做什么。</li>
  <li>让它扫一场可加入的比赛。</li>
  <li>一起挑一场，让它上。</li>
</ol>

<p class="foot">欢迎来到 Arena —— {g} 已热身完毕，就等你了。<br>
官网：<a href="https://arena42.ai" target="_blank" rel="noopener">arena42.ai</a></p>
"""


class ArenaBootstrapProfile(BootstrapProfile):
    """First-run flow for a provisioned Arena competitor (gamertag-aware)."""

    name = "arena"
    auto_delete_after_events = 3

    def greeting(self, ctx: BootstrapContext) -> str:
        return ARENA_GREETING.format(gamertag=ctx.extra.get("gamertag", "your agent"))

    def bootstrap_md(self, ctx: BootstrapContext):
        return ARENA_BOOTSTRAP_MD.format(gamertag=ctx.extra.get("gamertag", "your agent"))

    def welcome_artifact(self, ctx: BootstrapContext):
        gamertag = ctx.extra.get("gamertag", "your agent")
        return WelcomeArtifact(
            title=f"{gamertag} · Arena Guide",
            html=bilingual_html(
                f"{gamertag} · Arena",
                _arena_welcome_en(gamertag),
                _arena_welcome_zh(gamertag),
            ),
        )


register_profile(ArenaBootstrapProfile())


class ArenaProvisioningService:
    """Idempotent Arena agent provisioning. Entry point: `provision(user_id)`."""

    def __init__(self, db_client) -> None:
        self.db = db_client

    async def provision(self, user_id: str) -> Dict[str, Any]:
        from xyz_agent_context.repository.agent_repository import AgentRepository

        t_start = perf_counter()
        agent_repo = AgentRepository(self.db)

        # Warm path: already provisioned → return fast. Idempotency keys on the
        # `agents` table — the user's agent tagged
        # agent_metadata.provisioned_source == "arena". There is no separate
        # credentials table: Arena is the external source of truth for the
        # identity, and the api_key lives only in the agent workspace (the
        # backend never calls Arena itself — the agent does, via $ARENA_API_KEY).
        for a in await agent_repo.find(filters={"created_by": user_id}):
            md = a.agent_metadata or {}
            if md.get("provisioned_source") == "arena":
                logger.info(f"[arena.provision] reuse existing agent {a.agent_id} for {user_id}")
                return {
                    "success": True,
                    "reused": True,
                    "status": "reused",
                    "agent_id": a.agent_id,
                    "arena_agent_id": md.get("arena_agent_id"),
                    "arena_name": md.get("arena_agent_name") or a.agent_name,
                    "timings_ms": {"total": round((perf_counter() - t_start) * 1000, 1)},
                }

        timings: Dict[str, float] = {}
        onboarder = ArenaOnboarder()
        try:
            # 1. Register on Arena — the random gamertag becomes the agent name.
            t = perf_counter()
            creds = onboarder.register(description=f"NarraNexus agent for {user_id}")
            timings["register"] = round((perf_counter() - t) * 1000, 1)
            gamertag = creds.agent_name

            # 2. Create the local agent named with the gamertag. The non-secret
            # Arena identity goes into agent_metadata (the idempotency marker +
            # what the warm path returns). The secret api_key never touches the
            # DB — it lives only in the workspace skill.
            t = perf_counter()
            agent_id = f"agent_{uuid4().hex[:12]}"
            await agent_repo.add_agent(
                agent_id=agent_id,
                agent_name=gamertag,
                created_by=user_id,
                agent_description="Your NetMind Agent Arena competitor",
                agent_type="chat",
                agent_metadata={
                    "provisioned_source": "arena",
                    "arena_agent_id": creds.agent_id,
                    "arena_agent_name": gamertag,
                },
            )
            timings["create_agent"] = round((perf_counter() - t) * 1000, 1)

            # 3. Default agent-level instances (idempotent).
            t = perf_counter()
            from xyz_agent_context.module._module_impl.instance_factory import InstanceFactory

            await InstanceFactory(self.db).create_agent_level_instances(agent_id)
            timings["instances"] = round((perf_counter() - t) * 1000, 1)

            # 4. Awareness persona (铁律 #4 home).
            t = perf_counter()
            await self._set_awareness(
                agent_id,
                ARENA_AWARENESS.format(
                    gamertag=gamertag,
                    arena_agent_id=creds.agent_id,
                    auth_directive=arena_auth_directive(gamertag),
                ),
            )
            timings["awareness"] = round((perf_counter() - t) * 1000, 1)

            # 5. Install the arena skill into the agent's workspace. The api_key
            # + claim_token are written here (credentials.json + skill env) — the
            # only place they live on our side.
            t = perf_counter()
            workspace = Path(settings.base_working_path) / f"{agent_id}_{user_id}"
            onboarder.install_skill(
                workspace / "skills", creds, owner_user_id=user_id
            )
            timings["install_skill"] = round((perf_counter() - t) * 1000, 1)

            # 6. First-run flow via the "arena" bootstrap profile: writes
            # Bootstrap.md + sets the greeting/profile/deletion-rule metadata.
            t = perf_counter()
            await apply_bootstrap(
                self.db,
                agent_id=agent_id,
                user_id=user_id,
                profile=get_profile("arena"),
                ctx=BootstrapContext(
                    agent_id=agent_id, user_id=user_id, agent_name=gamertag,
                    extra={"gamertag": gamertag, "arena_agent_id": creds.agent_id},
                ),
            )
            timings["bootstrap"] = round((perf_counter() - t) * 1000, 1)

            # 7. Four PAUSED routines (heartbeat / scan / inbox / dashboard).
            t = perf_counter()
            paused = await self._create_paused_jobs(agent_id, user_id)
            timings["paused_jobs"] = round((perf_counter() - t) * 1000, 1)

            timings["total"] = round((perf_counter() - t_start) * 1000, 1)
            logger.info(
                f"[arena.provision] provisioned {agent_id} ({gamertag}) "
                f"arena_id={creds.agent_id} jobs={paused} in {timings['total']}ms"
            )
            return {
                "success": True,
                "reused": False,
                "status": "provisioned",
                "agent_id": agent_id,
                "arena_agent_id": creds.agent_id,
                "arena_name": gamertag,
                "paused_jobs": paused,
                "timings_ms": timings,
            }
        finally:
            onboarder.close()

    # ── helpers ──────────────────────────────────────────────────────────────

    async def _set_awareness(self, agent_id: str, awareness_text: str) -> None:
        from xyz_agent_context.repository.instance_repository import InstanceRepository
        from xyz_agent_context.repository.instance_awareness_repository import (
            InstanceAwarenessRepository,
        )

        rows = await InstanceRepository(self.db).get_by_agent(
            agent_id, module_class="AwarenessModule", is_public=True
        )
        if not rows:
            raise RuntimeError(f"no AwarenessModule instance for {agent_id}")
        await InstanceAwarenessRepository(self.db).upsert(rows[0].instance_id, awareness_text)

    async def _create_paused_jobs(self, agent_id: str, user_id: str) -> list:
        """Create the routines, then pause each. create→PENDING→pause→PAUSED."""
        from xyz_agent_context.module.job_module.job_service import JobInstanceService
        from xyz_agent_context.repository.job_repository import JobRepository
        from xyz_agent_context.repository.user_repository import UserRepository

        # Schedules display/fire in the creator's timezone (the cron 08:00 job
        # especially); fall back to UTC if the user has no timezone set.
        user = await UserRepository(self.db).get_user(user_id)
        tz = (user.timezone if user and getattr(user, "timezone", None) else "UTC")

        job_service = JobInstanceService(self.db)
        job_repo = JobRepository(self.db)
        created = []
        for spec in ARENA_JOBS:
            try:
                if "cron" in spec:
                    trigger_config = {"cron": spec["cron"], "timezone": tz}
                else:
                    trigger_config = {"interval_seconds": spec["interval_seconds"], "timezone": tz}
                result = await job_service.create_job_with_instance(
                    agent_id=agent_id,
                    user_id=user_id,
                    title=spec["title"],
                    description=spec["description"],
                    job_type="scheduled",
                    trigger_config=trigger_config,
                    payload=spec["payload"],
                )
                if not result.get("success"):
                    logger.warning(f"[arena.provision] job '{spec['title']}' failed: {result}")
                    continue
                job_id = result["job_id"]
                # Pause it: a consent gate, not a ceiling (铁律 #14). The poller
                # only fires status IN (pending, active), so paused never runs
                # until the agent flips it via job_update(status="active").
                await job_repo.pause_job(job_id)
                created.append({"job_id": job_id, "title": spec["title"]})
            except Exception as e:  # noqa: BLE001 — one bad job must not abort provisioning
                logger.exception(f"[arena.provision] job '{spec['title']}' error: {e}")
        return created

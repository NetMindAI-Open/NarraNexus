"""
@file_name: profiles.py
@author: Bin Liang
@date: 2026-06-16
@description: Bootstrap as a pluggable PROFILE rather than one hard-coded set.

A BootstrapProfile owns the whole first-run experience for an agent and renders
it into per-agent state at creation time:

  - greeting(ctx)        -> the first assistant message (frontend + DB)
  - bootstrap_md(ctx)    -> the Bootstrap.md written into the workspace (or None
                            to mean "no bootstrap")
  - injection_prompt()   -> the system-prompt directive that points the agent at
                            Bootstrap.md (defaults to the generic one)
  - auto_delete_after_events -> rule-based deletion threshold (None = never
                            auto-delete; rely only on the agent deleting the doc
                            itself == "semantic deletion")

Why render-then-store (not resolve-at-runtime): the profile is a CREATE-TIME
concept. `apply_bootstrap()` renders everything into the workspace + agent
metadata, so the runtime (context_runtime deletion rule, chat_module greeting,
GET /agents greeting) just reads the stored result and never needs the profile
registry. This keeps the runtime decoupled and avoids registration-timing
problems, and makes the snapshot intentional.

Selection: agent creation takes a `bootstrap` parameter (profile name); default
is "default" (today's behavior). Arena registers + uses the "arena" profile.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

from loguru import logger

from xyz_agent_context.bootstrap.template import (
    BOOTSTRAP_GREETING,
    BOOTSTRAP_MD_TEMPLATE,
)
from xyz_agent_context.context_runtime.prompts import BOOTSTRAP_INJECTION_PROMPT

# Metadata keys the runtime reads (single place so they don't drift).
META_PROFILE = "bootstrap_profile"
META_GREETING = "bootstrap_greeting"
META_AUTO_DELETE = "bootstrap_auto_delete_after_events"

DEFAULT_AUTO_DELETE_AFTER_EVENTS = 3


@dataclass(frozen=True)
class BootstrapContext:
    """Inputs available when a profile renders itself for a specific agent."""

    agent_id: str
    user_id: str
    agent_name: Optional[str] = None
    # Scenario params (e.g. {"gamertag": ..., "arena_agent_id": ...}).
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class WelcomeArtifact:
    """A first-run welcome artifact a profile renders (pointer-model text/html)."""

    title: str
    html: str
    subdir: str = "welcome"
    filename: str = "index.html"
    kind: str = "text/html"


class BootstrapProfile:
    """
    Base profile. Subclass and override the render methods; or instantiate with
    static strings via `StaticBootstrapProfile`. `name` must be unique.
    """

    name: str = "base"
    auto_delete_after_events: Optional[int] = DEFAULT_AUTO_DELETE_AFTER_EVENTS

    def greeting(self, ctx: BootstrapContext) -> str:
        raise NotImplementedError

    def bootstrap_md(self, ctx: BootstrapContext) -> Optional[str]:
        raise NotImplementedError

    def welcome_artifact(self, ctx: BootstrapContext) -> Optional[WelcomeArtifact]:
        """A pinned, agent-scoped welcome card shown on first run. None = skip."""
        return None

    def injection_prompt(self) -> str:
        # The "read Bootstrap.md first" directive is scenario-agnostic by
        # default; a profile may override if it wants a different framing.
        return BOOTSTRAP_INJECTION_PROMPT

    def should_auto_delete(self, event_count: int) -> bool:
        """Rule-based deletion. None threshold => never (semantic-only)."""
        return (
            self.auto_delete_after_events is not None
            and event_count >= self.auto_delete_after_events
        )


class DefaultBootstrapProfile(BootstrapProfile):
    """Today's behavior: blank-slate "who am I" first-run, auto-delete after 3."""

    name = "default"
    auto_delete_after_events = DEFAULT_AUTO_DELETE_AFTER_EVENTS

    def greeting(self, ctx: BootstrapContext) -> str:
        return BOOTSTRAP_GREETING

    def bootstrap_md(self, ctx: BootstrapContext) -> Optional[str]:
        return BOOTSTRAP_MD_TEMPLATE

    def welcome_artifact(self, ctx: BootstrapContext) -> Optional[WelcomeArtifact]:
        from xyz_agent_context.bootstrap.welcome_templates import default_welcome_html

        return WelcomeArtifact(title="Welcome to NarraNexus", html=default_welcome_html())


class NoBootstrapProfile(BootstrapProfile):
    """No first-run flow at all (no Bootstrap.md, no greeting)."""

    name = "none"
    auto_delete_after_events = None

    def greeting(self, ctx: BootstrapContext) -> Optional[str]:  # type: ignore[override]
        return ""

    def bootstrap_md(self, ctx: BootstrapContext) -> Optional[str]:
        return None


# ── Registry ────────────────────────────────────────────────────────────────

_REGISTRY: Dict[str, BootstrapProfile] = {}


def register_profile(profile: BootstrapProfile) -> None:
    _REGISTRY[profile.name] = profile


def get_profile(name: Optional[str]) -> BootstrapProfile:
    """Resolve a profile by name; unknown/None falls back to 'default'."""
    if name and name in _REGISTRY:
        return _REGISTRY[name]
    return _REGISTRY["default"]


register_profile(DefaultBootstrapProfile())
register_profile(NoBootstrapProfile())


# ── Apply (render → per-agent state) ─────────────────────────────────────────

async def apply_bootstrap(
    db,
    *,
    agent_id: str,
    user_id: str,
    profile: BootstrapProfile,
    ctx: Optional[BootstrapContext] = None,
) -> Dict[str, Any]:
    """
    Render `profile` for this agent and persist it:
      - write (or remove) `Bootstrap.md` in the agent workspace
      - merge bootstrap fields into agents.agent_metadata
        (profile name, rendered greeting, rule-based deletion threshold)

    Returns the metadata fields written (for logging/inspection).
    """
    from xyz_agent_context.settings import settings
    from xyz_agent_context.repository.agent_repository import AgentRepository

    ctx = ctx or BootstrapContext(agent_id=agent_id, user_id=user_id)

    # 1. workspace Bootstrap.md
    workspace = Path(settings.base_working_path) / f"{agent_id}_{user_id}"
    workspace.mkdir(parents=True, exist_ok=True)
    bs_file = workspace / "Bootstrap.md"
    md = profile.bootstrap_md(ctx)
    if md:
        bs_file.write_text(md, encoding="utf-8")
    elif bs_file.exists():
        bs_file.unlink()

    # 2. metadata merge
    repo = AgentRepository(db)
    agent = await repo.get_agent(agent_id)
    meta: Dict[str, Any] = dict((agent.agent_metadata if agent else None) or {})
    written = {
        META_PROFILE: profile.name,
        META_GREETING: profile.greeting(ctx) or "",
        META_AUTO_DELETE: profile.auto_delete_after_events,
    }
    meta.update(written)
    await repo.update_agent(agent_id, {"agent_metadata": meta})

    # 3. welcome artifact (best-effort — a failure must not block creation).
    welcome = profile.welcome_artifact(ctx)
    if welcome:
        try:
            await _create_welcome_artifact(db, agent_id, user_id, welcome, workspace)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[bootstrap] welcome artifact failed for {agent_id}: {e}")

    logger.info(
        f"[bootstrap] applied profile '{profile.name}' to {agent_id} "
        f"(md={'yes' if md else 'no'}, welcome={'yes' if welcome else 'no'}, "
        f"auto_delete={profile.auto_delete_after_events})"
    )
    return written


async def _create_welcome_artifact(
    db, agent_id: str, user_id: str, welcome: "WelcomeArtifact", workspace: Path
) -> None:
    """
    Write the welcome HTML into the workspace and register it as a pinned,
    agent-scoped artifact — reusing artifact_runner.register_artifact (the same
    path the LLM tool uses, so file_path/size_bytes are correct). Idempotent:
    skips if a pinned artifact with the same title already exists.
    """
    from xyz_agent_context.repository.artifact_repository import ArtifactRepository
    from xyz_agent_context.module.common_tools_module._common_tools_impl import (
        artifact_runner,
    )

    repo = ArtifactRepository(db)
    for existing in await repo.list_pinned(agent_id):
        if existing.title == welcome.title:
            return  # already created

    art_dir = workspace / welcome.subdir
    art_dir.mkdir(parents=True, exist_ok=True)
    entry = art_dir / welcome.filename
    entry.write_text(welcome.html, encoding="utf-8")

    await artifact_runner.register_artifact(
        repo=repo,
        agent_id=agent_id,
        user_id=user_id,
        session_id=None,  # agent-scoped → auto-pinned
        kind=welcome.kind,
        entry_path=str(entry),
        title=welcome.title,
        description=None,
        target_artifact_id=None,
    )


def auto_delete_threshold_from_meta(agent_metadata: Optional[Dict[str, Any]]) -> Optional[int]:
    """
    Read the rule-based deletion threshold the runtime should use.

    Backward compatible: agents created before profiles (no key) → the historical
    default of 3 events.
    """
    if not agent_metadata or META_AUTO_DELETE not in agent_metadata:
        return DEFAULT_AUTO_DELETE_AFTER_EVENTS
    return agent_metadata.get(META_AUTO_DELETE)

"""
@file_name: profile.py
@author: Bin Liang
@date: 2026-08-19
@description: The "onboarding" BootstrapProfile — first-run flow for the
auto-provisioned guide agent. Renders the bilingual greeting, the first-chat
playbook (Bootstrap.md) and the standard welcome artifact from the persona /
topic the provisioning step randomly picked and passed via ctx.extra
(persona_key / topic_index / is_local), mirroring how the Arena profile
receives its gamertag.
"""

from __future__ import annotations

from typing import Optional

from backend.onboarding.personas import (
    GUIDE_BOOTSTRAP_MD,
    TOPIC_OPENERS,
    Persona,
    TopicOpener,
    persona_by_key,
    render_greeting,
)
from narranexus.platform.bootstrap.profiles import (
    BootstrapContext,
    BootstrapProfile,
    WelcomeArtifact,
    register_profile,
)


def _resolve(ctx: BootstrapContext) -> tuple[Persona, TopicOpener, bool]:
    """Pull the provision-time random picks out of ctx.extra (with safe
    fallbacks so a bare ctx still renders something sensible)."""
    persona = persona_by_key(str(ctx.extra.get("persona_key", "")))
    try:
        topic = TOPIC_OPENERS[int(ctx.extra.get("topic_index", 0))]
    except (ValueError, IndexError):
        topic = TOPIC_OPENERS[0]
    is_local = bool(ctx.extra.get("is_local", False))
    return persona, topic, is_local


class OnboardingBootstrapProfile(BootstrapProfile):
    """First-run flow for the auto-provisioned onboarding guide agent."""

    name = "onboarding"
    auto_delete_after_events = 3

    def greeting(self, ctx: BootstrapContext) -> str:
        persona, topic, is_local = _resolve(ctx)
        return render_greeting(
            ctx.agent_name or "your guide", persona, topic, is_local=is_local
        )

    def bootstrap_md(self, ctx: BootstrapContext) -> Optional[str]:
        return GUIDE_BOOTSTRAP_MD.format(agent_name=ctx.agent_name or "the guide")

    def welcome_artifact(self, ctx: BootstrapContext) -> Optional[WelcomeArtifact]:
        from narranexus.platform.bootstrap.welcome_templates import default_welcome_html

        return WelcomeArtifact(
            title="Welcome to NarraNexus", html=default_welcome_html()
        )


register_profile(OnboardingBootstrapProfile())

from pathlib import Path

from narranexus.sdk import Contribution, SkillSpec

_SKILLS = Path(__file__).resolve().parent.parent / "skills"

SKILLS = (Contribution("__PLUGIN_PKG___skill", lambda: SkillSpec(_SKILLS / "__PLUGIN_PKG___skill")),)


def activate(ctx):
    pass

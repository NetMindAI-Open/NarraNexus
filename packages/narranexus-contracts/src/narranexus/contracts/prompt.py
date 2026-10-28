"""
@file_name: prompt.py
@author: Bin Liang
@date: 2026-09-07
@description: The prompt contracts (kind ``prompt``): a system prompt is a list of SECTIONS rendered by providers filling the many-slot ``prompt.sections``, joined by the ASSEMBLER bound to the one-slot ``prompt.assembler``. A distribution or ``narranexus.toml`` can reorder / drop sections and replace the assembler without touching the platform; a plugin can add a section with a character budget — except the ones a deployment mode declares REQUIRED, which fail the prompt rather than vanish.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, MutableMapping, Optional, Protocol, Sequence, runtime_checkable


@dataclass
class PromptContext:
    """What a section sees when it renders for one turn.

    ``runtime`` is the host's context-building object (the platform's
    ContextRuntime today) — sections that need its helpers (temporal block,
    narrative prompt, module instruction formatting) call it; a section that
    renders pure text ignores it. ``part_sizes`` and ``meta`` are the
    diagnostics the host logs after assembly; a section may add to them."""

    agent_id: str
    user_id: Optional[str]
    ctx_data: Any
    narrative_list: Sequence[Any]
    selected_events: Sequence[Any]
    module_instructions: Sequence[Any]
    db: Any
    runtime: Any
    deployment_mode: str = "local"
    part_sizes: MutableMapping[str, int] = field(default_factory=dict)
    meta: MutableMapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RenderedSection:
    """One rendered section as handed to the assembler (empty text = skipped)."""

    id: str
    owner: str
    order: int
    text: str
    # the provider's declared budget (0 = unbounded), carried so the assembler
    # can report an overrun without a separate budgets table
    budget_chars: int = 0

    @property
    def chars(self) -> int:
        return len(self.text)


@runtime_checkable
class PromptSectionProvider(Protocol):
    """Slot ``prompt.sections`` (many): renders one section of the system prompt.

    ``order`` sorts sections when no binding orders them (lower first);
    ``budget_chars`` is the section's declared maximum (0 = unbounded) — the
    assembler reports overruns, it does not truncate silently.

    ``required_in`` names the deployment modes in which this section is
    LOAD-BEARING: an empty render (or a raising one) in such a mode refuses the
    whole prompt instead of quietly shipping a turn without it. Empty (the
    default) means degradable — one section must not take the turn down, which
    is the right policy for a plugin's contribution and the wrong one for the
    cloud security preamble (2026-06-17 incident): "configurable off, silently"
    is how a security instruction disappears with one binding line and a single
    warning. Per MODE, not unconditional: ``SecuritySection`` legitimately
    renders nothing on a desktop build, so ``required_in=("cloud",)`` is what
    keeps every local turn working.
    """

    id: str
    order: int
    budget_chars: int
    required_in: tuple[str, ...]

    async def render(self, ctx: PromptContext) -> Optional[str]: ...


class RequiredSectionMissing(RuntimeError):
    """A section required in this deployment mode rendered empty or raised.

    Fail-closed: the prompt is NOT produced. Raised by the host's section loop,
    carrying the section id (and the underlying error when there was one) so
    the operator sees which contribution went missing rather than a subtly
    shorter prompt.
    """

    def __init__(self, section_id: str, deployment_mode: str, cause: BaseException | None = None) -> None:
        self.section_id = section_id
        self.deployment_mode = deployment_mode
        reason = f"raised {cause!r}" if cause is not None else "rendered empty"
        super().__init__(
            f"prompt section {section_id!r} is required in deployment mode {deployment_mode!r} but {reason}; "
            f"refusing to build a system prompt without it"
        )


def section_required(provider: Any, deployment_mode: str) -> bool:
    """Whether ``provider`` is load-bearing in ``deployment_mode``.

    Reads ``required_in`` defensively (``getattr``) so a provider written
    against the older Protocol — a third-party section that predates the field
    — is treated as degradable rather than crashing the loop.
    """
    return deployment_mode in tuple(getattr(provider, "required_in", ()) or ())


@runtime_checkable
class PromptAssembler(Protocol):
    """Slot ``prompt.assembler`` (one): joins rendered sections into the final system prompt."""

    async def assemble(self, sections: Sequence[RenderedSection], ctx: PromptContext) -> str: ...


def budget_report(sections: Sequence[RenderedSection], budgets: Mapping[str, int] | None = None) -> list[str]:
    """Sections whose rendered size exceeds their budget (id: chars > budget).

    The budget is the section's own ``budget_chars`` unless ``budgets``
    overrides it by id (a distribution may tighten a section).
    """
    out: list[str] = []
    for s in sections:
        limit = (budgets or {}).get(s.id) or s.budget_chars
        if limit and s.chars > limit:
            out.append(f"{s.id}: {s.chars} > {limit}")
    return out


__all__ = [
    "PromptAssembler",
    "PromptContext",
    "PromptSectionProvider",
    "RenderedSection",
    "RequiredSectionMissing",
    "budget_report",
    "section_required",
]

"""
@file_name: __init__.py
@author: NetMind.AI
@date: 2026-07-24
@description: Atomic LLM operations — single calls, no agent loop: the
protocol-keyed helper factory (helper_sdk) with its anthropic/cli/gemini
backends, failure classification (failure), and audio transcription
(transcription/).

One re-export: `prompt_probe_emit`. The helper backends that call it
(anthropic_helper, cli_helper) ship in the `builtin.llm_clients` wheel since
batch 6b, and were importing `llm._prompt_probe` — a private module of
another package. The probe itself stays private (it owns a file sink and a
sampling policy); what a helper needs is the one-line "record this prompt"
call, published here under a name that says which subsystem it belongs to.

Lazy (PEP 562) so importing this package for `helper_sdk` does not construct
the probe's sink.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ._prompt_probe import emit as prompt_probe_emit


def __getattr__(name: str):
    """Resolve `prompt_probe_emit` lazily (PEP 562)."""
    if name != "prompt_probe_emit":
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from ._prompt_probe import emit

    globals()["prompt_probe_emit"] = emit
    return emit


def __dir__() -> list[str]:
    return sorted({"prompt_probe_emit"} | set(globals()))


__all__ = ["prompt_probe_emit"]

"""
@file_name: call_tagging.py
@author: NetMind.AI
@date: 2026-08-27
@description: Tag a timer with the model the last helper LLM call actually
              used — the one place business code touches the adapter for it.

Why this exists (review 2026-08-27, I4): three call sites had grown the same
copy — function-level import of `adapters.openai_agents`, read the
contextvar, `if info: t.tag(**info)` — each one hardcoding a concrete
adapter path inside narrative business code. Binding rule #9 wants the
framework swappable; with N copies, a swap has to find all N, and a missed
one makes that timing's model tag silently empty, which on a timing chart is
indistinguishable from "did not run".

The contextvar can only be read AFTER the call returns: the model /
structured-output mode are resolved deep inside the SDK during the call
itself, so the tag helper must be invoked at the call site, after the await
— it cannot be folded into `timed()` itself, which opens before the call.
"""

from __future__ import annotations

from typing import Protocol


class _Taggable(Protocol):
    def tag(self, **kwargs) -> None: ...


def tag_last_llm_call(timer: _Taggable) -> None:
    """Tag `timer` with the last helper-LLM call's resolved info, if any.

    `timer` is the object yielded by `utils.logging.timed(...)` (anything
    with a ``tag(**kwargs)`` method). Reads the adapter's contextvar set by
    the call that just returned; a missing value tags nothing.
    """
    from ..adapters.openai_agents import get_last_llm_call_info

    info = get_last_llm_call_info()
    if info:
        timer.tag(**info)

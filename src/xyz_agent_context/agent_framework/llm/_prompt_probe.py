"""
@file_name: _prompt_probe.py
@author: NarraNexus
@date: 2026-07-30
@description: Off-by-default diagnostic: what a helper_llm call actually sends.

Why this exists
---------------
Helper calls were measured on 2026-07-29 at 18,288 input tokens each, ~6 per
turn, 100% full price — 2.4x the weighted input of the agent_loop they support.
Every proposal for fixing that (turn on prompt caching / slim the context /
make fewer calls) depends on one fact nobody had: **what is IN those 18K, and
how much of it is the same from call to call.**

Two findings make the obvious guess unsafe:

  * ``instance_decision`` passes an 85-CHARACTER constant as ``instructions``
    and puts the whole 18K in ``user_input``. Since the SDKs map instructions
    onto ``system`` and user_input onto the message, "add cache_control to the
    system block" would cache 85 characters.
  * The ~6 calls in a turn are ~6 DIFFERENT helpers with different prefixes, so
    "6 calls per turn" is not 6 chances to reuse one cache entry.

And a hard constraint on top: claude-haiku-4-5 reports
``prompt_cache_min_tokens = 4096``. A prefix shorter than that cannot be cached
at all, however it is arranged. So the question is not "is there repetition"
but "is there >=4096 tokens of BYTE-IDENTICAL leading text, repeated inside the
5-minute TTL" — which is measurable, and is what this module measures.

What it emits
-------------
One ``[HELPER-PROMPT]`` line per call, carrying no prompt content: lengths,
and SHA prefixes taken at increasing byte boundaries. Comparing two calls from
the same site, the largest boundary whose hash matches brackets the stable
prefix — e.g. 8k matches but 16k does not means the shared head is between
8K and 16K chars (~2K-4K tokens), i.e. below haiku's floor and not cacheable.

Set ``HELPER_PROMPT_DUMP_DIR`` as well to also write the exact payloads, which
turns the bracket into an exact longest-common-prefix. That writes conversation
content to disk, so it is a separate opt-in from the log line and belongs in a
scratch directory, never a shared or synced one.

Both switches default OFF. This runs on the hot path of every helper call;
the gate is checked before any hashing or frame walking happens.
"""

from __future__ import annotations

import hashlib
import os
import sys
import threading
from pathlib import Path

from loguru import logger

# Boundaries in CHARACTERS. Chosen around haiku's 4096-token floor, which lands
# near 16K chars at ~4 chars/token — the boundary that decides whether caching
# is even possible, with neighbours either side to bracket it.
_PREFIX_BOUNDS = (1_000, 2_000, 4_000, 8_000, 16_000, 32_000, 64_000)

# Frames belonging to the plumbing rather than to a real call site.
_INTERNAL_MODULE_MARKERS = (
    "/agent_framework/llm/",
    "/agent_framework/adapters/",
    "/utils/logging/",
)

_dump_seq = 0
# `_dump_seq += 1` is read-modify-write, and helper calls run concurrently — two
# callers could take the same number and one file would overwrite the other,
# losing exactly the payload someone turned this on to read. A lock rather than
# itertools.count purely for symmetry with the ordering guarantee below: the
# number is claimed and the name built under the same lock, so the filenames are
# a real call order and not just unique.
_dump_lock = threading.Lock()


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()[:8]


def _call_site() -> str:
    """Nearest frame outside the helper plumbing, as ``module:func:line``.

    Walks ``sys._getframe`` rather than ``inspect.stack()``: the latter builds
    full FrameInfo objects (source lookups included) for every level, which is
    an order of magnitude more expensive on a path that runs several times per
    turn. Bounded at 12 levels so a deep or unusual stack cannot turn this into
    a long walk.
    """
    try:
        frame = sys._getframe(1)
        for _ in range(12):
            if frame is None:
                break
            filename = frame.f_code.co_filename
            if not any(m in filename for m in _INTERNAL_MODULE_MARKERS):
                return (
                    f"{Path(filename).stem}:{frame.f_code.co_name}"
                    f":{frame.f_lineno}"
                )
            frame = frame.f_back
    except Exception:  # noqa: BLE001 — diagnostics never break the call
        pass
    return "unknown"


def _dump_dir() -> Path | None:
    raw = os.environ.get("HELPER_PROMPT_DUMP_DIR", "").strip()
    if not raw:
        return None
    try:
        d = Path(raw)
        d.mkdir(parents=True, exist_ok=True)
        return d
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[HELPER-PROMPT] dump dir {raw!r} unusable: {e}")
        return None


def emit(sdk: str, model: str, instructions: str, user_input: str) -> None:
    """Log the shape of one helper call. Never raises, never blocks."""
    try:
        from xyz_agent_context.settings import settings

        if not settings.helper_prompt_probe_enabled:
            return
    except Exception:  # noqa: BLE001 — an unreadable setting means "off"
        return

    try:
        instructions = instructions or ""
        user_input = user_input or ""
        site = _call_site()

        # Hash the LEADING slice at each boundary: a cache prefix matches from
        # byte 0, so a whole-payload hash cannot tell "shares a long head" from
        # "shares nothing". Boundaries past the end are skipped rather than
        # padded, so a short payload does not report spurious matches.
        marks = " ".join(
            f"{b // 1000}k:{_sha(user_input[:b])}"
            for b in _PREFIX_BOUNDS
            if len(user_input) >= b
        )
        logger.info(
            f"[HELPER-PROMPT] sdk={sdk} site={site} model={model} "
            f"ins={len(instructions)}c/{_sha(instructions)} "
            f"usr={len(user_input)}c all={_sha(user_input)} {marks}"
        )

        dump = _dump_dir()
        if dump is not None:
            # Sequence-prefixed so call order is recoverable from the filenames
            # alone; the site is in the name so per-site grouping needs no
            # parsing of the contents.
            safe_site = site.replace("/", "_").replace(":", "-")
            global _dump_seq
            with _dump_lock:
                _dump_seq += 1
                path = dump / f"{_dump_seq:05d}_{sdk}_{safe_site}.txt"
            path.write_text(
                f"=== SDK ===\n{sdk}\n"
                f"=== SITE ===\n{site}\n"
                f"=== MODEL ===\n{model}\n"
                f"=== INSTRUCTIONS ({len(instructions)} chars) ===\n{instructions}\n"
                f"=== USER_INPUT ({len(user_input)} chars) ===\n{user_input}\n",
                encoding="utf-8",
            )
    except Exception as e:  # noqa: BLE001 — diagnostics never break the call
        try:
            logger.warning(f"[HELPER-PROMPT] probe failed: {e}")
        except Exception:
            pass


__all__ = ["emit"]

"""
@file_name: model_pricing.py
@author: NarraNexus
@date: 2026-07-30
@description: Resolve per-token USD pricing for a model id, from a maintained
              upstream table rather than a hand-written dict.

Why this module exists
----------------------
``cost_tracker`` used to carry the price table inline::

    MODEL_PRICING = {
        "gpt-5.1-2025-11-13": {"input": 2.0,  "output": 8.0},
        "gemini-2.5-flash":   {"input": 0.15, "output": 0.60},
    }

Two entries, and on 2026-07-30 NEITHER of the models actually running was one
of them. Measured against the live ledger, every ``llm_function`` /
``llm_stream`` / ``embedding`` row had ``total_cost_usd = 0`` — 2254 helper
calls, 3.7M input tokens, all recorded as free. ``agent_loop`` was the only
call type showing money, and only because it bypasses this table entirely via
``sdk_cost_usd`` (the Claude SDK's own figure).

The hand-written table had also gone stale where it *did* have an entry:
``gemini-2.5-flash`` was listed at $0.15/$0.60 while the real price is
$0.30/$2.50. A two-row dict nobody updates is not a price table; it is a
decoration that makes cost look measured.

So the source of truth is now ``litellm.model_cost`` — ~2983 entries,
maintained upstream, already a dependency of this repo (see
``agent_framework/llm/litellm_client.py``), and it carries the prompt-cache
tiers (``cache_creation_input_token_cost`` /
``cache_read_input_token_cost``) that an Anthropic-shaped bill actually needs.

Known limitation: LIST price, not invoice price
-----------------------------------------------
What resolves is the published rate for the model id, and a model reached
through an aggregator (NetMind resells DeepSeek, MiniMax, …) is not billed at
the vendor's direct rate. Nothing in an id distinguishes the two —
``deepseek-ai/DeepSeek-V4-Flash`` looks exactly like ``anthropic/claude-...``,
and a bare ``gpt-5`` may itself be arriving through a reseller — so this is
recorded once, here, instead of being guessed at per id. Refusing to price the
ids that happen to LOOK like an aggregator's would not make the ledger safer,
only make its coverage arbitrary while leaving the same error on every other
row. ``_LOCAL_OVERRIDES`` takes a rate card when the real number is known.

Scope: OBSERVABILITY ONLY
-------------------------
Per the 2026-07-28 change, real money for the free tier is metered by the
LiteLLM gateway on the request path; this repo does not keep a second ledger.
A wrong or missing number here misleads a dashboard — it does not misbill
anyone. That is exactly why the failure mode below is "return None and say so
out loud" rather than "guess something plausible": a fabricated price looks
right and is therefore worse than a visible zero.

Design rules
------------
1. **Never raise.** Cost accounting is observability, not flow control (the
   rule ``warn_missing_usage`` already follows). Any litellm shape change,
   import failure or odd entry degrades to ``None``.
2. **Never invent a price.** Models the upstream table does not know stay
   unknown and get warned about, by name, once. ``_LOCAL_OVERRIDES`` exists
   for the operator to fill in from an invoice — not for us to estimate.
   The line this draws is "is there a published rate for this model id", NOT
   "is that rate what we are actually charged" — see the limitation below.
3. **Warn once per model, not once per call.** 2254 helper calls would mean
   2254 identical warnings; that trains people to filter the log.
4. **Lazy + memoised import.** ``import litellm`` costs ~1.54s. Paying that on
   any module that happens to import cost accounting would be a startup
   regression for a table most calls never miss.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any, Dict, Optional

from loguru import logger


@dataclass(frozen=True)
class ModelPrice:
    """USD per SINGLE token (not per million) — matches litellm's own unit.

    ``cache_write_per_token`` / ``cache_read_per_token`` are None for models
    with no prompt cache (or none that upstream records). Callers must treat
    None as "price the tokens at the normal input rate", not as "free": an
    un-priced cache read is still a real, billed read.
    """

    input_per_token: float
    output_per_token: float
    cache_write_per_token: Optional[float]
    cache_read_per_token: Optional[float]
    resolved_id: str
    source: str  # "override" | "litellm" | "litellm(alias)"


# Models the upstream table does not know — chiefly aggregator-specific ids
# (NetMind's "minimax/minimax-m2.5", "deepseek-ai/DeepSeek-V4-Flash", …) whose
# price is set by the aggregator's own rate card, not by the model vendor.
#
# DELIBERATELY EMPTY. Add an entry only from a rate card or an invoice, never
# from an estimate — see design rule 2. The warning emitted by ``price_for``
# names the exact model id to add, so the log tells you what is missing.
_LOCAL_OVERRIDES: Dict[str, ModelPrice] = {}


_lock = threading.Lock()
_litellm_table: Optional[Dict[str, Any]] = None
_litellm_loaded = False
_lower_index: Optional[Dict[str, str]] = None
_warned_unknown: set[str] = set()


def _load_litellm_table() -> Optional[Dict[str, Any]]:
    """Return ``litellm.model_cost``, loading it at most once per process."""
    global _litellm_table, _litellm_loaded
    if _litellm_loaded:
        return _litellm_table
    with _lock:
        if _litellm_loaded:
            return _litellm_table
        try:
            # Through the seam, never `import litellm` here. LitellmClient
            # declares itself this repo's single litellm import point (iron rule
            # #9: swapping litellm out must stay a one-file change), and its
            # model_cost_map() exists precisely because pricing callers used to
            # reach for the bare import — nexus_power did, and the 2026-07-29
            # review filled that hole. A second import point re-opens it and
            # also skips the seat's drop_params / suppress_debug_info quieting.
            from xyz_agent_context.agent_framework.llm.litellm_client import (
                LitellmClient,
            )

            table = LitellmClient.model_cost_map()
            _litellm_table = table if isinstance(table, dict) else None
            if _litellm_table is None:
                logger.warning(
                    "[pricing] litellm.model_cost is not a dict "
                    f"({type(table).__name__}) — pricing disabled"
                )
        except Exception as e:  # noqa: BLE001 — pricing must never break a call
            _litellm_table = None
            logger.warning(f"[pricing] could not load litellm price table: {e}")
        _litellm_loaded = True
        return _litellm_table


def _entry_to_price(model_id: str, entry: Any, source: str) -> Optional[ModelPrice]:
    """Convert one litellm entry, or None if it carries no usable input price.

    An entry with no ``input_cost_per_token`` is not a cheap model — it is an
    entry that does not describe token pricing at all (image, audio and
    per-request models live in the same table). Pricing those at 0 would
    silently report them as free.
    """
    if not isinstance(entry, dict):
        return None
    try:
        in_cost = entry.get("input_cost_per_token")
        out_cost = entry.get("output_cost_per_token")
        if in_cost is None and out_cost is None:
            return None
        return ModelPrice(
            input_per_token=float(in_cost or 0.0),
            output_per_token=float(out_cost or 0.0),
            cache_write_per_token=(
                float(entry["cache_creation_input_token_cost"])
                if entry.get("cache_creation_input_token_cost") is not None
                else None
            ),
            cache_read_per_token=(
                float(entry["cache_read_input_token_cost"])
                if entry.get("cache_read_input_token_cost") is not None
                else None
            ),
            resolved_id=model_id,
            source=source,
        )
    except Exception as e:  # noqa: BLE001 — a malformed entry is not fatal
        logger.warning(f"[pricing] malformed price entry for {model_id!r}: {e}")
        return None


def _normalised_alias(model_id: str) -> Optional[str]:
    """Concrete model id behind a CLI family alias ("haiku" → …), else None.

    Reuses ``model_catalog``'s curated alias map instead of keeping a second
    one here: that map is already guarded by
    ``test_alias_targets_are_registered_catalog_models`` and is updated when a
    family ships a new latest. A private copy would be the thing that goes
    stale. Users type these aliases into slot config themselves (iron rule
    #15 — the platform does not police model choice), so "haiku" reaching the
    ledger is normal input, not a bug to fix upstream.

    Imported lazily: model_catalog lives in agent_framework, which imports
    cost accounting, so a module-level import would close a cycle.
    """
    try:
        from xyz_agent_context.agent_framework.providers.model_catalog import (
            resolve_cli_alias,
        )

        # auth_type is irrelevant for pricing — we only want "what concrete
        # model does this alias mean", which is the api_key branch's answer.
        resolved = resolve_cli_alias(model_id, auth_type="api_key")
        return resolved if resolved != model_id else None
    except Exception as e:  # noqa: BLE001 — alias resolution is best-effort
        logger.debug(f"[pricing] alias resolution failed for {model_id!r}: {e}")
        return None


def _lower_key_index() -> Dict[str, str]:
    """lowercased id → the table's own key, built once.

    litellm's keys are inconsistently cased for the SAME id — the ledger's
    highest-volume model arrives as ``minimax/minimax-m2.5`` while the table
    spells it ``minimax/MiniMax-M2.5``. A case-sensitive miss there is not
    caution, it is a lost price: 1416 calls booked at $0 while the rate was
    published all along. An index rather than a scan because the miss path is
    the common one for aggregator ids, and the table holds ~2983 entries.
    """
    global _lower_index
    if _lower_index is not None:
        return _lower_index
    table = _load_litellm_table() or {}
    with _lock:
        if _lower_index is None:
            # First key wins on a collision: table order is upstream's, and a
            # differing-case duplicate would price the same model twice anyway.
            index: Dict[str, str] = {}
            for key in table:
                index.setdefault(key.lower(), key)
            _lower_index = index
    return _lower_index


def _table_lookup(model_id: str, source: str) -> Optional[ModelPrice]:
    """One id against the upstream table: exact, then case-insensitive.

    Case folding is a match on the SAME key, not a guess at a different model,
    which is why it is allowed here while the prefix-stripping below is not.
    """
    table = _load_litellm_table()
    if table is None:
        return None
    price = _entry_to_price(model_id, table.get(model_id), source)
    if price is not None:
        return price
    actual = _lower_key_index().get(model_id.lower())
    if actual and actual != model_id:
        return _entry_to_price(actual, table.get(actual), source)
    return None


def _route_candidates(model_id: str) -> list[str]:
    """Route-qualified forms of the same model, most specific first.

    ``anthropic/claude-sonnet-4-6`` and ``openrouter/x/y`` carry a ROUTE in
    front of the model id; the table keys some models with it and some without,
    so stripping one segment at a time recovers the match.

    Yes, this means ``deepseek-ai/DeepSeek-V4-Flash`` resolves to
    ``deepseek-v4-flash`` — the VENDOR's published rate for a model we may be
    buying through an aggregator. That is a real approximation, and it is
    stated as a module-level limitation rather than fought here, because there
    is no id shape that separates the two: nothing distinguishes
    ``deepseek-ai/DeepSeek-V4-Flash`` from ``anthropic/claude-haiku-4-6``, and
    a bare ``gpt-5`` may equally be arriving through a reseller. Refusing to
    strip prefixes would not protect the ledger, only make its coverage
    arbitrary. _LOCAL_OVERRIDES is the escape hatch for a route whose real rate
    is known to differ.
    """
    out: list[str] = []
    rest = model_id
    while "/" in rest:
        rest = rest.split("/", 1)[1]
        out.append(rest)
    return out


def price_for(model_id: str) -> Optional[ModelPrice]:
    """Price for ``model_id``, or None when nothing authoritative is known.

    Resolution order: local override → upstream table (exact, then
    case-insensitive) → the alias's concrete id → route-stripped forms.
    Returning None is a real answer ("we do not know what this costs"), not an
    error, and the caller is expected to record the tokens regardless — an
    unpriced call must still be visible as usage.

    This is the ONE price resolver in the repo. nexus_power carried a second
    one (``_price_row``) whose rules matched line for line but whose id
    handling did not, so the same model id could be priced on one ledger and
    unpriced on the other — see the 2026-08-03 mirror entry.
    """
    if not model_id:
        return None

    override = _LOCAL_OVERRIDES.get(model_id)
    if override is not None:
        return override

    price = _table_lookup(model_id, "litellm")
    if price is not None:
        return price

    alias_target = _normalised_alias(model_id)
    if alias_target:
        price = _table_lookup(alias_target, "litellm(alias)")
        if price is not None:
            return price

    # Route prefixes last: the id as given is always the better answer, so a
    # stripped form may only fill a gap, never override an exact hit.
    for candidate in _route_candidates(model_id):
        price = _table_lookup(candidate, "litellm(route)")
        if price is not None:
            return price

    _warn_unknown_once(model_id)
    return None


def _warn_unknown_once(model_id: str) -> None:
    """Say — once — that this model's spend is being recorded as $0.

    Loud on purpose. The two-entry table this module replaced failed silently
    for months at ``logger.debug``, which is why nobody noticed that every
    helper call was booked as free. A miss here means a real dashboard is
    under-reporting, and the message names the id to add to
    ``_LOCAL_OVERRIDES``.
    """
    if model_id in _warned_unknown:
        return
    with _lock:
        if model_id in _warned_unknown:
            return
        _warned_unknown.add(model_id)
    try:
        logger.warning(
            f"[pricing] no price known for model={model_id!r} — its tokens are "
            f"recorded but its cost is booked as $0. Add it to "
            f"_LOCAL_OVERRIDES in utils/model_pricing.py from the provider's "
            f"rate card (warned once per model per process)."
        )
    except Exception:  # noqa: BLE001 — logging must never break accounting
        pass


def warm_cache() -> None:
    """Load the table now, synchronously. Call from a THREAD, not the loop.

    The lazy load is correct — most processes never price anything — but its
    first trigger sits inside ``await record_cost(...)``, so on an async server
    it lands on the event loop and stalls ~1.5s of concurrent traffic. backend's
    lifespan calls this via ``asyncio.to_thread`` so the import is paid once,
    off the loop, before any request needs it. Idempotent and never raises: a
    failure just leaves the lazy path in place.
    """
    _load_litellm_table()
    _lower_key_index()


def reset_cache_for_tests() -> None:
    """Drop memoised table + warn-once state. Tests only."""
    global _litellm_table, _litellm_loaded, _lower_index
    with _lock:
        _litellm_table = None
        _litellm_loaded = False
        _lower_index = None
        _warned_unknown.clear()


__all__ = ["ModelPrice", "price_for", "warm_cache", "reset_cache_for_tests"]

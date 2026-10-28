"""
@file_name: test_contribution_symbol_naming.py
@author: Bin Liang
@date: 2026-09-07
@description: Every builtin manifest's ``provides`` refs live inside their own package and are named the way docs/API_POLICY.md §8 says, by slot arity.

Two invariants that used to be checked one plugin at a time (or not at all):

1. **A ref never leaves its package.** `builtin.turn`'s `turn.pipeline` pointed
   at `narranexus.platform.turn.pipeline:PIPELINE_CONTRIBUTION` — the one ref of
   95 outside `narranexus_plugins.`, which made the slot whose whole purpose is
   "replace the turn runtime" the one slot no third party could fill (they
   cannot add symbols to the platform). Four packages asserted this about
   themselves; this is the generalisation.
2. **The symbol name says the arity.** §8 exists so a template composes without
   renaming and a reader can tell what a symbol holds. `builtin.turn` shipped
   `RECALL` where the template scaffolds `RECALL_STRATEGIES`, and the nine
   `model.providers` modules were split between `CONTRIBUTION` and
   `CONTRIBUTIONS` on the same many-arity slot.

Revert either and this file goes red at the offending plugin.
"""
from __future__ import annotations

import re

import pytest

from narranexus.kernel.plugins.builtins import builtin_manifests, slot_tree_with_builtins

#: one-arity: ``CONTRIBUTION``, or ``<SEAT>_CONTRIBUTION`` when one module fills
#: several one-arity slots (a framework's own seats).
_ONE_RE = re.compile(r"^(?:[A-Z][A-Z0-9]*_)*CONTRIBUTION$")
#: many-arity: an UPPER_SNAKE plural — ``CONTRIBUTIONS``, ``ROUTES``,
#: ``RECALL_STRATEGIES``, ``PROFILES``, ``HOOKS``, … ``CHANNEL`` is the one
#: documented singular (exactly one descriptor per channel plugin).
_MANY_RE = re.compile(r"^[A-Z][A-Z0-9_]*S$|^CHANNEL$")


def _provides():
    tree = slot_tree_with_builtins()
    for manifest in builtin_manifests():
        for slot, refs in (manifest.provides or {}).items():
            for ref in ([refs] if isinstance(refs, str) else refs):
                yield manifest.id, slot, ref, tree.get(slot).arity


ROWS = list(_provides())


def test_there_are_provides_to_check():
    assert len(ROWS) > 50, "the manifests did not load — this file would pass vacuously"


@pytest.mark.parametrize("plugin_id,slot,ref,arity", ROWS, ids=[f"{p}:{s}" for p, s, _, _ in ROWS])
def test_every_provides_ref_stays_inside_narranexus_plugins(plugin_id, slot, ref, arity):
    assert ref.startswith("narranexus_plugins."), (
        f"{plugin_id} provides {slot} from {ref} — a builtin's implementation must live in its own "
        f"package, or the slot is one only the platform can fill"
    )


@pytest.mark.parametrize("plugin_id,slot,ref,arity", ROWS, ids=[f"{p}:{s}" for p, s, _, _ in ROWS])
def test_every_provides_symbol_follows_the_arity_naming(plugin_id, slot, ref, arity):
    symbol = ref.split(":", 1)[1]
    pattern = _ONE_RE if arity == "one" else _MANY_RE
    assert pattern.match(symbol), (
        f"{plugin_id} fills the {arity}-arity slot {slot} with {symbol!r}; API_POLICY §8 wants "
        f"{'CONTRIBUTION / <SEAT>_CONTRIBUTION' if arity == 'one' else 'an UPPER_SNAKE plural (CONTRIBUTIONS, ROUTES, <STAGE>_STRATEGIES, PROFILES, ...)'}"
    )

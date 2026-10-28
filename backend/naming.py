"""
@file_name: naming.py
@author: Bin Liang
@date: 2026-08-19
@description: Shared Nintendo-style three-group random agent-name generator.

Extracted from `backend/integrations/arena/arena_onboarding.py` so non-Arena
provisioning flows (the onboarding guide agent) can use the same word lists
without importing an external-platform integration. Lives at backend ROOT
as a neutral shared leaf (铁律 #21: its consumers are backend provisioning
flows — Arena and the guide agent — and neither may depend on the other's
feature package just to get a name). The Arena onboarder delegates to these
functions with its own seeded RNG and re-exports the lists on its historical
import path.

24 x 24 x 24 = 13,824 base combinations; a numeric suffix on collision extends
that to ~1.38M. Tokens are single words, [A-Za-z] only — Arena's naming rules
(name must match [A-Za-z0-9_]) constrain the shared lists, so any consumer of
these names is automatically Arena-safe.
"""

from __future__ import annotations

import random
from typing import Callable, Optional

GROUP_TEMPERAMENT = (
    "Brave", "Swift", "Clever", "Mighty", "Silent", "Fierce", "Bold", "Sly",
    "Stoic", "Nimble", "Savage", "Lucid", "Radiant", "Relentless", "Vivid",
    "Crafty", "Daring", "Steady", "Witty", "Zealous", "Cunning", "Gallant",
    "Keen", "Valiant",
)
GROUP_FORCE = (
    "Thunder", "Shadow", "Frost", "Blaze", "Storm", "Ember", "Echo", "Nova",
    "Quantum", "Tempest", "Cinder", "Glacier", "Mirage", "Comet", "Aurora",
    "Vortex", "Onyx", "Solar", "Lunar", "Plasma", "Granite", "Zephyr",
    "Titan", "Phantom",
)
GROUP_CREATURE = (
    "Falcon", "Tiger", "Dragon", "Wolf", "Phoenix", "Raven", "Panther",
    "Cobra", "Lynx", "Orca", "Griffin", "Viper", "Jaguar", "Heron", "Mantis",
    "Stag", "Kraken", "Bison", "Osprey", "Sable", "Fox", "Hawk", "Ronin",
    "Sphinx",
)
BASE_NAME_COMBINATIONS = len(GROUP_TEMPERAMENT) * len(GROUP_FORCE) * len(GROUP_CREATURE)


class NameExhausted(RuntimeError):
    """Raised when no free name could be found after all attempts."""


def generate_name(rng: Optional[random.Random] = None) -> str:
    """One random three-group name, e.g. 'Brave_Thunder_Falcon'."""
    r = rng if rng is not None else random
    return "_".join((
        r.choice(GROUP_TEMPERAMENT),
        r.choice(GROUP_FORCE),
        r.choice(GROUP_CREATURE),
    ))


def generate_unique_name(
    is_taken: Callable[[str], bool],
    *,
    rng: Optional[random.Random] = None,
    reroll_attempts: int = 8,
    suffix_attempts: int = 20,
) -> str:
    """
    Return a name for which `is_taken(name)` is False.

    1. Re-roll a fresh name up to `reroll_attempts` times.
    2. Then keep the last base name and append `_<NN>` (01..99) up to
       `suffix_attempts` times.

    `is_taken` is the collision oracle. When driving real registration (the
    Arena flow), pass an oracle whose success path captures the credentials so
    the very call that proves uniqueness is the one that registers.
    """
    r = rng or random.Random()
    last = generate_name(r)
    for _ in range(reroll_attempts):
        if not is_taken(last):
            return last
        last = generate_name(r)

    for _ in range(suffix_attempts):
        candidate = f"{last}_{r.randint(1, 99):02d}"
        if not is_taken(candidate):
            return candidate

    raise NameExhausted(
        f"No free name after {reroll_attempts} re-rolls + "
        f"{suffix_attempts} suffixed attempts (last base: {last!r})"
    )

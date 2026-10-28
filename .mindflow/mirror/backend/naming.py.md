---
code_file: backend/naming.py
last_verified: 2026-08-19
stub: false
---

# naming.py — shared three-group random agent-name generator

## Why it exists

The Nintendo-style gamertag generator (three 24-word groups → 13,824 base
combinations, `_NN` suffix on collision) was born inside
`backend/integrations/arena/arena_onboarding.py`. The onboarding guide-agent
provisioning needs the same generator, so the word lists and generation
logic moved to this neutral backend-ROOT leaf; Arena re-exports and
delegates. Backend-side per 铁律 #21 (its only consumers are backend
provisioning flows; nothing agent-side imports it), and at backend root —
deliberately NOT inside backend/onboarding/ — so the Arena integration never
depends on the guide-agent feature package, and a third random-name consumer
(team rooms, template instantiation) has a neutral home to import from.

## Upstream / Downstream

**Consumers:** `backend/integrations/arena/arena_onboarding.py` (re-exports the
lists on its historical import path; `ArenaOnboarder.generate_name` /
`generate_unique_name` delegate with `self._rng`), and
`backend/onboarding/provisioning.py` (guide-agent naming — no uniqueness
oracle needed there, agent names are not unique keys locally).
**Imports:** stdlib only. This module must stay a pure leaf (no DB, no
settings) so any backend consumer can import it for free.

## Design decisions

- **RNG injected, module-level functions.** The Arena class carried a seeded
  `self._rng` for deterministic tests; keeping the rng a parameter preserves
  that without binding the generator to any class. `generate_name(None)` uses
  the module-level `random` for casual callers.
- **`NameExhausted` here, `ArenaNameExhausted(NameExhausted)` in Arena.**
  Existing Arena callers keep catching their flavored type; the shared
  function raises the base and Arena re-raises the subclass.
- **Word tokens are `[A-Za-z]` single words** — Arena's `[A-Za-z0-9_]` naming
  rule constrains the shared lists, so any consumer's names are Arena-safe.

## Gotchas

- `generate_unique_name`'s `is_taken` oracle can double as the registration
  call itself (Arena's 201-claims-the-name pattern) — the uniqueness proof and
  the claim are then one call, no check-then-act race.

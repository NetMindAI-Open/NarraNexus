---
code_dir: backend/onboarding/
last_verified: 2026-08-19
stub: false
---

# backend/onboarding/ — the auto-provisioned guide agent

Domain subpackage (铁律 #23) for the "new user's first agent": when a user
logs in for the first time (or an existing zero-agent user logs in, behind
its own backfill env brake), the login hooks in `backend/routes/auth.py`
fire-and-forget `provisioning.ensure_guide_agent`, which creates a
randomly-named, randomly-personified companion agent that greets the user
immediately (static bilingual bootstrap greeting — zero LLM cost), teaches
them NarraNexus (via the `narranexus-guide` marketplace skill), and checks in
once a day through a SCHEDULED job the user can pause/cancel in the Jobs
panel — hard-stopped by the platform-enforced `trigger_config.end_at` horizon
(14 days, no model cooperation needed), with a model-judged
3-ignored-check-ins goodbye + self-pause on top.

Files:
- `personas.py` — content pools (personas, topic openers) + greeting /
  awareness / Bootstrap.md renderers. Pure data, no IO.
- `profile.py` — the "onboarding" BootstrapProfile (registered on import).
- `provisioning.py` — the orchestration seam the login hooks call.

Random names come from the neutral shared leaf `backend/naming.py` (also
consumed by the Arena integration — it deliberately does NOT live in this
package, so Arena never depends on this feature package).

Placement (铁律 #21 import-graph litmus): everything here is consumed ONLY by
backend login routes (and Arena's naming re-export), so it lives under
`backend/` — the same side as its twin, `backend/integrations/arena/`'s
provisioning service. The generic machinery it drives (`provision_new_agent`,
the profile registry) stays in `src/narranexus/platform/bootstrap/`, which IS
agent-side-consumed.

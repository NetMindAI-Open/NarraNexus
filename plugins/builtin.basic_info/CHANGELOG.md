# Changelog

## Unreleased

- `submit_feedback` gains an optional `dedup_key`: with it set, the tool files a
  report once per agent per key (TTL- and size-bounded, in-process) and silently
  drops repeats, so the Product Feedback Duty's machine-generated trigger
  (a rejected platform-injected credential) cannot flood the intake during a
  platform-wide outage. A report that never reached the intake releases its slot.
- `submit_feedback` now answers with what actually happened instead of a constant
  "Feedback recorded": a `notified` flag plus a message that says whether the
  agent may tell the user the team has been notified. Five outcomes are
  distinguished — delivered, duplicate of a confirmed delivery, a concurrent send
  still in flight, undelivered, and reporting disabled for the deployment — and
  only the first two permit the claim. It used to be asserted unconditionally by
  the prompt, which the agent had no way to verify. Still always `ok=True`.

## 1.0.0

- Packaged as an independent workspace member (plugin platform batch 6b).

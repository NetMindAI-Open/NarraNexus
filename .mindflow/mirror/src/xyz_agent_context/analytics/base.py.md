---
code_file: src/xyz_agent_context/analytics/base.py
last_verified: 2026-06-08
stub: false
---

# base.py

## Why it exists

Defines the `AnalyticsClient` Protocol — the vendor-agnostic seam between all
capture sites and any concrete analytics backend. Binding rule #9 demands we
are never one import away from breaking when a vendor is swapped; this Protocol
is that seam.

Without this file, every capture site would import PostHog (or whatever SDK is
current) directly. With it, all callers depend only on three method signatures
(`capture`, `identify`, `flush`) and are completely indifferent to the
implementation underneath.

## Upstream / downstream

- **Consumed by**:
  - `analytics/__init__.py` — re-exports `AnalyticsClient` and uses it as the
    type annotation for `get_analytics()`'s return value
  - Any type-annotated caller that wants to accept or store a sink reference
- **Implemented by**:
  - `analytics/_impl/posthog_sink.PostHogSink`
  - `analytics/_impl/null_sink.NullSink`
  - `analytics/_impl/fake_sink.FakeSink`

## Design decisions

**Python `Protocol` not ABC**: a structural (duck-typed) Protocol is chosen
over an abstract base class so that existing third-party or community sink
implementations can satisfy the interface without inheriting from us. This also
avoids a test-time dependency on the package that defines the ABC.

**All three methods are sync**: `capture` and `identify` are sync because every
real backend (PostHog, Segment, Mixpanel) batches on a background thread; the
caller's coroutine should not block waiting for a network write. `flush()` is
intentionally sync too — it is only called from `shutdown_analytics()` inside
the lifespan teardown, where a brief block is acceptable and an async flush
would complicate the shutdown sequence unnecessarily.

**Best-effort contract is a doc-level convention, not enforced by the type
system**: the Protocol does not wrap the three methods in try/except — that
responsibility belongs to each implementation. The docstring in `base.py`
explicitly states that implementors must swallow errors. This is intentional:
the Protocol stays minimal and the enforcement stays where it can be tested per
implementation.

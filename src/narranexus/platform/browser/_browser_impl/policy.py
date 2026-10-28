"""
@file_name: policy.py
@author:
@date: 2026-09-22
@description: Per-origin permission model for the in-app browser's arbitrary-script capability.

Ordinary HTTP(S) browsing has no permission policy: navigation, reading,
screenshots and fixed actions only check the URL scheme and who holds control.
The one decided capability is ``full_cdp_access`` — arbitrary page JavaScript
through ``browser_run``. It is the whole browser (it can send requests, submit
forms and navigate on the page's authority), so it is deny-by-default and only
an owner configures it, per origin, in Settings. There is no in-chat prompt
that grants it: a yes/no dialog would train users to click through the one
permission that has no ceiling.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Literal, Optional
from urllib.parse import urlparse

#: What a policy says about the capability at one origin.
Verdict = Literal["allow", "deny"]

#: The capability decided per origin.
Capability = Literal["full_cdp_access"]

_VERDICTS = ("allow", "deny")

#: Built-in fallback, used when neither the origin entry nor the default
#: policy says anything.
_BUILTIN: dict[str, Verdict] = {"full_cdp_access": "deny"}


@dataclass(frozen=True)
class OriginPolicy:
    """Per-origin verdicts. ``None`` means "inherit"."""

    full_cdp_access: Optional[Verdict] = None

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if v is not None}

    @staticmethod
    def from_dict(raw: dict) -> "OriginPolicy":
        """Build from config, ignoring unknown keys but validating known ones.

        Unknown keys are ignored so documents written by earlier builds (which
        also carried download / upload verdicts) still load. A known key with
        an invalid verdict is an error, never an implicit default.
        """
        known = set(OriginPolicy.__dataclass_fields__)
        kwargs = {}
        for key, value in (raw or {}).items():
            if key not in known:
                continue
            if value not in _VERDICTS:
                raise ValueError(f"invalid verdict {value!r} for {key}")
            kwargs[key] = value
        return OriginPolicy(**kwargs)


@dataclass(frozen=True)
class PolicyDecision:
    """The answer, plus enough context for an audit row."""

    verdict: Verdict
    capability: str
    origin: Optional[str]
    matched: str
    reason: str


@dataclass
class BrowserPolicy:
    """A whole agent's browser permissions."""

    default_origin_policy: OriginPolicy = field(default_factory=OriginPolicy)
    origins: dict[str, OriginPolicy] = field(default_factory=dict)

    def default_verdict(self, capability: str) -> Verdict:
        """Share configured and built-in defaults with the settings view."""
        return getattr(self.default_origin_policy, capability) or _BUILTIN[capability]

    def to_dict(self) -> dict:
        return {
            "default_origin_policy": self.default_origin_policy.to_dict(),
            "origins": {k: v.to_dict() for k, v in self.origins.items()},
        }

    @staticmethod
    def from_dict(raw: dict) -> "BrowserPolicy":
        """Load a stored document. Keys from earlier builds (session grants,
        approval receipts, history access) are ignored, not errors."""
        raw = raw or {}
        return BrowserPolicy(
            default_origin_policy=OriginPolicy.from_dict(raw.get("default_origin_policy") or {}),
            origins={
                k: entry for k, v in (raw.get("origins") or {}).items()
                if (entry := OriginPolicy.from_dict(v or {})).to_dict()
            },
        )

    def with_origin(self, origin: str, policy: OriginPolicy) -> "BrowserPolicy":
        """Return a copy with one origin replaced."""
        merged = dict(self.origins)
        merged[origin] = policy
        return replace(self, origins=merged)


def origin_of(url: str) -> Optional[str]:
    """``scheme://host[:port]`` for http(s) URLs, else None.

    Anything that is not http(s) never resolves to an origin a rule can name:
    ``file:`` turns a navigation into a local-file read and ``javascript:``
    into script injection.

    The default port is dropped so ``https://x`` and ``https://x:443`` are the
    same rule — otherwise a user who configured one would find the other
    still denied.
    """
    try:
        parsed = urlparse((url or "").strip())
        port = parsed.port
    except (ValueError, TypeError, AttributeError):
        return None
    scheme = (parsed.scheme or "").lower()
    if scheme not in ("http", "https"):
        return None
    host = (parsed.hostname or "").lower()
    if not host:
        return None
    if ":" in host:
        host = f"[{host}]"
    default_port = 443 if scheme == "https" else 80
    if port is None or port == default_port:
        return f"{scheme}://{host}"
    return f"{scheme}://{host}:{port}"


def _match_origin(policy: BrowserPolicy, origin: str) -> tuple[Optional[OriginPolicy], str]:
    """Exact entry, then wildcard host, then nothing. Exact always wins."""
    exact = policy.origins.get(origin)
    if exact is not None:
        return exact, origin

    scheme, _, host = origin.partition("://")
    for key, entry in policy.origins.items():
        key_scheme, _, key_host = key.partition("://")
        if key_scheme != scheme or not key_host.startswith("*."):
            continue
        suffix = key_host[1:]  # ".github.com"
        # Require a real label boundary: `evil-github.com` must not match, and
        # neither must the bare domain — `*.x` and `x` are different rules.
        if host.endswith(suffix) and len(host) > len(suffix):
            return entry, key
    return None, "default"


def decide(policy: BrowserPolicy, *, url: str, capability: Capability) -> PolicyDecision:
    """Decide the capability at one URL. Pure; unit-tested per branch.

    Resolution order: a non-http(s) scheme is denied before any lookup; then
    the origin entry (exact, then wildcard), the default policy, and finally
    the built-in fallback.
    """
    origin = origin_of(url)
    if origin is None:
        return PolicyDecision(
            verdict="deny", capability=capability, origin=None,
            matched="none", reason="unsupported-scheme",
        )

    entry, matched = _match_origin(policy, origin)
    configured: Optional[Verdict] = getattr(entry, capability, None) if entry else None
    if configured is None:
        configured = getattr(policy.default_origin_policy, capability, None)
    if configured is not None:
        return PolicyDecision(
            verdict=configured, capability=capability, origin=origin,
            matched=matched, reason="configured",
        )
    return PolicyDecision(
        verdict=_BUILTIN[capability], capability=capability, origin=origin,
        matched=matched, reason="builtin-default",
    )

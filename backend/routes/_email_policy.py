"""
@file_name: _email_policy.py
@author: Bin Liang
@date: 2026-10-28
@description: Which email addresses the self-serve signup routes refuse.

POST /api/auth/signup/send-code and POST /api/auth/signup are
unauthenticated by definition, and send-code spends our NetMind mail channel.
Throwaway mailboxes are the cheap way to mint many accounts, so both routes
refuse them before any rate-limit bucket is spent or any upstream call is
made. Login-time free-credit provisioning applies the same check (accounts
can also be created directly at NetMind, bypassing these routes).

The list is deliberately small and explicit — well-known disposable services
only, reviewable in a diff — rather than a vendored third-party list that
changes under us and false-positives real providers. Long-lived alias /
forwarding services (they front a real, durable inbox) are deliberately not
listed — they are not throwaway. Operators extend it
without a release via ``NARRANEXUS_BLOCKED_EMAIL_DOMAINS`` (comma-separated),
read per call so tests and operators see the current env (the parsed set is
cached per raw env value, so an unchanged env costs one dict lookup).

Matching is on whole DNS labels: a listed domain blocks itself and every
subdomain of it (``eu.mailinator.com``), never a look-alike
(``notmailinator.com``) or a name that merely CONTAINS it as a left label
(``mailinator.com.evil.org``).
"""
from __future__ import annotations

import os
from functools import lru_cache

BLOCKED_EMAIL_DOMAINS_ENV = "NARRANEXUS_BLOCKED_EMAIL_DOMAINS"

DISPOSABLE_EMAIL_DOMAINS: frozenset[str] = frozenset({
    "10minutemail.com",
    "10minutemail.net",
    "1secmail.com",
    "anonbox.net",
    "armyspy.com",
    "cc.cd",
    "crazymailing.com",
    "cuvox.de",
    "dayrep.com",
    "dispostable.com",
    "dropmail.me",
    "einrot.com",
    "emailfake.com",
    "emailondeck.com",
    "fakeinbox.com",
    "fakemail.net",
    "fleckens.hu",
    "generator.email",
    "getnada.com",
    "grr.la",
    "guerrillamail.biz",
    "guerrillamail.com",
    "guerrillamail.net",
    "guerrillamail.org",
    "guerrillamailblock.com",
    "gustr.com",
    "harakirimail.com",
    "inboxkitten.com",
    "jourrapide.com",
    "luxusmail.org",
    "mail-temp.com",
    "mail7.io",
    "mailcatch.com",
    "maildrop.cc",
    "mailinator.com",
    "mailnesia.com",
    "mailpoof.com",
    "mailsac.com",
    "mailtemp.net",
    "maxxspace.com",
    "mintemail.com",
    "moakt.com",
    "mohmal.com",
    "mytemp.email",
    "nimail.cn",
    "rhyta.com",
    "sharklasers.com",
    "superrito.com",
    "teleworm.us",
    "temp-mail.io",
    "temp-mail.org",
    "tempail.com",
    "tempinbox.com",
    "tempmail.net",
    "tempmail.plus",
    "tempmailo.com",
    "tempr.email",
    "throwawaymail.com",
    "tmpmail.org",
    "trashmail.com",
    "yopmail.com",
})


def _normalise_domain(raw: str) -> str:
    return raw.strip().lower().lstrip("@").strip(".")


@lru_cache(maxsize=4)
def _blocked_domains(raw_env: str) -> frozenset[str]:
    """The built-in list plus the env extension, cached per RAW env string.

    Keyed on the env value rather than cached argument-free, so the env is
    still read on every call and a changed value is seen immediately.
    """
    env = frozenset(
        d for d in (_normalise_domain(p) for p in raw_env.split(",")) if d
    )
    return DISPOSABLE_EMAIL_DOMAINS | env


def is_blocked_email(email: str) -> bool:
    """True when the address's domain, or any parent domain of it, is blocked.

    A string without ``@`` is never blocked here — address validity is the
    request model's job (``EmailStr``), not this policy's.
    """
    if "@" not in email:
        return False
    domain = _normalise_domain(email.rsplit("@", 1)[1])
    if not domain:
        return False
    blocked = _blocked_domains(os.getenv(BLOCKED_EMAIL_DOMAINS_ENV) or "")
    labels = domain.split(".")
    return any(".".join(labels[i:]) in blocked for i in range(len(labels)))


__all__ = ["BLOCKED_EMAIL_DOMAINS_ENV", "DISPOSABLE_EMAIL_DOMAINS", "is_blocked_email"]

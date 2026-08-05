"""
@file_name: netmind_register_client.py
@author: Bin Liang
@date: 2026-07-28
@description: NetMind's self-serve registration API (send code + create user).

Sibling of ``netmind_auth_client`` and deliberately shaped like it: same host
(``NETMIND_AUTH_API_URL`` — dev and prod differ, so it is NEVER hardcoded), same
form-urlencoded convention, same "envelope with success/msg" unwrapping.

Why the calls live here rather than in the browser: the existing login page does
talk to NetMind directly, but a signup form is the one place we most want a
server in the middle — it lets us rate-limit the code-send endpoint (an email
bombing vector against a third party's mail reputation as much as ours), map
upstream messages to something a user can act on, and keep the upstream host out
of the page's network tab.

SECRET DISCIPLINE (spec, "注意事项"): the password and the verification code
must never reach logs, metrics or error reports. Every log line here names the
email only, and the exception types carry upstream *messages*, never the request
body. Do not add a debug log of the payload — that is the whole reason this
client wraps the HTTP call instead of callers doing it inline.
"""
from __future__ import annotations

import os
import re
from typing import Optional

import httpx
from loguru import logger

_DEFAULT_TIMEOUT_S = 15.0

# Fixed by the upstream contract; see the API doc. Named so a future reader does
# not have to guess what a bare `1` or `2` meant.
_CODE_TYPE_REGISTER = 1
_CK_TYPE_REGISTER = 2

# 2 = subscribe to the newsletter, 1 = do not. We send 1: the signup form asks
# for three fields and a newsletter opt-in is not one of them, so subscribing
# would be consent the user never gave. Add a checkbox before changing this.
_SUBSCRIBE_NO = 1

# Password policy, straight from the spec. Enforced here as well as in the UI —
# client-side validation is a convenience, never a guarantee.
_MIN_LEN, _MAX_LEN = 8, 16
_SPECIAL_RE = re.compile(r"[^A-Za-z0-9]")


class RegistrationError(Exception):
    """Upstream refused the request for a reason the USER can act on.

    Carries the upstream message verbatim (e.g. "verification code error",
    "email already registered"); routes surface it as 400.
    """


class RegistrationUpstreamError(Exception):
    """NetMind is unreachable or answered something we cannot parse.

    Distinct from :class:`RegistrationError` so the route can return 502 and the
    UI can say "try again" instead of blaming the user's input.
    """


def password_policy_error(password: str) -> Optional[str]:
    """Return a human-readable reason the password is unacceptable, or None.

    Kept as a pure function so the route can reject before spending an upstream
    call, and so the rules live in exactly one place on the server side.
    """
    if not (_MIN_LEN <= len(password) <= _MAX_LEN):
        return f"Password must be {_MIN_LEN}-{_MAX_LEN} characters."
    if not any(c.isupper() for c in password):
        return "Password must contain an uppercase letter."
    if not any(c.islower() for c in password):
        return "Password must contain a lowercase letter."
    if not any(c.isdigit() for c in password):
        return "Password must contain a digit."
    if not _SPECIAL_RE.search(password):
        return "Password must contain a special character."
    return None


class NetmindRegisterClient:
    """The two calls a self-hosted signup page needs."""

    def __init__(
        self,
        base_url: Optional[str] = None,
        *,
        timeout_s: Optional[float] = None,
        transport: Optional[httpx.AsyncBaseTransport] = None,
    ) -> None:
        self.base_url = (
            base_url or os.environ.get("NETMIND_AUTH_API_URL", "")
        ).rstrip("/")
        self.timeout_s = timeout_s or float(
            os.environ.get("NETMIND_AUTH_TIMEOUT_SECONDS", _DEFAULT_TIMEOUT_S)
        )
        self._transport = transport

    async def send_code(self, email: str) -> None:
        """Ask NetMind to email a 6-digit registration code."""
        await self._post(
            "/register/sendCode",
            {"email": email, "type": _CODE_TYPE_REGISTER},
            email=email,
        )
        logger.info(f"[signup-funnel] verification code requested for {email}")

    async def register(self, email: str, password: str, verify_code: str) -> None:
        """Create the account. Raises on any refusal.

        Note what is NOT logged here.
        """
        await self._post(
            "/register/registerUser",
            {
                "email": email,
                "password": password,
                "verifyCode": verify_code,
                "ckType": _CK_TYPE_REGISTER,
                "subscribeFlag": _SUBSCRIBE_NO,
            },
            email=email,
        )
        logger.info(f"[signup-funnel] account created for {email}")

    # -- transport ---------------------------------------------------------

    async def _post(self, path: str, form: dict, *, email: str = "") -> dict:
        if not self.base_url and self._transport is None:
            raise RegistrationUpstreamError("NETMIND_AUTH_API_URL is not configured")
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout_s, transport=self._transport
            ) as client:
                resp = await client.post(
                    f"{self.base_url}{path}",
                    data=form,
                    headers={
                        "Content-Type": "application/x-www-form-urlencoded",
                        # Upstream localises its error strings off this header;
                        # we surface those strings to the user, so pin English
                        # rather than inherit whatever the server defaults to.
                        "netmind-language": "en",
                    },
                )
        except Exception as e:  # noqa: BLE001 — transport-level
            # `e` here can only be an httpx transport error, which never
            # contains the request body. Do not widen this to log `form`.
            raise RegistrationUpstreamError(f"netmind {path} unreachable: {e!r}") from e

        if resp.status_code >= 500:
            # A response body never contains the request form, so a short
            # snippet is safe — and it is the difference between "500" and
            # an actionable post-mortem (2026-08-01: signup 400x17 with no
            # record of what upstream actually said).
            snippet = resp.text[:200].replace("\n", " ")
            logger.warning(
                f"[signup-funnel] upstream 5xx path={path} status={resp.status_code} "
                f"email={email} body={snippet!r}"
            )
            raise RegistrationUpstreamError(f"netmind {path} -> {resp.status_code}")
        try:
            payload = resp.json()
        except Exception as e:  # noqa: BLE001
            snippet = resp.text[:200].replace("\n", " ")
            logger.warning(
                f"[signup-funnel] upstream non-JSON path={path} "
                f"status={resp.status_code} email={email} body={snippet!r}"
            )
            raise RegistrationUpstreamError(f"netmind {path} returned non-JSON") from e

        # NetMind answers 200 + {success:false, msg} for user-fixable refusals
        # (bad code, duplicate email), so status alone is not the verdict.
        if payload.get("success") is False or resp.status_code >= 400:
            msg = (payload.get("msg") or "").strip()
            # The one log line that buckets a 400 storm: upstream's own msg
            # (their error string — never the form, per the module discipline).
            logger.warning(
                f"[signup-funnel] upstream refusal path={path} "
                f"status={resp.status_code} email={email} msg={msg[:120]!r}"
            )
            raise RegistrationError(msg or "Registration failed. Please try again.")
        return payload.get("data") or {}

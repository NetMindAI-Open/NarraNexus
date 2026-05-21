"""
@file_name: invite_code_gen.py
@author: NarraNexus
@date: 2026-05-14
@description: Invite code generator — CSPRNG random short string.

Invite codes must be (a) unguessable — a sequential / enumerable code would
make the registration cap meaningless — and (b) human-typeable. So this is
deliberately NOT a snowflake / sequence / auto-increment generator: it draws
from a CSPRNG (`secrets`) over an alphabet with visually ambiguous characters
removed.

Uniqueness is NOT guaranteed here — the authoritative guarantee is the
`UNIQUE(code)` constraint on the `invite_codes` table. `InviteCodeRepository`
generates a code, attempts the insert, and retries on the (astronomically
rare) collision. See drafts/logs/invite_code_2026_05_14.md §5.

Format: ``NX-`` + 8 chars from a 30-char alphabet ≈ 30^8 ≈ 2^39 keyspace.
"""

import secrets

# Crockford-style alphabet with visually ambiguous characters removed:
# no 0/1 digits, no I/L/O/U letters.
INVITE_CODE_ALPHABET = "23456789ABCDEFGHJKMNPQRSTVWXYZ"
INVITE_CODE_PREFIX = "NX-"
INVITE_CODE_BODY_LEN = 8


def generate_code() -> str:
    """Return a fresh random invite code, e.g. ``NX-7K9MQ2WX``."""
    body = "".join(
        secrets.choice(INVITE_CODE_ALPHABET) for _ in range(INVITE_CODE_BODY_LEN)
    )
    return f"{INVITE_CODE_PREFIX}{body}"

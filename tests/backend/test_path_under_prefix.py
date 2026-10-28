"""
@file_name: test_path_under_prefix.py
@author: Bin Liang
@date: 2026-09-07
@description: backend.auth.path_under_prefix is segment matching, and every prefix SET in backend/auth.py goes through it.

String prefix matching is not path matching: `/api/quota` startswith-matches
`/api/quota-admin`, and that is exactly how a prefix list quietly grows an
extra member. The helper itself had no unit test, and three of the four prefix
sets in `backend/auth.py` still used bare `startswith` after the helper landed
(the "fix one instance, miss the class" shape). The set-membership test below
is the guard against that regressing.
"""
from __future__ import annotations

import pytest

from backend import auth as auth_mod
from backend.auth import path_under_prefix


@pytest.mark.parametrize(
    "path,prefix,expected",
    [
        ("/api/quota", "/api/quota", True),            # exact
        ("/api/quota/me", "/api/quota", True),         # true descendant
        ("/api/quota-admin", "/api/quota", False),     # sibling sharing the string prefix
        ("/api/quota-admin/x", "/api/quota", False),
        ("/api/quotas", "/api/quota", False),
        ("/api/quota/me", "/api/quota/", True),        # prefix written with a trailing slash
        ("/api/quota", "/api/quota/", True),
        ("/api", "/api/quota", False),                 # prefix longer than the path
        ("/api/x/p/webhook-admin", "/api/x/p/webhook", False),
        ("/api/x/p2/webhook", "/api/x/p", False),
    ],
)
def test_path_under_prefix_is_segment_matching(path, prefix, expected):
    assert path_under_prefix(path, prefix) is expected


@pytest.mark.parametrize(
    "name",
    ["AUTH_EXEMPT_PREFIXES", "QUOTA_BYPASS_PREFIXES", "MARKETPLACE_PUBLIC_READ_PREFIXES"],
)
def test_every_static_prefix_set_is_matched_with_the_helper(name):
    """The prefix sets are compared with `path_under_prefix`, not `startswith`.

    Read the module source rather than the behaviour, because the collision is
    latent today (verified: no shipped route sits on a sibling prefix) — the
    point is that the NEXT prefix added must not silently open its siblings.
    """
    import inspect

    source = inspect.getsource(auth_mod)
    for line in source.splitlines():
        if name in line and "startswith" in line:
            pytest.fail(f"{name} is matched with bare startswith — use path_under_prefix:\n    {line.strip()}")
    assert f"path_under_prefix(path, p) for p in {name}" in source or f"path_under_prefix(request.url.path, p) for p in {name}" in source or f"path_under_prefix(local_path, p) for p in {name}" in source, (
        f"{name} is not matched with path_under_prefix anywhere"
    )

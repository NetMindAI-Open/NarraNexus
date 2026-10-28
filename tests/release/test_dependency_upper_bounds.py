"""
@file_name: test_dependency_upper_bounds.py
@author: NarraNexus
@date: 2026-09-10
@description: Every third-party requirement this workspace declares must carry
              an upper bound, and uv.lock must sit inside it.

uv.lock is the primary protection — Dockerfile.manyfold, run.sh, deploy-cloud.sh
and (since 2026-09-10) build-desktop.sh all install through it. The bound is the
SECOND line of defence, for the moments the lock is not in play: a `uv lock`
refresh, a `uv add`, a new install path that forgets. An unbounded `>=` is an
open invitation for the next major release of an SDK to walk into the build.

The scan covers `[project.dependencies]`, every `[project.optional-dependencies]`
extra and every PEP 735 `[dependency-groups]` group, across the root pyproject
and all 30 workspace members. `[build-system].requires` is deliberately out of
scope: it is resolved by the build frontend in its own isolated env, not from
this lock.

That is not hypothetical here: on 2026-09-10 `mcp[cli]>=1.20.0` let mcp 2.2.0
into the desktop bundle, which had renamed `streamablehttp_client`, and every
service in the shipped dmg died at import. `regex` is exempt: it is CalVer, so
"the next major" carries no API meaning.

The second test is the one that catches a bound written CARELESSLY — a `<N`
below what the lock already holds resolves to nothing and would only surface on
the next `uv lock`.
"""
from __future__ import annotations

import functools
import re
import tomllib
from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType

import pytest
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
from packaging.version import Version

REPO = Path(__file__).resolve().parents[2]

# CalVer packages: a major bump is a date, not an API break.
UNBOUNDED_BY_DESIGN = frozenset({"regex"})


def _pyproject_files() -> list[Path]:
    return [
        REPO / "pyproject.toml",
        *sorted((REPO / "packages").glob("*/pyproject.toml")),
        *sorted((REPO / "plugins").glob("*/pyproject.toml")),
    ]


@functools.cache
def _declared_requirements() -> tuple[tuple[str, Requirement], ...]:
    """(repo-relative pyproject, requirement) for every THIRD-PARTY dependency.

    Workspace members (narranexus-*) are excluded: they are resolved from
    [tool.uv.sources], never from PyPI, so a version range on them means
    nothing.
    """
    out: list[tuple[str, Requirement]] = []
    for path in _pyproject_files():
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        project = data.get("project", {})
        specs = list(project.get("dependencies", []))
        for group in (project.get("optional-dependencies") or {}).values():
            specs.extend(group)
        # PEP 735 groups too — `[dependency-groups] dev` is what CI installs
        # (`uv sync --dev`), so an unbounded pytest/ruff/pyright there is the
        # same exposure, just aimed at CI instead of at a user's app. Entries
        # can be `{include-group = "..."}` dicts rather than strings; those
        # name another group in this same table, already visited by the loop.
        for group in (data.get("dependency-groups") or {}).values():
            specs.extend(entry for entry in group if isinstance(entry, str))
        for spec in specs:
            req = Requirement(spec)
            if canonicalize_name(req.name).startswith("narranexus"):
                continue
            out.append((str(path.relative_to(REPO)), req))
    # A tuple, because @functools.cache hands the same object to every caller
    # and a list would let one test mutate what the next one sees.
    return tuple(out)


@functools.cache
def _locked_versions() -> Mapping[str, frozenset[str]]:
    """Locked versions per package — a SET, not one version.

    A lock can carry the same package at two versions behind different markers.
    A dict comprehension would silently keep whichever came last and compare the
    bound against that one only; the bound has to hold for all of them.
    """
    text = (REPO / "uv.lock").read_text(encoding="utf-8")
    versions: dict[str, set[str]] = {}
    for name, version in re.findall(r'\nname = "([^"]+)"\nversion = "([^"]+)"', text):
        versions.setdefault(canonicalize_name(name), set()).add(version)
    # Read-only, for the same reason `_declared_requirements` returns a tuple:
    # @functools.cache hands every caller the same object.
    return MappingProxyType({k: frozenset(v) for k, v in versions.items()})


def test_every_third_party_requirement_has_an_upper_bound() -> None:
    unbounded = sorted(
        f"{rel}: {req}"
        for rel, req in _declared_requirements()
        if canonicalize_name(req.name) not in UNBOUNDED_BY_DESIGN
        and not any(s.operator in ("<", "<=", "==", "~=") for s in req.specifier)
    )
    assert not unbounded, (
        "these requirements have no upper bound — the next major release of each "
        "can walk into any resolution that is not lock-driven (2026-09-10: mcp 2.x "
        "in the dmg):\n  " + "\n  ".join(unbounded)
    )


@pytest.mark.parametrize(
    "rel,req",
    [pytest.param(rel, req, id=f"{rel}:{req.name}") for rel, req in _declared_requirements()],
)
def test_locked_version_satisfies_the_declared_range(rel: str, req: Requirement) -> None:
    locked = _locked_versions().get(canonicalize_name(req.name))
    # Not a skip: uv.lock carries every extra and every dependency group, so a
    # name that is missing is a name that is WRONG (a typo, or a package
    # dropped from the lock but not from pyproject). Skipping would hide it.
    assert locked, (
        f"{rel} declares `{req}` but no package named {req.name} is in uv.lock — "
        f"typo, or the lock was not regenerated after this line changed"
    )
    outside = sorted(v for v in locked if not req.specifier.contains(Version(v), prereleases=True))
    assert not outside, (
        f"{rel} declares `{req}` but uv.lock holds {req.name}=={', '.join(outside)} — "
        f"the bound and the lock disagree, so the next `uv lock` would move or fail"
    )

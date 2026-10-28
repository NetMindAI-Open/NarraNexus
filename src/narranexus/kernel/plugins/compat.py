"""
@file_name: compat.py
@author: Bin Liang
@date: 2026-09-03
@description: Semantic versions and ranges for manifests, dependencies and host compatibility.

A deliberately small grammar — the subset plugin authors actually write:

    exact        1.2.3
    comparators  >=1.2 <2   (space-separated, all must hold)
    caret        ^1.2.3     (>=1.2.3 <2.0.0; ^0.2.1 means >=0.2.1 <0.3.0)
    tilde        ~1.2.3     (>=1.2.3 <1.3.0)
    wildcard     *          (anything)

Pre-release tags (``1.2.0-beta.1``) order before the release. Build metadata
(``+abc``) is ignored for ordering. Nothing here depends on the ``packaging``
library so the kernel stays import-light.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from functools import total_ordering
from typing import Callable, Mapping

_VERSION_RE = re.compile(
    r"^(?P<major>0|[1-9]\d*)(?:\.(?P<minor>0|[1-9]\d*)(?:\.(?P<patch>0|[1-9]\d*))?)?"
    r"(?:-(?P<pre>[0-9A-Za-z.-]+))?(?:\+(?P<build>[0-9A-Za-z.-]+))?$"
)


@total_ordering
@dataclass(frozen=True)
class Version:
    major: int
    minor: int
    patch: int
    pre: tuple[object, ...] = ()

    @classmethod
    def parse(cls, text: str) -> "Version":
        m = _VERSION_RE.match(text.strip())
        if not m:
            raise ValueError(f"invalid semantic version {text!r}")
        pre_text = m.group("pre")
        pre: tuple[object, ...] = ()
        if pre_text:
            pre = tuple(int(p) if p.isdigit() else p for p in pre_text.split("."))
        return cls(int(m.group("major")), int(m.group("minor") or 0), int(m.group("patch") or 0), pre)

    def _key(self) -> tuple:
        # A release sorts after any pre-release of the same core version.
        pre_key: tuple = (1,) if not self.pre else (0, tuple((0, p) if isinstance(p, int) else (1, p) for p in self.pre))
        return (self.major, self.minor, self.patch, pre_key)

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, Version):
            return NotImplemented
        return self._key() < other._key()

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Version):
            return NotImplemented
        return self._key() == other._key()

    def __hash__(self) -> int:
        return hash(self._key())

    def __str__(self) -> str:
        core = f"{self.major}.{self.minor}.{self.patch}"
        return core + ("-" + ".".join(str(p) for p in self.pre) if self.pre else "")


_COMPARATOR_RE = re.compile(r"^(>=|<=|>|<|=|\^|~)?\s*(.+)$")

_OPS: dict[str, Callable[[Version, Version], bool]] = {
    ">=": lambda v, b: v >= b,
    "<=": lambda v, b: v <= b,
    ">": lambda v, b: v > b,
    "<": lambda v, b: v < b,
    "=": lambda v, b: v == b,
}


@dataclass(frozen=True)
class Range:
    """A version range; ``contains(version)`` is the only operation."""

    text: str
    _clauses: tuple[tuple[str, Version], ...]

    @classmethod
    def parse(cls, text: str) -> "Range":
        stripped = text.strip()
        if stripped in ("", "*"):
            return cls("*", ())
        clauses: list[tuple[str, Version]] = []
        for token in stripped.split():
            m = _COMPARATOR_RE.match(token)
            if not m:
                raise ValueError(f"invalid version range token {token!r} in {text!r}")
            op, ver_text = m.group(1) or "=", m.group(2)
            base = Version.parse(ver_text)
            if op == "^":
                if base.major > 0:
                    upper = Version(base.major + 1, 0, 0)
                elif base.minor > 0:
                    upper = Version(0, base.minor + 1, 0)
                else:
                    upper = Version(0, 0, base.patch + 1)
                clauses += [(">=", base), ("<", upper)]
            elif op == "~":
                clauses += [(">=", base), ("<", Version(base.major, base.minor + 1, 0))]
            else:
                clauses.append((op, base))
        return cls(stripped, tuple(clauses))

    def contains(self, version: Version | str) -> bool:
        v = Version.parse(version) if isinstance(version, str) else version
        return all(_OPS[op](v, bound) for op, bound in self._clauses)

    def __str__(self) -> str:
        return self.text


def host_version() -> str:
    """The running application's version (pyproject ``version`` via package metadata; ``0.0.0`` if unknown)."""
    from importlib.metadata import PackageNotFoundError, version

    for name in ("narranexus", "xyz-agent-context"):
        try:
            return version(name)
        except PackageNotFoundError:
            continue
    return "0.0.0"


def select_version(versions_json: Mapping[str, str], host: str) -> str | None:
    """Pick the newest plugin version whose ``minAppVersion`` the host satisfies (Obsidian ``versions.json``).

    ``versions_json`` maps plugin version → minimum app version. Returns
    ``None`` when no entry fits, so an old host installs an older release
    instead of refusing.
    """
    host_v = Version.parse(host)
    fitting = [Version.parse(pv) for pv, min_app in versions_json.items() if Version.parse(min_app) <= host_v]
    return str(max(fitting)) if fitting else None


def blocked_reason(blocked_json: Mapping[str, Mapping[str, str]], plugin_id: str, version: str) -> str | None:
    """``blocked_versions.json`` lookup: ``{id: {"below": "x.y.z", "reason": "..."}}`` blocks versions below ``below``."""
    entry = blocked_json.get(plugin_id)
    if not entry:
        return None
    below = entry.get("below")
    if below and Version.parse(version) < Version.parse(below):
        return entry.get("reason") or f"versions below {below} are blocked"
    return None


__all__ = ["Version", "Range", "blocked_reason", "host_version", "select_version"]

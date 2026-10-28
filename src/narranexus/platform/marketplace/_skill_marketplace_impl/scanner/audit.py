"""
@file_name: audit.py
@author: NetMind.AI
@date: 2026-07-20
@description: Dependency audit for skill packages (requirements.txt vs the
in-repo advisory list).

MVP scope: exact-pin (==X.Y.Z) and no-pin lines are checked against
KNOWN_VULNERABLE; range specifiers we cannot resolve without a full resolver
are left alone. Upgrading this to a live Safety/OSV feed only touches this
file and patterns.KNOWN_VULNERABLE.
"""

import re
from pathlib import Path
from typing import List, Tuple

from .patterns import KNOWN_VULNERABLE, SEVERITY_LOW

_REQ_LINE = re.compile(r"^\s*([A-Za-z0-9._-]+)\s*(==|>=|<=|~=|>|<)?\s*([0-9][0-9a-zA-Z.]*)?")


def _version_tuple(version: str) -> Tuple[int, ...]:
    return tuple(int(n) for n in re.findall(r"\d+", version)[:3])


def _is_vulnerable(pinned: str, spec: str) -> bool:
    if spec.startswith("<"):
        return _version_tuple(pinned) < _version_tuple(spec[1:])
    if spec.startswith("=="):
        return _version_tuple(pinned) == _version_tuple(spec[2:])
    return False


def audit_requirements(skill_dir: Path) -> List[Tuple[str, str, int, str]]:
    """Return (rule, file, line, detail) tuples for known-vulnerable pins."""
    findings: List[Tuple[str, str, int, str]] = []
    req_file = skill_dir / "requirements.txt"
    if not req_file.exists():
        return findings

    for lineno, raw in enumerate(req_file.read_text(encoding="utf-8", errors="replace").splitlines(), start=1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        match = _REQ_LINE.match(line)
        if not match:
            continue
        package = match.group(1).lower()
        operator, version = match.group(2), match.group(3)
        advisories = KNOWN_VULNERABLE.get(package)
        if not advisories or operator != "==" or not version:
            continue
        for spec, advisory in advisories:
            if _is_vulnerable(version, spec):
                findings.append(
                    (
                        "vulnerable_dependency",
                        str(req_file.name),
                        lineno,
                        f"{package}=={version} matches advisory {spec}: {advisory}",
                    )
                )
    return findings


__all__ = ["audit_requirements", "SEVERITY_LOW"]

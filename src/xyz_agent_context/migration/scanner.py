"""
@file_name: scanner.py
@author: NetMind.AI
@date: 2026-07-21
@description: Scanner orchestration for Agent Migration — detect + extract.

Public surface (the "Scanner CLI" capability, embedded, local-only):
- ``detect(home)``          -> list[FrameworkDetection]
- ``scan(path, framework)`` -> StandardizedAgentImport

Detect + extract only; never writes, never reads non-MCP secrets. Consumed by
the Migration Skill / Import Button which do the map+write via MCP tools / API.
See reference/self_notebook/specs/2026-07-21-agent-migration-tech-design.md.
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from xyz_agent_context.migration import detector, extractors
from xyz_agent_context.schema.migration_schema import (
    Framework,
    FrameworkDetection,
    MigrationSource,
    StandardizedAgentImport,
)


def detect(home: str | Path | None = None) -> List[FrameworkDetection]:
    """Probe the standard home locations for known frameworks."""
    return detector.detect_all(home)


def scan(
    path: str | Path | None = None,
    framework: Optional[Framework] = None,
) -> StandardizedAgentImport:
    """Scan one source into the standardized JSON.

    Args:
        path: source directory. If None, auto-detect the highest-confidence
            framework across the standard home locations and use its path.
        framework: force a framework and skip detection (still needs ``path``,
            or auto-detects the path for that framework).
    """
    if path is not None:
        det = detector.classify_path(path)
        if framework:
            det = FrameworkDetection(
                framework=framework, path=det.path,
                confidence=det.confidence, signals=det.signals,
            )
    else:
        candidates = detector.detect_all()
        if framework:
            candidates = [c for c in candidates if c.framework == framework]
        if not candidates:
            raise FileNotFoundError(
                "No known agent framework detected in the standard home "
                "locations; pass an explicit path."
            )
        order = {"high": 3, "medium": 2, "low": 1}
        det = max(candidates, key=lambda c: order[c.confidence])

    agent, skills, memory, mcp, custom, sessions = extractors.extract(
        det.framework, det.path
    )
    return StandardizedAgentImport(
        source=MigrationSource(
            framework=det.framework,
            detected_path=det.path,
            detection_confidence=det.confidence,
        ),
        agent=agent,
        skills=skills,
        memory=memory,
        mcp_servers=mcp,
        sessions=sessions,
        custom=custom,
    )

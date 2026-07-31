"""
Agent Migration — Scanner (detect + extract) for importing agents from
Claude Code / Hermes / OpenClaw / Codex into NarraNexus.

Embedded, local-only capability (disabled on cloud — no local filesystem).
See reference/self_notebook/specs/2026-07-21-agent-migration-tech-design.md.
"""

from xyz_agent_context.migration.scanner import detect, scan

__all__ = ["detect", "scan"]

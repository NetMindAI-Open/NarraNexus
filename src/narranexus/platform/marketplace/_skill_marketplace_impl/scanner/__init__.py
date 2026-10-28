"""
@file_name: __init__.py
@author: NetMind.AI
@date: 2026-07-20
@description: Skill security scanner — the framework-agnostic static gate.

Runs at publish time (cloud) and again before installing URL/GitHub-sourced
skills (both deployment modes). Pure Python AST + regex; no external services.
"""

from .static import ScanIssue, ScanReport, scan_skill_dir

__all__ = ["ScanIssue", "ScanReport", "scan_skill_dir"]

"""
@file_name: static.py
@author: NetMind.AI
@date: 2026-07-20
@description: Static-analysis engine for skill packages.

Walks a skill directory, regex-scans every text file with the TEXT_RULES
(HIGH severity) and AST-scans Python files with AST_CALL_RULES (LOW
severity). Aggregation: any HIGH issue -> rejected; any LOW -> warning;
none -> passed. Unparsable Python is itself a LOW finding and still gets
the regex pass, so a syntax error cannot be used to dodge the HIGH rules.
"""

import ast
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from loguru import logger

from .audit import audit_requirements
from .patterns import (
    AST_CALL_RULES,
    GLOB_RULE,
    MAX_SCAN_FILE_BYTES,
    SCANNABLE_SUFFIXES,
    SCANNER_VERSION,
    SEVERITY_HIGH,
    SEVERITY_LOW,
    TEXT_RULES,
    UNPARSABLE_RULE,
)


@dataclass
class ScanIssue:
    rule: str
    severity: str  # high | low
    file: str  # path relative to the skill root
    line: int
    detail: str

    def to_dict(self) -> dict:
        return {
            "rule": self.rule,
            "severity": self.severity,
            "file": self.file,
            "line": self.line,
            "detail": self.detail,
        }


@dataclass
class ScanReport:
    status: str  # passed | warning | rejected
    high_issues: int
    low_issues: int
    issues: List[ScanIssue] = field(default_factory=list)
    scanner_version: str = SCANNER_VERSION


def _read_text(path: Path) -> Optional[str]:
    """Return file content for scanning, or None for binary/oversized files."""
    try:
        if path.stat().st_size > MAX_SCAN_FILE_BYTES:
            return None
        blob = path.read_bytes()
    except OSError as exc:
        logger.warning(f"Scanner: cannot read {path}: {exc}")
        return None
    if b"\x00" in blob:
        return None
    if path.suffix and path.suffix.lower() not in SCANNABLE_SUFFIXES:
        return None
    return blob.decode("utf-8", errors="replace")


def _dotted_name(node: ast.AST) -> Optional[str]:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted_name(node.value)
        return f"{base}.{node.attr}" if base else None
    return None


def _match_call_rule(name: str) -> Optional[tuple]:
    exact = AST_CALL_RULES.get(name)
    if exact:
        return exact
    for prefix, rule in AST_CALL_RULES.items():
        if prefix.endswith(".") and name.startswith(prefix):
            return rule
    return None


def _scan_python_ast(source: str, rel_path: str, issues: List[ScanIssue]) -> None:
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        rule, description = UNPARSABLE_RULE
        issues.append(
            ScanIssue(rule, SEVERITY_LOW, rel_path, exc.lineno or 0, description)
        )
        return

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _dotted_name(node.func)
        if not name:
            continue

        if name == "glob.glob":
            for arg in node.args:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str) and "**" in arg.value:
                    rule, description = GLOB_RULE
                    issues.append(
                        ScanIssue(rule, SEVERITY_LOW, rel_path, node.lineno, description)
                    )
                    break
            continue

        matched = _match_call_rule(name)
        if matched:
            rule, description = matched
            issues.append(
                ScanIssue(rule, SEVERITY_LOW, rel_path, node.lineno, f"{description} ({name})")
            )


def _scan_text_rules(content: str, rel_path: str, issues: List[ScanIssue]) -> None:
    for lineno, line in enumerate(content.splitlines(), start=1):
        for rule in TEXT_RULES:
            if rule.pattern.search(line):
                issues.append(
                    ScanIssue(rule.rule, rule.severity, rel_path, lineno, rule.description)
                )


def scan_skill_dir(skill_dir: Path, include_dependency_audit: bool = True) -> ScanReport:
    """Scan an unpacked skill directory and aggregate a verdict."""
    skill_dir = Path(skill_dir)
    issues: List[ScanIssue] = []

    for path in sorted(skill_dir.rglob("*")):
        if not path.is_file() or path.name == ".skill_meta.json":
            continue
        content = _read_text(path)
        if content is None:
            continue
        rel_path = path.relative_to(skill_dir).as_posix()
        _scan_text_rules(content, rel_path, issues)
        if path.suffix.lower() == ".py":
            _scan_python_ast(content, rel_path, issues)

    if include_dependency_audit:
        for rule, file, line, detail in audit_requirements(skill_dir):
            issues.append(ScanIssue(rule, SEVERITY_LOW, file, line, detail))

    high = sum(1 for i in issues if i.severity == SEVERITY_HIGH)
    low = len(issues) - high
    status = "rejected" if high else ("warning" if low else "passed")
    return ScanReport(status=status, high_issues=high, low_issues=low, issues=issues)

"""
@file_name: service.py
@author: Bin Liang
@date: 2026-09-03
@description: ``SelfExtensionService`` — the plugin_* verbs as plain methods over the kernel + guards + state (the MCP tools are thin wrappers).

Flow (spec §11.3): docs → scaffold (dev dir under the agent workspace,
state draft) → edit (allow-listed paths, changelog per edit) → validate →
test (green report signed for the current tree hash) → register (link,
scope agent, requires the green report for THIS tree) → activate (a
proposal the user decides on the factory page; approval timeout rejects)
→ observe → deactivate / rollback / diff / install / upgrade /
publish_hint. Every step lands in the audit timeline. Cloud: every verb
refuses.
"""
from __future__ import annotations

import difflib
import json
import shutil
import time
from pathlib import Path
from typing import Any

from narranexus.contracts import PluginError
from narranexus.kernel.deployment import is_cloud_mode
from narranexus.kernel.plugins.compat import host_version
from narranexus.kernel.plugins.install import Installer, LocalSource
from narranexus.kernel.plugins.install.index import Index
from narranexus.kernel.plugins.lifecycle import RegistryStore
from narranexus.kernel.plugins.paths import MANIFEST_FILENAME, plugin_home, registry_path
from narranexus.kernel.plugins.registries import KERNEL_REGISTRIES
from narranexus.platform.utils.workspace_paths import agent_workspace_path

from .guards import (
    GuardError,
    check_edit_path,
    check_inside,
    check_kinds,
    check_not_protected,
    check_plugin_id,
    dev_root,
)
from .state import Audit, BudgetStore, ProposalStore
from .test_runner import run_tests
from .validate import tree_hash, validate_plugin

HEAVY_KINDS = frozenset({"hook", "routes", "worker", "tool", "mcp_server"})  # force an agent-scope canary first


class SelfExtensionService:
    def __init__(self, agent_id: str, user_id: str, *, workspace: Path | None = None, store: RegistryStore | None = None, index: Index | None = None) -> None:
        if is_cloud_mode():
            raise GuardError("self-extension is a local feature; the cloud build ships the standard plugin set")
        self.agent_id = agent_id
        self.user_id = user_id
        self.workspace = workspace or agent_workspace_path(agent_id, user_id)
        self.store = store or RegistryStore(path=registry_path())
        self.index = index
        self.proposals = ProposalStore()
        self.budgets = BudgetStore()
        self.audit = Audit()
        self.installer = Installer(store=self.store, host=host_version())

    # ------------------------------------------------------------ helpers

    def _dev_dir(self, plugin_id: str) -> Path:
        check_plugin_id(plugin_id)
        return check_inside(dev_root(self.workspace), dev_root(self.workspace) / plugin_id)

    def _record(self, plugin_id: str, action: str, **kw: Any) -> None:
        self.audit.record(agent_id=self.agent_id, user_id=self.user_id, plugin_id=plugin_id, action=action, **kw)

    def _installed(self) -> dict[str, str]:
        return {pid: r.installed_version for pid, r in self.store.read().plugins.items()}

    def _scope_ok(self, plugin_id: str) -> None:
        rec = self.store.read().plugins.get(plugin_id)
        if rec is None:
            raise GuardError(f"{plugin_id} is not registered")
        if rec.installed_by != f"agent:{self.agent_id}" and rec.scope != f"agent:{self.agent_id}" and rec.scope != "global":
            raise GuardError(f"{plugin_id} belongs to another agent")

    # -------------------------------------------------------------- read

    def list(self) -> dict[str, Any]:
        reg = self.store.read()
        mine = {pid: {"version": r.installed_version, "state": r.state, "enabled": r.enabled, "scope": r.scope, "installed_by": r.installed_by, "last_error": r.last_error} for pid, r in sorted(reg.plugins.items()) if r.scope in ("global", f"agent:{self.agent_id}")}
        drafts = sorted(p.name for p in dev_root(self.workspace).glob("*.*") if (p / MANIFEST_FILENAME).is_file()) if dev_root(self.workspace).is_dir() else []
        return {"registered": mine, "drafts": [d for d in drafts if d not in mine], "safe_mode": reg.safe_mode, "pending_proposals": [p.id for p in self.proposals.list(agent_id=self.agent_id, pending_only=True)], "slots": list(KERNEL_REGISTRIES.paths())}

    def search(self, query: str) -> list[dict[str, Any]]:
        index = self.index or Index(cache_dir=plugin_home() / ".index-cache")
        return [{"id": e.id, "repo": e.repo, "description": e.description, "kinds": list(e.kinds)} for e in index.search(query)]

    def docs(self, kinds: list[str]) -> dict[str, str]:
        from narranexus.cli.scaffold import TEMPLATES_DIR

        available = sorted(p.name for p in TEMPLATES_DIR.iterdir() if p.is_dir())
        out: dict[str, str] = {}
        for kind in check_kinds(kinds, available):
            src = TEMPLATES_DIR / kind
            parts = [f"# kind: {kind}"]
            frag = src / "manifest.fragment.json"
            if frag.is_file():
                parts.append("manifest fragment:\n" + frag.read_text(encoding="utf-8"))
            init = src / "backend" / "__init__.py"
            if init.is_file():
                parts.append("backend example:\n" + init.read_text(encoding="utf-8"))
            fe = src / "frontend" / "src" / "index.ts"
            if fe.is_file():
                parts.append("frontend example:\n" + fe.read_text(encoding="utf-8"))
            out[kind] = "\n\n".join(parts)
        out["_workflow"] = "scaffold → edit → validate → test → register(link) → activate(scope=agent) [user approves] → observe → activate(scope=global)"
        return out

    # ------------------------------------------------------------- write

    def scaffold(self, plugin_id: str, kinds: list[str], display_name: str = "") -> dict[str, Any]:
        from narranexus.cli.scaffold import TEMPLATES_DIR, scaffold

        available = sorted(p.name for p in TEMPLATES_DIR.iterdir() if p.is_dir())
        dest = self._dev_dir(plugin_id)
        check_kinds(kinds, available)
        if dest.exists() and any(dest.iterdir()):
            raise GuardError(f"{dest} already exists; use plugin_edit or pick another id")
        files = scaffold(plugin_id, kinds, dest, display_name=display_name or plugin_id.split(".", 1)[1].replace("_", " ").title())
        self._record(plugin_id, "scaffold", why=f"kinds={kinds}", diff_hash=tree_hash(dest))
        return {"path": str(dest), "files": [str(f.relative_to(dest)) for f in files], "state": "draft"}

    def edit(self, plugin_id: str, rel_path: str, content: str, why: str = "") -> dict[str, Any]:
        dest = self._dev_dir(plugin_id)
        if not dest.is_dir():
            raise GuardError(f"{plugin_id} has no draft; scaffold it first")
        target = check_edit_path(dest, rel_path, content)
        before = target.read_text(encoding="utf-8", errors="ignore") if target.is_file() else ""
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        diff = "".join(difflib.unified_diff(before.splitlines(True), content.splitlines(True), fromfile=rel_path, tofile=rel_path))
        with open(dest / ".plugin-changelog.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"at": time.time(), "path": rel_path, "why": why, "bytes": len(content.encode()), "by": f"agent:{self.agent_id}"}) + "\n")
        self._record(plugin_id, "edit", why=why or rel_path, diff_hash=tree_hash(dest), extra={"path": rel_path, "diff_lines": diff.count("\n")})
        return {"path": rel_path, "bytes": len(content.encode()), "diff": diff[-4000:]}

    def validate(self, plugin_id: str) -> dict[str, Any]:
        dest = self._dev_dir(plugin_id)
        result = validate_plugin(dest, host_version=host_version(), installed=self._installed())
        self._record(plugin_id, "validate", diff_hash=result.tree_hash, report={"ok": result.ok, "problems": result.problems, "mismatches": result.mismatches})
        return result.as_dict()

    def test(self, plugin_id: str, timeout_s: float = 300.0) -> dict[str, Any]:
        dest = self._dev_dir(plugin_id)
        report = run_tests(dest, timeout_s=timeout_s)
        self._record(plugin_id, "test", diff_hash=report.tree_hash, report={"ok": report.ok, "passed": report.passed, "failed": report.failed, "report_hash": report.report_hash})
        self._save_report(dest, report.as_dict())
        return report.as_dict()

    @staticmethod
    def _save_report(dest: Path, report: dict[str, Any]) -> None:
        (dest / ".test-report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")

    def register(self, plugin_id: str, report_hash: str) -> dict[str, Any]:
        dest = self._dev_dir(plugin_id)
        budget = self.budgets.get(self.agent_id)
        budget.check()
        saved = dest / ".test-report.json"
        if not saved.is_file():
            raise GuardError("run plugin_test first; a green report is required to register")
        report = json.loads(saved.read_text(encoding="utf-8"))
        if report.get("report_hash") != report_hash or not report.get("ok"):
            raise GuardError("the report hash does not match a green test run for this plugin")
        if report.get("tree_hash") != tree_hash(dest):
            raise GuardError("files changed since the last green test run; run plugin_test again")
        validation = validate_plugin(dest, host_version=host_version(), installed=self._installed())
        if not validation.ok:
            raise GuardError(f"validation failed: {validation.problems}")
        existing = self.store.read().plugins.get(plugin_id)
        if existing is not None:
            from narranexus.kernel.plugins.compat import Version

            if not Version.parse(existing.installed_version) < Version.parse(validation.version):
                raise GuardError(f"version must increase: registered {existing.installed_version}, draft {validation.version}")
            self._scope_ok(plugin_id)
        result = self.installer.install(LocalSource(dest, mode="link"), installed_by=f"agent:{self.agent_id}", scope=f"agent:{self.agent_id}", replace=existing is not None)
        self.store.set_enabled(plugin_id, False)  # registered/inactive until an approved activation
        budget.spend()
        self.budgets.put(self.agent_id, budget)
        self._record(plugin_id, "register", diff_hash=validation.tree_hash, report={"report_hash": report_hash}, extra={"version": result.version, "mismatches": validation.mismatches})
        return {"plugin_id": plugin_id, "version": result.version, "state": "registered", "enabled": False, "mismatches": validation.mismatches, "next": "plugin_activate(scope='agent')"}

    def activate(self, plugin_id: str, scope: str = "agent") -> dict[str, Any]:
        check_not_protected(plugin_id)
        self._scope_ok(plugin_id)
        budget = self.budgets.get(self.agent_id)
        budget.check()
        rec = self.store.read().plugins[plugin_id]
        manifest_path = Path(rec.path) / MANIFEST_FILENAME
        validation = validate_plugin(Path(rec.path), host_version=host_version(), installed=self._installed())
        heavy = any(any(k in path for k in ("routes", "workers", "hooks", "tools", "mcp_servers")) for path in self._provides(manifest_path))
        if scope == "global" and heavy and rec.scope != f"agent:{self.agent_id}" or (scope == "global" and heavy and not self._observed(plugin_id)):
            raise GuardError("hooks/routes/workers/tools plugins must be activated with scope='agent' and observed before global")
        report = json.loads((Path(rec.path) / ".test-report.json").read_text(encoding="utf-8")) if (Path(rec.path) / ".test-report.json").is_file() else {}
        proposal = self.proposals.create(
            plugin_id=plugin_id, agent_id=self.agent_id, user_id=self.user_id, action="activate", scope=scope,
            summary=f"Activate {plugin_id} {rec.installed_version} for {'this agent only' if scope == 'agent' else 'all agents'}",
            permissions=json.loads(manifest_path.read_text(encoding="utf-8")).get("permissions", {}), test_report={"ok": report.get("ok"), "passed": report.get("passed"), "report_hash": report.get("report_hash")},
            diff_hash=validation.tree_hash, extra={"mismatches": validation.mismatches},
        )
        budget.spend()
        self.budgets.put(self.agent_id, budget)
        self._record(plugin_id, "propose_activate", why=scope, diff_hash=validation.tree_hash, extra={"proposal": proposal.id})
        return {"proposal_id": proposal.id, "status": "pending_user_approval", "expires_in_s": 600, "summary": proposal.summary}

    @staticmethod
    def _provides(manifest_path: Path) -> list[str]:
        try:
            return list(json.loads(manifest_path.read_text(encoding="utf-8")).get("provides", {}))
        except (OSError, ValueError):
            return []

    def _observed(self, plugin_id: str) -> bool:
        return any(row["action"] == "observe" for row in self.audit.timeline(plugin_id=plugin_id, agent_id=self.agent_id))

    def apply_decision(self, proposal_id: str, decision: str, *, by: str) -> dict[str, Any]:
        """Called by the factory API when the user decides; performs the approved action."""
        p = self.proposals.decide(proposal_id, decision, by=by)  # type: ignore[arg-type]
        if decision != "approved":
            self._record(p.plugin_id, "rejected", why=proposal_id)
            return {"proposal_id": proposal_id, "decision": decision}
        if p.action == "activate":
            def _mutate(reg):
                rec = reg.plugins[p.plugin_id]
                rec.enabled = True
                rec.scope = "global" if p.scope == "global" else f"agent:{p.agent_id}"
                rec.permissions_acknowledged = True
                rec.state = "registered"

            self.store.update(_mutate)
            budget = self.budgets.get(p.agent_id)
            budget.success()
            self.budgets.put(p.agent_id, budget)
        elif p.action == "install":
            self.installer.install(p.extra["source"], installed_by=f"agent:{p.agent_id}", scope=f"agent:{p.agent_id}", permissions_acknowledged=True)
        elif p.action == "upgrade":
            self.installer.upgrade(p.plugin_id)
        self._record(p.plugin_id, f"{p.action}_approved", why=proposal_id, extra={"by": by, "scope": p.scope})
        return {"proposal_id": proposal_id, "decision": decision, "restart_required": True}

    def observe(self, plugin_id: str, window_s: int = 24 * 3600) -> dict[str, Any]:
        self._scope_ok(plugin_id)
        rec = self.store.read().plugins[plugin_id]
        since = time.time() - window_s
        rows = [r for r in self.audit.timeline(plugin_id=plugin_id) if r["at"] >= since]
        ui_errors = len([r for r in rows if r["action"] == "ui_error"])  # the factory API appends these
        out = {"plugin_id": plugin_id, "state": rec.state, "enabled": rec.enabled, "scope": rec.scope, "crash_count": rec.crash_count, "last_error": rec.last_error, "ui_errors": ui_errors, "audit_events": len(rows), "window_s": window_s}
        self._record(plugin_id, "observe", report=out)
        return out

    def deactivate(self, plugin_id: str) -> dict[str, Any]:
        check_not_protected(plugin_id)
        self._scope_ok(plugin_id)
        rec = self.store.set_enabled(plugin_id, False)
        self._record(plugin_id, "deactivate")
        return {"plugin_id": plugin_id, "enabled": rec.enabled, "restart_required": True}

    def rollback(self, plugin_id: str | None = None) -> dict[str, Any]:
        reg = self.store.rollback_to_lkg()
        budget = self.budgets.get(self.agent_id)
        budget.rollback()
        self.budgets.put(self.agent_id, budget)
        self._record(plugin_id or "*", "rollback", extra={"manual_required": budget.manual_required})
        return {"restored_plugins": sorted(reg.plugins), "manual_required": budget.manual_required, "restart_required": True}

    def diff(self, plugin_id: str, rel_path: str | None = None) -> dict[str, Any]:
        dest = self._dev_dir(plugin_id)
        rec = self.store.read().plugins.get(plugin_id)
        if rec is None or not Path(rec.path).is_dir():
            return {"plugin_id": plugin_id, "registered": False, "tree_hash": tree_hash(dest) if dest.is_dir() else ""}
        reg_root = Path(rec.path)
        files = [rel_path] if rel_path else sorted(str(p.relative_to(dest)) for p in dest.rglob("*") if p.is_file() and p.suffix in (".py", ".json", ".md", ".ts", ".js"))
        chunks: list[str] = []
        for rel in files:
            a = (reg_root / rel).read_text(encoding="utf-8", errors="ignore") if (reg_root / rel).is_file() else ""
            b = (dest / rel).read_text(encoding="utf-8", errors="ignore") if (dest / rel).is_file() else ""
            if a != b:
                chunks.append("".join(difflib.unified_diff(a.splitlines(True), b.splitlines(True), fromfile=f"registered/{rel}", tofile=f"draft/{rel}")))
        return {"plugin_id": plugin_id, "registered_version": rec.installed_version, "changed_files": len(chunks), "diff": "".join(chunks)[-8000:]}

    def install(self, source: str) -> dict[str, Any]:
        budget = self.budgets.get(self.agent_id)
        budget.check()
        proposal = self.proposals.create(plugin_id=source, agent_id=self.agent_id, user_id=self.user_id, action="install", scope="agent", summary=f"Install plugin from {source} for this agent", permissions={}, test_report={}, diff_hash="", extra={"source": source})
        budget.spend()
        self.budgets.put(self.agent_id, budget)
        self._record(source, "propose_install", extra={"proposal": proposal.id})
        return {"proposal_id": proposal.id, "status": "pending_user_approval"}

    def upgrade(self, plugin_id: str) -> dict[str, Any]:
        check_not_protected(plugin_id)
        self._scope_ok(plugin_id)
        check = self.installer.check_update(plugin_id)
        if not check.available:
            return {"plugin_id": plugin_id, "installed": check.installed, "available": None}
        proposal = self.proposals.create(plugin_id=plugin_id, agent_id=self.agent_id, user_id=self.user_id, action="upgrade", scope="agent", summary=f"Upgrade {plugin_id} {check.installed} → {check.available}", permissions={}, test_report={}, diff_hash="")
        self._record(plugin_id, "propose_upgrade", extra={"proposal": proposal.id, "to": check.available})
        return {"proposal_id": proposal.id, "status": "pending_user_approval", "available": check.available}

    def publish_hint(self, plugin_id: str) -> dict[str, Any]:
        from narranexus.cli.publish_check import publish_check

        dest = self._dev_dir(plugin_id)
        problems = publish_check(dest)
        steps = [
            "Create a GitHub repository for the plugin (public).",
            "Commit the plugin directory; keep frontend/dist/plugin.js built and committed.",
            "Set narranexus-plugin.json version = release tag (no 'v' prefix) and update versions.json.",
            "Push a tag; attach narranexus-plugin.json, backend.zip, plugin.js to the GitHub Release.",
            "Open a metadata PR to the official index (id, repo, description, tags, kinds).",
        ]
        return {"plugin_id": plugin_id, "checklist_problems": problems, "steps": steps, "note": "Secrets are never part of a plugin; use settings.secret."}

    def wipe_draft(self, plugin_id: str) -> None:
        dest = self._dev_dir(plugin_id)
        if dest.exists():
            shutil.rmtree(dest)
            self._record(plugin_id, "discard_draft")


__all__ = ["HEAVY_KINDS", "SelfExtensionService"]

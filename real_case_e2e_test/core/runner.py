"""
@file_name: runner.py
@author: NarraNexus E2E
@date: 2026-05-13
@description: End-to-end orchestration — discovery, group execution,
              transcript dump, programmatic + semantic analysis, report

The runner deliberately keeps each phase self-contained:

  discover → execute (per pillar group) → cleanup → programmatic →
  semantic (best-effort) → report.md + manifest.json + history.jsonl

A failure in semantic never marks a case red on its own — the
programmatic verdict is the gate.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import os
import pkgutil
import shutil
import subprocess
import time
import traceback
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Awaitable, Callable, Optional

from .api_client import APIClient
from .case_spec import CaseSpec, TalkLine
from .fixtures import CaseFixtures, ResourceLedger, cleanup_ledger
from .log_grep import slice_by_run_id
from .preflight import run_preflight
from .programmatic import analyze_transcript, write_case_metrics
from .semantic import SemanticResult, build_prompt, run_semantic
from .transcript import CaseEnv, Transcript, TurnRecord
from .ws_client import drive_turn


HERE = Path(__file__).resolve().parent
PKG_ROOT = HERE.parent  # real_case_e2e_test/
REPORTS_ROOT = PKG_ROOT / "reports"
STATE_PATH = PKG_ROOT / "state" / "history.jsonl"
NARRANEXUS_ROOT = PKG_ROOT.parent  # NarraNexus/


# `bash run.sh` puts the backend uvicorn process inside a tmux window
# named "Backend" under session "nexus-dev". loguru goes to that pane's
# stdout, which is not a file we can `open`. The cheapest correct way
# to read it is to capture the pane's scrollback after a run finishes
# and feed that snapshot to log_grep. If tmux is missing or the
# session is not running, we fall through and the report flags the
# empty log slice (no false positives).
TMUX_BACKEND_TARGET = "nexus-dev:Backend"
TMUX_CAPTURE_SCROLLBACK_LINES = 200000


def _capture_tmux_backend_log(reports_dir: Path) -> Path | None:
    if not shutil.which("tmux"):
        return None
    out_path = reports_dir / "backend_log_full.txt"
    try:
        result = subprocess.run(
            [
                "tmux",
                "capture-pane",
                "-p",
                # -J joins wrapped lines back into one logical line.
                # Without it, every log line longer than the pane width
                # is split mid-token and the `model=...` regex never
                # matches because the suffix sits on its own line.
                "-J",
                "-S",
                f"-{TMUX_CAPTURE_SCROLLBACK_LINES}",
                "-t",
                TMUX_BACKEND_TARGET,
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    if result.returncode != 0 or not result.stdout:
        return None
    out_path.write_text(result.stdout, encoding="utf-8")
    return out_path


def _load_dotenv_into_os_environ(dotenv_path: Path) -> None:
    """Best-effort loader for NarraNexus/.env so case fixtures can read
    NETMIND_API_KEY etc. without the operator pre-exporting them. We
    deliberately do NOT use python-dotenv (extra dep); the file format
    here is just ``KEY=VALUE`` per line. Existing env vars win — never
    clobber an explicit shell setting."""
    if not dotenv_path.exists():
        return
    for raw_line in dotenv_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if not key or key in os.environ:
            continue
        value = value.strip().strip('"').strip("'")
        os.environ[key] = value


# ---------------------------------------------------------------- discovery


@dataclass
class CaseModule:
    spec: CaseSpec
    talk: list[TalkLine]
    run: Callable[["CaseContext"], Awaitable[None]]
    module_name: str


def discover_cases(pillar_filter: Optional[str], case_filter: Optional[str]) -> list[CaseModule]:
    """Walk ``cases/`` and return every module exposing SPEC + TALK + run."""
    from real_case_e2e_test import cases as cases_pkg

    found: list[CaseModule] = []
    for finder, modname, ispkg in pkgutil.walk_packages(
        cases_pkg.__path__, prefix="real_case_e2e_test.cases."
    ):
        if ispkg:
            continue
        if modname.rsplit(".", 1)[-1].startswith("_"):
            continue
        mod = importlib.import_module(modname)
        if not all(hasattr(mod, attr) for attr in ("SPEC", "TALK", "run")):
            continue
        spec: CaseSpec = mod.SPEC
        if pillar_filter and spec.pillar != pillar_filter:
            continue
        if case_filter and case_filter not in spec.case_id:
            continue
        found.append(
            CaseModule(
                spec=spec,
                talk=list(mod.TALK),
                run=mod.run,
                module_name=modname,
            )
        )
    found.sort(key=lambda m: m.spec.case_id)
    return found


def group_by_pillar(cases: list[CaseModule]) -> dict[str, list[CaseModule]]:
    out: dict[str, list[CaseModule]] = {}
    for c in cases:
        out.setdefault(c.spec.pillar, []).append(c)
    return out


# ---------------------------------------------------------------- context


@dataclass
class RunnerConfig:
    base_url: str = "http://127.0.0.1:8000"
    ws_url: str = "ws://127.0.0.1:8000"
    prefix: str = "e2e"
    concurrency: int = 5
    http_timeout: float = 30.0
    inter_group_sleep_seconds: float = 15.0
    skip_semantic: bool = False
    semantic_timeout_seconds: int = 180


class CaseContext:
    """Handed to each case's ``run(ctx)``. Exposes the API client, the
    talk-driving helper, and ledger-aware fixtures."""

    def __init__(
        self,
        spec: CaseSpec,
        talk: list[TalkLine],
        config: RunnerConfig,
        env: CaseEnv,
        api: APIClient,
        ledger: ResourceLedger,
        run_prefix: str,
    ) -> None:
        self.spec = spec
        self.talk = talk
        self.config = config
        self.env = env
        self.api = api
        self.ledger = ledger
        self.fixtures = CaseFixtures(api, ledger, prefix=run_prefix)
        self.transcript = Transcript.from_spec(spec, env)

    async def drive_turn(
        self,
        *,
        user,
        agent,
        line: TalkLine,
    ) -> None:
        """Run one scripted user→agent turn against the WebSocket. The
        case author calls this directly; the transcript is updated in
        place."""
        if not self.transcript.user_id:
            self.transcript.user_id = user.user_id
        if agent.agent_id not in self.transcript.agent_ids:
            self.transcript.agent_ids.append(agent.agent_id)

        timeout = line.turn_timeout_seconds or self.spec.turn_timeout_seconds
        ws_turn = await drive_turn(
            ws_url=self.config.ws_url,
            agent_id=agent.agent_id,
            user_id=user.user_id,
            input_content=line.content,
            turn_timeout_seconds=timeout,
        )
        self.transcript.turns.append(
            TurnRecord.from_ws_turn(
                turn_index=len(self.transcript.turns),
                role=line.role,
                expect_contains=list(line.expect_contains),
                expect_not_contains=list(line.expect_not_contains),
                ws_turn=ws_turn,
            )
        )


# ---------------------------------------------------------------- execution


async def _execute_case(case: CaseModule, config: RunnerConfig, env: CaseEnv) -> Transcript:
    run_prefix = f"{config.prefix}_{env.run_ts}_{case.spec.case_id.replace('/', '_')}"
    async with APIClient(config.base_url, config.http_timeout) as api:
        ledger = ResourceLedger()
        ctx = CaseContext(
            spec=case.spec,
            talk=case.talk,
            config=config,
            env=env,
            api=api,
            ledger=ledger,
            run_prefix=run_prefix,
        )
        try:
            await case.run(ctx)
        except Exception as exc:
            ctx.transcript.driver_error = f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}"

        ctx.transcript.ended_at = time.time()

        # Cleanup belongs to the runner, not the case. Failures are
        # recorded on a dedicated transcript field so they do not get
        # confused with the case's own driver_error in the binary
        # verdict.
        ctx.transcript.cleanup_failures = await cleanup_ledger(api, ledger)
        return ctx.transcript


async def _run_group(
    cases: list[CaseModule],
    config: RunnerConfig,
    env: CaseEnv,
) -> list[Transcript]:
    sem = asyncio.Semaphore(config.concurrency)

    async def _bounded(c: CaseModule) -> Transcript:
        async with sem:
            return await _execute_case(c, config, env)

    return await asyncio.gather(*[_bounded(c) for c in cases])


# ---------------------------------------------------------------- post-processing


def _write_transcript(transcript: Transcript, reports_dir: Path) -> Path:
    safe_id = transcript.case_id.replace("/", "__")
    path = reports_dir / "transcripts" / f"{safe_id}.json"
    transcript.write(path)
    return path


def _write_programmatic(transcript: Transcript, log_slice: list[str], reports_dir: Path) -> dict:
    safe_id = transcript.case_id.replace("/", "__")
    transcript_dict = json.loads(json.dumps(asdict(transcript), default=str))
    metrics = analyze_transcript(transcript_dict, log_slice)
    write_case_metrics(metrics, reports_dir / "programmatic" / f"{safe_id}.json")
    return asdict(metrics)


async def _run_semantic_for_case(
    case: CaseModule,
    transcript: Transcript,
    metrics: dict,
    reports_dir: Path,
    semantic_timeout_seconds: int,
) -> SemanticResult:
    safe_id = transcript.case_id.replace("/", "__")
    case_spec_dict = {
        "case_id": case.spec.case_id,
        "pillar": case.spec.pillar,
        "description": case.spec.description,
        "linked_bugs": list(case.spec.linked_bugs),
        "severity": case.spec.severity,
        "tags": list(case.spec.tags),
        "semantic_intent": case.spec.semantic_intent,
    }
    talk_dict = [asdict(t) for t in case.talk]
    transcript_dict = json.loads(json.dumps(asdict(transcript), default=str))
    prompt = build_prompt(
        case_spec=case_spec_dict,
        talk=talk_dict,
        transcript=transcript_dict,
        metrics=metrics,
    )
    result = await run_semantic(
        case_id=case.spec.case_id,
        prompt=prompt,
        timeout_seconds=semantic_timeout_seconds,
    )

    sem_dir = reports_dir / "semantic"
    sem_dir.mkdir(parents=True, exist_ok=True)
    out_path = sem_dir / f"{safe_id}.md"
    body_parts = []
    if result.skipped:
        body_parts.append(f"# semantic skipped — {result.reason}\n")
    elif result.error:
        body_parts.append(f"# semantic errored — {result.error}\n")
    if result.verdict_markdown:
        body_parts.append(result.verdict_markdown)
    if not body_parts:
        body_parts.append("# semantic produced no output\n")
    out_path.write_text("\n\n".join(body_parts))
    return result


# ---------------------------------------------------------------- report


def _short_commit() -> Optional[str]:
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=PKG_ROOT.parent,
            stderr=subprocess.DEVNULL,
        )
        return out.decode().strip()
    except Exception:
        return None


def _write_manifest_and_summary(
    reports_dir: Path,
    env: CaseEnv,
    cases: list[CaseModule],
    transcripts: list[Transcript],
    metrics_list: list[dict],
    semantic_results: list[SemanticResult],
    cleanup_failures: list[str],
) -> dict:
    pass_count = sum(1 for m in metrics_list if m.get("binary_pass"))
    fail_count = len(metrics_list) - pass_count
    by_pillar: dict[str, dict] = {}

    rows: list[dict] = []
    for c, t, m, s in zip(cases, transcripts, metrics_list, semantic_results):
        pillar = c.spec.pillar
        bucket = by_pillar.setdefault(pillar, {"passed": 0, "failed": 0})
        if m.get("binary_pass"):
            bucket["passed"] += 1
        else:
            bucket["failed"] += 1
        rows.append(
            {
                "case_id": c.spec.case_id,
                "pillar": pillar,
                "severity": c.spec.severity,
                "binary_pass": bool(m.get("binary_pass")),
                "binary_pass_reason": m.get("binary_pass_reason", ""),
                "overall_duration_seconds": m.get("overall_duration_seconds"),
                "models_seen": m.get("models_seen", []),
                "semantic_skipped": s.skipped,
                "semantic_error": s.error,
                "linked_bugs": list(c.spec.linked_bugs),
            }
        )

    manifest = {
        "run_ts": env.run_ts,
        "narranexus_commit": env.narranexus_commit,
        "base_url": env.base_url,
        "ws_url": env.ws_url,
        "totals": {"passed": pass_count, "failed": fail_count, "total": len(rows)},
        "by_pillar": by_pillar,
        "cases": rows,
        "cleanup_failures": cleanup_failures,
    }
    (reports_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False)
    )

    md_lines = [
        f"# real_case_e2e_test run {env.run_ts}",
        "",
        f"- commit: `{env.narranexus_commit or 'unknown'}`",
        f"- base_url: `{env.base_url}`",
        f"- totals: **{pass_count} passed / {fail_count} failed** out of {len(rows)}",
        "",
        "## Per case",
        "",
        "| case | pillar | severity | pass | reason | duration |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for row in rows:
        md_lines.append(
            "| `{cid}` | {pillar} | {sev} | {pass_} | {reason} | {dur} |".format(
                cid=row["case_id"],
                pillar=row["pillar"],
                sev=row["severity"],
                pass_="✅" if row["binary_pass"] else "❌",
                reason=(row["binary_pass_reason"] or "").replace("|", "\\|"),
                dur=(
                    f"{row['overall_duration_seconds']:.1f}s"
                    if row["overall_duration_seconds"] is not None
                    else "—"
                ),
            )
        )
    if cleanup_failures:
        md_lines += ["", "## Cleanup failures", ""]
        md_lines += [f"- {f}" for f in cleanup_failures]
    (reports_dir / "report.md").write_text("\n".join(md_lines))

    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(STATE_PATH, "a", encoding="utf-8") as fh:
        fh.write(
            json.dumps(
                {
                    "run_ts": env.run_ts,
                    "narranexus_commit": env.narranexus_commit,
                    "totals": manifest["totals"],
                    "by_pillar": by_pillar,
                    "failures": [r["case_id"] for r in rows if not r["binary_pass"]],
                },
                ensure_ascii=False,
            )
            + "\n"
        )
    return manifest


# ---------------------------------------------------------------- public entry


async def execute(
    config: RunnerConfig,
    pillar_filter: Optional[str],
    case_filter: Optional[str],
    list_only: bool,
) -> int:
    _load_dotenv_into_os_environ(NARRANEXUS_ROOT / ".env")
    cases = discover_cases(pillar_filter, case_filter)
    print(f"[e2e] discovered {len(cases)} case(s)")
    for c in cases:
        print(f"[e2e]   {c.spec.case_id} ({c.spec.severity}, {c.spec.pillar})")
    if list_only:
        return 0
    if not cases:
        print("[e2e] nothing to run; exiting 0")
        return 0

    run_ts = time.strftime("%Y%m%d_%H%M%S")
    env = CaseEnv(
        narranexus_commit=_short_commit(),
        base_url=config.base_url,
        ws_url=config.ws_url,
        run_ts=run_ts,
    )
    reports_dir = REPORTS_ROOT / run_ts
    reports_dir.mkdir(parents=True, exist_ok=True)

    # Preflight is per-suite, not per-case.
    async with APIClient(config.base_url, config.http_timeout) as api:
        pre = await run_preflight(api, require_semantic=not config.skip_semantic)
    if not pre.ok:
        for err in pre.errors:
            print(f"[e2e][preflight][error] {err}")
        return 2
    for warn in pre.warnings:
        print(f"[e2e][preflight][warn] {warn}")
    if config.skip_semantic or not pre.claude_cli_found:
        config.skip_semantic = True
        print("[e2e] semantic phase will be skipped")

    grouped = group_by_pillar(cases)
    all_transcripts: list[Transcript] = []
    case_order: list[CaseModule] = []

    pillars = sorted(grouped.keys())
    for idx, pillar in enumerate(pillars):
        group = grouped[pillar]
        print(f"\n[e2e] === pillar '{pillar}' ({len(group)} case(s)) ===")
        group_started = time.monotonic()
        transcripts = await _run_group(group, config, env)
        for c, t in zip(group, transcripts):
            status = "ok" if not t.driver_error else "errored"
            duration = (t.ended_at - t.started_at) if t.ended_at else 0.0
            print(f"[e2e]   {status:7s} {c.spec.case_id} ({duration:.1f}s)")
        all_transcripts.extend(transcripts)
        case_order.extend(group)
        elapsed = time.monotonic() - group_started
        print(f"[e2e]   group done in {elapsed:.1f}s")
        if idx != len(pillars) - 1 and config.inter_group_sleep_seconds > 0:
            print(f"[e2e]   sleeping {config.inter_group_sleep_seconds}s before next pillar")
            await asyncio.sleep(config.inter_group_sleep_seconds)

    # Programmatic phase: one pass over transcripts in memory. Capture
    # the backend tmux pane once now so every case's run_id matches
    # against the same snapshot. log_grep accepts the path directly
    # so we do not need to pollute os.environ.
    backend_log_path = _capture_tmux_backend_log(reports_dir)
    metrics_list: list[dict] = []
    for transcript in all_transcripts:
        _write_transcript(transcript, reports_dir)
        run_ids = [t.run_id for t in transcript.turns if t.run_id]
        log_slice: list[str] = []
        for rid in run_ids:
            log_slice.extend(slice_by_run_id(rid, log_path=backend_log_path))
        if log_slice:
            (reports_dir / "backend_log").mkdir(parents=True, exist_ok=True)
            (reports_dir / "backend_log" / f"{transcript.case_id.replace('/', '__')}.txt").write_text(
                "\n".join(log_slice)
            )
        metrics = _write_programmatic(transcript, log_slice, reports_dir)
        metrics_list.append(metrics)

    # Semantic phase: serial, never blocks the report.
    semantic_results: list[SemanticResult] = []
    if config.skip_semantic:
        for case in case_order:
            semantic_results.append(
                SemanticResult(
                    case_id=case.spec.case_id,
                    verdict_markdown="",
                    skipped=True,
                    reason="skip-semantic flag or claude CLI missing",
                )
            )
    else:
        for case, transcript, metrics in zip(case_order, all_transcripts, metrics_list):
            print(f"[e2e]   semantic: {case.spec.case_id}")
            res = await _run_semantic_for_case(
                case=case,
                transcript=transcript,
                metrics=metrics,
                reports_dir=reports_dir,
                semantic_timeout_seconds=config.semantic_timeout_seconds,
            )
            semantic_results.append(res)

    cleanup_failures: list[str] = []
    for t in all_transcripts:
        cleanup_failures.extend(t.cleanup_failures or [])
    _write_manifest_and_summary(
        reports_dir=reports_dir,
        env=env,
        cases=case_order,
        transcripts=all_transcripts,
        metrics_list=metrics_list,
        semantic_results=semantic_results,
        cleanup_failures=cleanup_failures,
    )

    print(f"\n[e2e] report: {reports_dir / 'report.md'}")
    failed = sum(1 for m in metrics_list if not m.get("binary_pass"))
    return 0 if failed == 0 else 1

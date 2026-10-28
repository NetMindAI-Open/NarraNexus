"""
@file_name: test_desktop_build_uses_lock.py
@author: NarraNexus
@date: 2026-09-10
@description: The DMG's Python install must come from uv.lock, and the build
              must import what it packaged before shipping it.

2026-09-10 incident. Step 3 of build-desktop.sh was `uv pip install
"$PROJECT_ROOT"`, which RE-RESOLVES every dependency from the ranges in
pyproject.toml and ignores uv.lock. The dmg therefore shipped whatever PyPI
held on build day: 99 of the 176 resolved packages differed from the lock, and
one of them — `mcp` 2.2.0 against a locked 1.24.0 — had renamed
`streamablehttp_client` to `streamable_http_client`. Every sidecar service in
the shipped app died at import; the user saw "SQLite Proxy failed to start".

Two things had to be true for that to reach a user, and this file guards both:

  1. the install path bypassed the lock            → `test_step3_*`
  2. the build never imported the code it packaged → `test_import_smoke_*`

Same "mirror a fact + assert they agree" shape as test_plugins_extra_lockstep.py
(which guards a different property of the same step: that it stays uv-driven and
light). Cheap, and both fail on a plain `git revert` of the fix.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

from tests._shell_commands import command_lines

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "release" / "build-desktop.sh"
SMOKE = REPO / "scripts" / "release" / "bundle_import_smoke.py"
STATE_RS = REPO / "tauri" / "src-tauri" / "src" / "state.rs"


def test_step3_exports_the_lock() -> None:
    exports = command_lines(SCRIPT, "uv export")
    assert exports, (
        "build-desktop.sh no longer exports uv.lock — the bundled interpreter is "
        "then resolved from the pyproject ranges, which is the 2026-09-10 mcp-2.x "
        "breakage verbatim"
    )
    for line in exports:
        # `--locked`, not `--frozen`: uv's `--frozen` means "don't update the
        # lock before exporting", which exports a lock that is out of date with
        # pyproject.toml WITHOUT complaining. `--locked` asserts the lock would
        # not change and exits non-zero when it would, which is the property
        # this step needs — a forgotten `uv lock` must not ship a bundle that
        # is missing a dependency someone added.
        assert "--locked" in line, (
            "`uv export` must use --locked; with --frozen (or neither) a lock that "
            f"is out of date with pyproject.toml is used silently:\n    {line}"
        )
        assert "--no-editable" in line, (
            "the bundled install must stay NON-editable — an editable install bakes "
            f"the build machine's absolute source path into the .app:\n    {line}"
        )
        # The export decides what ends up in the bundle, so the light-build
        # invariant is asserted HERE, on the line that carries it.
        assert "--no-dev" in line, (
            "the export must exclude the dev group; without it pytest / ruff / "
            f"pyright / pillow all ship inside the .app:\n    {line}"
        )
        assert "--extra plugins" not in line and "--all-extras" not in line, (
            "the desktop build stays the LIGHT one — the coding-agent SDKs "
            "(~186 MB claude-agent-sdk) are installed on demand from Settings → "
            f"Plugins, not exported into the bundle:\n    {line}"
        )


def test_step3_installs_from_the_exported_requirements() -> None:
    installs = command_lines(SCRIPT, "uv pip install")
    assert installs, "build-desktop.sh no longer installs the project — update this guard"
    for line in installs:
        # On the install, not just the export (v1.21.3): the export writes the
        # workspace members as bare paths and `uv pip install -r` installs those
        # editable unless the INSTALL says otherwise.
        assert "--no-editable" in line, (
            "the install itself must carry --no-editable; without it the 30 "
            "workspace members become `.pth` hooks into the build machine's source "
            f"tree and the shipped app cannot import them:\n    {line}"
        )
        assert re.search(r"(^|\s)(-r\s|--requirements?[=\s])", line), (
            "the bundled install must read the exported requirements file; installing "
            f"the project directly re-resolves from the pyproject ranges:\n    {line}"
        )
        # The exact 2026-09-10 shape, spelled out so a revert cannot pass by
        # keeping a `-r` somewhere else on the same line.
        assert not re.search(r'uv pip install[^|;]*\s"\$PROJECT_ROOT"', line), (
            "installing $PROJECT_ROOT directly is the pre-2026-09-10 command that "
            f"ignored uv.lock:\n    {line}"
        )


def test_import_smoke_runs_in_the_build() -> None:
    assert SMOKE.is_file(), f"missing {SMOKE.relative_to(REPO)}"
    calls = command_lines(SCRIPT, "bundle_import_smoke.py")
    assert calls, (
        "build-desktop.sh no longer runs the bundle import smoke test — an "
        "unimportable bundle would again be signed, notarized and shipped"
    )
    assert any("$PYTHON_DIR" in line for line in calls), (
        "the smoke test must run under the BUNDLED interpreter (the whole point is "
        "to exercise the wheels just installed there):\n    " + "\n    ".join(calls)
    )


def _smoke_tuple(name: str) -> tuple[str, ...]:
    """A module-level tuple constant out of the smoke script, read via AST.

    Reading rather than importing: this file must not execute the smoke script,
    which imports the whole backend. `AnnAssign` is matched too — an annotated
    `ENTRYPOINTS: tuple[str, ...] = (...)` is the natural thing to write in a
    repo that runs pyright, and it would otherwise read as "constant missing".
    """
    for node in ast.parse(SMOKE.read_text(encoding="utf-8")).body:
        targets = (
            node.targets if isinstance(node, ast.Assign)
            else [node.target] if isinstance(node, ast.AnnAssign)
            else []
        )
        if not any(isinstance(t, ast.Name) and t.id == name for t in targets):
            continue
        # A bare annotation (`ENTRYPOINTS: tuple[str, ...]`) has no value. Say
        # that, rather than "not found" — the two need different fixes.
        assert node.value is not None, (
            f"{name} is declared in bundle_import_smoke.py but has no value"
        )
        return tuple(ast.literal_eval(node.value))
    raise AssertionError(f"{name} not found in bundle_import_smoke.py")


def _smoke_entrypoints() -> set[str]:
    """ENTRYPOINTS out of the smoke script, read without importing it."""
    return set(_smoke_tuple("ENTRYPOINTS"))


def _args_blocks(body: str) -> list[str]:
    """Every `args: vec![ ... ]` body, sliced by BRACKET MATCHING.

    A regex that stops at the first `],` is at the mercy of Rust formatting: a
    trailing `// comment` after the closing bracket, or a single-line `vec![..]`,
    makes the whole ServiceDef invisible — and an invisible ServiceDef is
    exactly the case this guard exists to catch, failing OPEN. Counting
    brackets has no such blind spot.
    """
    blocks: list[str] = []
    marker = "args: vec!["
    idx = body.find(marker)
    while idx != -1:
        start = idx + len(marker)
        depth, i = 1, start
        while i < len(body) and depth:
            if body[i] == "[":
                depth += 1
            elif body[i] == "]":
                depth -= 1
            i += 1
        blocks.append(body[start:i - 1])
        idx = body.find(marker, i)
    return blocks


_ASGI_TARGET = re.compile(r"[A-Za-z_][\w.]*:[A-Za-z_]\w*")


def _service_blocks(body: str) -> list[str]:
    """Every `ServiceDef { ... }` body, sliced by brace matching."""
    blocks: list[str] = []
    marker = "ServiceDef {"
    idx = body.find(marker)
    while idx != -1:
        start = idx + len(marker)
        depth, i = 1, start
        while i < len(body) and depth:
            if body[i] == "{":
                depth += 1
            elif body[i] == "}":
                depth -= 1
            i += 1
        blocks.append(body[start:i - 1])
        idx = body.find(marker, i)
    return blocks


def _state_rs_services() -> list[dict]:
    """Each bundled_services() entry as {literals, modules, launch_targets, port}.

    ONE reader for everything the guards need from state.rs, with the
    structural invariants in one place: one block per ServiceDef, exactly one
    args vec per block, and every entry yielding both an importable module and
    a launch target. A reader that lets one entry contribute nothing fails OPEN
    — a new sidecar with an unrecognised launch shape would go unverified while
    the rest of the list still looks fine.

    - modules: what must IMPORT (the `-m` runner counts: `-m uvicorn` means
      uvicorn must import too), `.py` paths as module names;
    - launch_targets: the literal strings the relocated-app step must launch
      (`.py` paths verbatim, the ASGI `mod:app` — never the runner itself, or
      `"uvicorn" in run` would pass on `-m uvicorn` alone);
    - port: `port: Some(N)` when the entry declares one.
    """
    text = STATE_RS.read_text(encoding="utf-8")
    start = text.index("fn bundled_services")
    # bundled_services only — dev_services runs the same modules through
    # `uv run` against the source tree, which is not what the dmg ships.
    end = text.index("fn dev_services", start)
    body = text[start:end]

    blocks = _service_blocks(body)
    assert len(blocks) == body.count("ServiceDef {"), (
        f"parsed {len(blocks)} ServiceDef blocks but bundled_services() contains "
        f"{body.count('ServiceDef {')} 'ServiceDef {{' — the reader lost one (or the "
        f"text occurs in a comment); fix the reader rather than the count"
    )
    services: list[dict] = []
    for block in blocks:
        args = _args_blocks(block)
        assert len(args) == 1, f"a ServiceDef has {len(args)} args vecs, expected 1"
        literals = re.findall(r'"([^"]*)"', args[0])
        runs_asgi_server = "uvicorn" in literals
        modules: set[str] = set()
        targets: list[str] = []
        for i, literal in enumerate(literals):
            if literal == "-m" and i + 1 < len(literals):
                modules.add(literals[i + 1])
                if not runs_asgi_server:
                    targets.append(literals[i + 1])
            elif literal.endswith(".py"):
                modules.add(literal.removeprefix("src/").removesuffix(".py").replace("/", "."))
                targets.append(literal)
            elif runs_asgi_server and _ASGI_TARGET.fullmatch(literal):
                modules.add(literal.split(":")[0])
                targets.append(literal)
        assert modules and targets, (
            "a bundled_services() entry yielded no importable module or no launch "
            "target — the launch shape is new (console script? bare path?) and this "
            f"reader has to learn it, otherwise that service ships unverified:\n    {literals}"
        )
        port = re.search(r"port:\s*Some\((\d+)\)", block)
        services.append({
            "literals": literals,
            "modules": modules,
            "launch_targets": targets,
            "port": int(port.group(1)) if port else None,
        })
    return services


def _state_rs_entrypoints() -> set[str]:
    """Every module state.rs's bundled services need to import."""
    return set().union(*(service["modules"] for service in _state_rs_services()))


def _state_rs_launch_targets() -> list[str]:
    """The literal launch targets the relocated-app step must start."""
    return [t for service in _state_rs_services() for t in service["launch_targets"]]


def test_smoke_entrypoints_match_state_rs() -> None:
    smoke, rust = _smoke_entrypoints(), _state_rs_entrypoints()
    assert smoke == rust, (
        "bundle_import_smoke.ENTRYPOINTS is out of lockstep with state.rs "
        f"bundled_services():\n  only in state.rs: {sorted(rust - smoke)}\n"
        f"  only in the smoke test: {sorted(smoke - rust)}"
    )


def test_uv_is_pinned_and_consistent_across_workflows() -> None:
    """Every setup-uv step pins uv, and they all pin the SAME uv.

    `uv export --locked` asserts the lock would not change under the uv that
    runs it, so which uv CI installs is part of the build contract. Drift
    between the two workflows separates a green PR from a red release: ci.yml
    proves the lock against one uv while build-desktop.yml, which only runs
    after the tag is pushed, proves it against another. Asserted by content,
    not by count — a hardcoded number of steps rots the moment a job is added.
    """
    import yaml  # pyyaml is a runtime dependency (pyproject.toml)

    # The whole workflow directory, not a hardcoded list: a file list rots for
    # the same reason a step count does — a new workflow that installs uv would
    # simply be invisible to a guard that only knows today's two.
    pins: list[tuple[str, str]] = []
    unpinned: list[str] = []
    for path in sorted((REPO / ".github/workflows").glob("*.y*ml")):
        rel = path.relative_to(REPO)
        workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
        for job_name, job in (workflow.get("jobs") or {}).items():
            for index, step in enumerate(job.get("steps") or []):
                if "astral-sh/setup-uv" not in str(step.get("uses", "")):
                    continue
                version = (step.get("with") or {}).get("version")
                # Keyed by step, not by job: two setup-uv steps in one job would
                # otherwise collapse into one and hide a disagreement.
                where = f"{rel}:{job_name}:step{index}"
                if version:
                    pins.append((where, str(version)))
                else:
                    unpinned.append(where)

    # More important now that the file list is a glob: a mistyped pattern would
    # otherwise scan nothing and pass.
    assert pins, "no setup-uv steps found — did the workflows move? update this guard"
    assert not unpinned, (
        "these setup-uv steps install whatever uv is newest that day, which "
        "`uv export --locked` turns into a failure after the tag is pushed:\n  "
        + "\n  ".join(unpinned)
    )
    assert len({version for _, version in pins}) == 1, (
        f"the workflows pin different uv versions, so CI and the release build "
        f"prove the lock against different resolvers: {pins}"
    )


def test_smoke_checks_uvicorns_lazily_resolved_deps() -> None:
    """`websockets` must stay in the smoke test, and the loop must still run it.

    uvicorn stores its protocol/loop implementations as import-path STRINGS and
    resolves them in `Config.load()`, so `import uvicorn` leaves uvloop,
    httptools and websockets untouched. `websockets` is the one that bites:
    it is a direct dependency of this project that NOTHING in src/ backend/
    plugins/ packages/ imports, so uvicorn's websockets_impl is its only
    consumer — and a broken wheel there does not raise, it just logs
    "Unsupported upgrade request" per connection while the build stays green and
    the desktop app's chat WS is dead.

    Asserted by name and by structure, never by a total count: this file has
    already paid for a hardcoded "four entrypoints" once.
    """
    lazy = _smoke_tuple("LAZY_RUNTIME_IMPORTS")
    assert "websockets" in lazy, (
        "websockets dropped out of the smoke test — nothing else in the repo "
        f"imports it, so the bundle would ship it unverified: {lazy}"
    )

    # ...and ONE loop must iterate both tuples. Asserting only that each name
    # appears in some loop would still pass if the lazy group were split into
    # its own pass that merely warns — which is the most natural way to weaken
    # this (uvloop failing really is just a perf regression, so "that one
    # shouldn't be fatal" is an easy sell) and exactly what the script's own
    # header argues against.
    per_loop: list[set[str]] = []
    for node in ast.walk(ast.parse(SMOKE.read_text(encoding="utf-8"))):
        if isinstance(node, ast.For):
            per_loop.append({sub.id for sub in ast.walk(node.iter) if isinstance(sub, ast.Name)})
    assert any({"ENTRYPOINTS", "LAZY_RUNTIME_IMPORTS"} <= names for names in per_loop), (
        "no single loop iterates both ENTRYPOINTS and LAZY_RUNTIME_IMPORTS — the "
        "two must share one fatal path, or the lazy group can be quietly demoted "
        f"to a warning (loops found: {[sorted(n) for n in per_loop if n]})"
    )


def test_smoke_checks_the_bundle_is_relocatable() -> None:
    """The smoke script must check the INSTALL, not only the imports.

    Import checks run on the build machine, where an editable install's `.pth`
    target exists — so they pass on a bundle that cannot work anywhere else.
    That is exactly how v1.21.3 shipped: green build, green smoke, and
    `No module named 'narranexus.contracts'` on every user's Mac. The
    relocatability check has to exist AND run before the imports.
    """
    tree = ast.parse(SMOKE.read_text(encoding="utf-8"))
    functions = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
    assert "_non_relocatable_installs" in functions, (
        "bundle_import_smoke.py lost its relocatability check — an editable/"
        "non-relocatable bundle would pass the import smoke on the build machine"
    )
    main = functions.get("main")
    assert main is not None, "bundle_import_smoke.py has no main()"
    called = {
        n.func.id for n in ast.walk(main)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
    }
    assert "_non_relocatable_installs" in called, (
        "main() no longer calls _non_relocatable_installs — the check exists but "
        "never runs, which is the same as not having it"
    )


def test_release_workflow_runs_the_shipped_app_without_the_checkout() -> None:
    """The release workflow must run the FINAL app with the checkout hidden, before uploading.

    Every other gate here runs on the build machine, where the source tree
    exists — v1.21.3 passed all of them and still died on every user's Mac,
    because its bundle leaned on /Users/runner/work/... through editable `.pth`
    hooks. The only check that sees what a user sees is one that removes the
    checkout and then launches the app; it has to sit before both uploads, or a
    broken bundle is published first and found second.
    """
    import yaml  # pyyaml is a runtime dependency (pyproject.toml)

    workflow = yaml.safe_load(
        (REPO / ".github/workflows/build-desktop.yml").read_text(encoding="utf-8")
    )
    steps = workflow["jobs"]["build-macos"]["steps"]
    names = [str(step.get("name", "")) for step in steps]

    def index_of(fragment: str) -> int:
        hits = [i for i, name in enumerate(names) if fragment in name]
        assert hits, f"no workflow step named like {fragment!r}: {names}"
        return hits[0]

    verify = index_of("without the source tree")
    build = index_of("Build, sign, notarize")
    uploads = [i for i, name in enumerate(names) if name.startswith("Upload")]
    assert uploads, "no upload steps found — update this guard"
    assert build < verify < min(uploads), (
        "the relocated-app verification must run after the build and before "
        f"every upload (build={build}, verify={verify}, uploads={uploads})"
    )

    # The comments and mirror promise the sidecar logs survive a failure; the
    # step that makes that true must exist, run only on failure, come right
    # after the verification, and point at where the verification writes them.
    collect = index_of("sidecar logs (on failure)")
    assert collect == verify + 1, (
        f"the log-collection step must directly follow the verification "
        f"(verify={verify}, collect={collect})"
    )
    collect_step = steps[collect]
    assert "failure()" in str(collect_step.get("if", "")), (
        "the log-collection step must run on failure (job-level), not unconditionally"
    )
    assert "upload-artifact" in str(collect_step.get("uses", "")), (
        "the log-collection step must upload the logs as an artifact"
    )
    assert "relocated/*.log" in str((collect_step.get("with") or {}).get("path", "")), (
        "the log-collection step must upload the verification's sidecar logs"
    )

    step = steps[verify]
    # A gate this expensive (it runs after notarization) is exactly the one a
    # "just get the release out" edit would soften. Neither of the two quiet
    # ways to do that — without deleting the step — is allowed.
    assert not step.get("continue-on-error"), (
        "the relocated-app verification is the only gate that sees what a user "
        "sees; continue-on-error turns it into a log line"
    )
    assert "if" not in step, (
        "the relocated-app verification must run on every desktop build — a "
        "condition here is how it gets disabled without being deleted"
    )

    run = str(step.get("run", ""))
    # Code lines only, for every assertion below that looks for a token the
    # step's own comments also mention: a guard satisfied by the comment that
    # explains it is not a guard. (Whole-line comments only — trailing
    # comments stay, which none of these tokens depend on.)
    code = "\n".join(line for line in run.splitlines() if not line.lstrip().startswith("#"))
    # The three things that make it a real check rather than a re-run of the
    # build-machine smoke: the checkout is moved away, the app's OWN copy of
    # the smoke script runs, and a sidecar is actually launched until it binds.
    assert 'mv "$GITHUB_WORKSPACE"' in run, "the step no longer hides the checkout"
    assert "set -euo pipefail" in run, "the step must fail on any unhandled error"
    assert "trap restore EXIT" in run, "the checkout must be restored on every exit path"
    for signal_name in ("INT", "TERM"):
        assert re.search(rf"trap '[^']*restore[^']*exit[^']*' {signal_name}\b", run), (
            f"{signal_name} must restore the checkout AND exit — a handler that returns "
            "lets the script run on after the checkout was put back"
        )
    # The explicit failure path must actually EXIT: `|| { echo ...; }` without
    # it keeps the message and quietly turns the smoke into "log and continue"
    # (the `||` already exempts the command from `set -e`).
    assert re.search(r"failed the import smoke[^\n]*exit 1", run), (
        "the relocated-app smoke needs an explicit failure path that exits, not only "
        "`set -e` — it is the one command that reproduces v1.21.3 directly"
    )
    # Everything about the log scan is asserted on CODE lines only. The step's
    # own comments quote these very strings (`cannot import name ...`, the
    # grep|grep -q it avoids), and a guard satisfied by the comment explaining
    # it is not a guard: deleting the pattern from the awk would stay green.
    # Caught-and-logged import failures too (the supervisor and plugin hooks
    # log only the message), iterating the launched sidecars, not a hand-kept
    # third list of names.
    for message in ("cannot import name ", "No module named "):
        assert message in code, f"the log scan no longer catches logged {message!r} failures"
    assert 'name="${entry##*:}"' in code and "for name in sqlite_proxy" not in code, (
        "the log scan must iterate $PIDS, so a new sidecar's log is scanned without "
        "anyone remembering to add it"
    )
    # DEBUG probes excluded in all three spellings that reach these logs.
    for spelling in (":DEBUG", "| DEBUG ", "^DEBUG:"):
        assert spelling in code, f"the log scan no longer skips {spelling!r} DEBUG lines"
    # No `grep -v ... | grep -q`: under pipefail an early match SIGPIPEs the
    # first grep and the pipeline reads as "no match".
    assert not re.search(r"grep[^\n|]*\|\s*grep -q", code), (
        "a `grep ... | grep -q` scan can report a real match as clean under pipefail"
    )
    # Both non-clean outcomes of the awk must fail the step, matched on
    # structure (branch + `fail`), not on the wording of the message. `0)` is
    # the one a false red would soften first — `0) ;;` turns the whole scan
    # into a no-op; `*)` is awk itself not running.
    assert re.search(r"^\s*0\)\s*fail ", code, re.MULTILINE), (
        "an awk hit must fail the step — a `0) ;;` turns the whole log scan into a no-op"
    )
    assert re.search(r"^\s*\*\)\s*fail ", code, re.MULTILINE), (
        "an awk that fails to run (exit 2) must fail the step, not pass as clean"
    )
    assert 'export PATH="$RES/nodejs/bin' in run and "/usr/bin:/bin:/usr/sbin:/sbin" in run, (
        "the sidecars must get a Finder launch's PATH (bundled node dirs + launchd's "
        "minimal PATH), not the runner's wider one"
    )
    assert "unset NARRANEXUS_DEPLOYMENT_MODE" in run, (
        "the step must scrub deployment-mode env the way a Finder launch does, or a "
        "runner-side variable decides whether backend thinks it is in the cloud"
    )
    assert "$PROJ/scripts/release/bundle_import_smoke.py" in run, (
        "the smoke must run from the relocated app's project copy"
    )
    # All four sidecars, not just the first: an import check proves a module
    # loads, not that the service it belongs to comes up. Launch targets are
    # read from state.rs's bundled_services() via the same reader the lockstep
    # test uses, so a new service there that is not launched here goes red.
    for target in _state_rs_launch_targets():
        assert target in code, (
            f"the relocated-app step does not launch {target!r}, which state.rs's "
            "bundled_services() starts — that service's startup is unverified"
        )
    # 8100 / 8000 straight from state.rs's `port: Some(..)`; 7801 / 47831 are
    # Python constants (MCP_PORT default, HEALTHZ_PORT) named next to the loop.
    declared = {service["port"] for service in _state_rs_services() if service["port"]}
    assert declared, "no `port: Some(..)` parsed from state.rs — update the reader"
    # Each port must appear in a wait_port CALL: the step's comment names all
    # four numbers, and SQLITE_PROXY_PORT=8100 names one again, so a bare
    # substring check passes with the wait deleted.
    for port in sorted(declared) + [7801, 47831]:
        assert re.search(rf"wait_port \w+ {port}\b", code), (
            f"the step no longer waits for :{port}"
        )
    assert "/docs" in run, "backend must be probed over HTTP, not only for an open port"
    assert "/healthz" in run, (
        "workers must be gated on its health endpoint, not a fixed sleep"
    )
    # An open port proves nothing if someone else already held it: the step
    # must refuse to test on a taken port and confirm its own process is alive.
    assert "port_free" in run and "already in use" in run, (
        "the step must check each port is free before launching"
    )

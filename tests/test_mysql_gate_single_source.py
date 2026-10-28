"""
@file_name: test_mysql_gate_single_source.py
@author: NarraNexus
@date: 2026-08-17
@description: Every MySQL dialect twin must gate on the same env var name.

`tests/mysql_dialect.py` exists so that "run this against a real MySQL" is
described in one place, and its docstring is honest that it did not finish the
job: one twin imports it (`tests/backend/test_team_room_activity_mysql.py`)
and the rest each keep their own `MYSQL_URL_ENV = "NARRANEXUS_MYSQL_TEST_URL"`.

Copies of a string are fine right up until one of them changes. The failure mode
is the nasty kind: rename the env var in the helper (or in CI) and the copies
stop matching, their `skipif` fires, and **those dialect twins report green while
executing nothing**. That is the exact regression the
`backend-tests` CI job was added to prevent, arriving through the one door the
job cannot see — a skip is not a failure.

So until the copies are migrated, the invariant is enforced from outside: every
`*_mysql.py` twin's gate names the same variable, and the CI workflow passes that
same name to the step that runs the suite.
This test needs no MySQL and no container; it reads files.

Whoever migrates the copies into `tests.mysql_dialect` should keep this test —
it then guards the helper against CI drifting away from it, which is the half
that survives the refactor.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
_TESTS = _REPO / "tests"
_WORKFLOW = _REPO / ".github/workflows/ci.yml"
_CI_JOB = "backend-tests"

# Files that legitimately name the gate without being twins: the helper declares
# it (that is its job), and this file quotes it throughout. Excluded by exact
# path — a rule like "skip anything with 'dialect' in the name" would be one more
# thing a filename could satisfy.
_NOT_TWINS = {"tests/mysql_dialect.py", "tests/test_mysql_gate_single_source.py"}

# The single source of truth, by definition: whatever the shared helper says.
_HELPER = _TESTS / "mysql_dialect.py"


def _canonical_env_name() -> str:
    """The canonical name, or a readable failure.

    Resolved lazily and through `pytest.fail`, not at import time. A module-level
    regex whose `.group(1)` the other tests reach into turns "the helper stopped
    declaring MYSQL_URL_ENV" into `AttributeError: 'NoneType' has no attribute
    'group'`, which buries the explanation this file exists to give; a
    module-level `read_text()` turns "the helper was renamed" into a collection
    error, so not even the explaining test gets to run.
    """
    if not _HELPER.exists():
        pytest.fail(
            f"{_HELPER.relative_to(_REPO)} is gone — it is where the canonical "
            f"MySQL gate env var name is declared, so there is nothing left to "
            f"check the dialect twins against"
        )
    found = re.search(r'^MYSQL_URL_ENV\s*=\s*"([^"]+)"', _HELPER.read_text(), re.M)
    if found is None:
        pytest.fail(
            f"{_HELPER.relative_to(_REPO)} no longer declares MYSQL_URL_ENV — "
            f"this file's whole premise was that it is the canonical name"
        )
    return found.group(1)


def _parse(path: Path, text: str) -> ast.AST:
    try:
        return ast.parse(text, filename=str(path))
    except SyntaxError as exc:  # pragma: no cover — a broken twin
        pytest.fail(
            f"{path.relative_to(_REPO)} does not parse ({exc}), so whether it "
            f"gates on the canonical env var cannot be established"
        )


def _assigned_names(node: ast.AST) -> list[str]:
    """The plain names a statement assigns, for `x = …` and `x: T = …` alike.

    Both forms exist in this repo's tests, and handling one but not the other is
    how a correctly gated twin gets falsely reported — this file's own line about
    false reds being how guards get relaxed away applies to itself.
    """
    if isinstance(node, ast.Assign):
        targets = node.targets
    elif isinstance(node, ast.AnnAssign):
        targets = [node.target]
    else:
        return []
    return [t.id for t in targets if isinstance(t, ast.Name)]


def _mentions_skipif(tree: ast.AST) -> bool:
    """True iff this subtree structurally references `skipif`.

    Structural, not `"skipif" in ast.dump(...)`: a dump includes string
    CONSTANTS, so `@pytest.mark.parametrize("case", ["skipif removed"])` would
    satisfy a substring scan. That is the same prose-satisfies-the-guard defect
    this file removed from its env-var check in the commit before this one, and
    it had been reintroduced here.
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr == "skipif":
            return True
        if isinstance(node, ast.Name) and node.id == "skipif":
            return True
    return False


def _imports_the_helper(tree: ast.AST) -> bool:
    """True iff this module really imports `mysql_dialect`, in any spelling."""
    # Compare the last dotted SEGMENT, not a string suffix: `endswith` would also
    # accept `tests.legacy_mysql_dialect`, and a stray match here grants amnesty
    # to a twin with no gate at all.
    #
    # Splitting is strictly load-bearing only on the `ast.Import` branch, the one
    # place `alias.name` can be dotted (`import tests.mysql_dialect`). On
    # `ImportFrom`, `node.module` carries the module path — `None` for
    # `from . import mysql_dialect` (hence `or ""`, which prevents a TypeError),
    # and the bare name for `from ..mysql_dialect import X`, since the level
    # lives in `node.level`. The `alias.name` comparison is exact because that
    # branch sees an already-single name; aliases cannot slip past it either way,
    # `alias.name` is the real name and `asname` holds the alias.
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if (node.module or "").split(".")[-1] == "mysql_dialect":
                return True
            if any(a.name == "mysql_dialect" for a in node.names):
                return True
        elif isinstance(node, ast.Import):
            if any(a.name.split(".")[-1] == "mysql_dialect" for a in node.names):
                return True
    return False


def _declared_env_names(tree: ast.AST) -> set[str]:
    """Every string assigned to `MYSQL_URL_ENV`, from the AST.

    Read from the syntax tree rather than by `re.findall` over the file, because
    a text scan accepts and rejects for the wrong reasons — measured, both
    directions:

      * a twin with NO gate at all, whose only trace is
        `# gate removed; used to be MYSQL_URL_ENV = "NARRANEXUS_MYSQL_TEST_URL"`,
        passed — the precise false green this file exists to stop;
      * a correctly gated twin whose docstring narrates
        `Historically this was MYSQL_URL_ENV = "OLD_MYSQL_URL"` failed, and a
        false red on a correct twin is how a guard gets "relaxed" away.

    Both are live: the twins' docstrings routinely quote the env var, one of them
    verbatim as an `export` line.
    """
    found: set[str] = set()
    for node in ast.walk(tree):
        if "MYSQL_URL_ENV" not in _assigned_names(node):
            continue
        value = node.value
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            found.add(value.value)
    return found


def _has_skip_gate(tree: ast.AST, imports_helper: bool) -> bool:
    """True iff the module installs a gate that can actually skip it.

    Matching the env var's NAME never established that anything acts on it: a
    twin holding the right string with no gate would run unconditionally and
    error on every machine without MySQL.

    Judged on the VALUE and at module scope, because the obvious cheap version
    of this check is wrong in three directions at once — a bare "is something
    called `pytestmark` assigned anywhere" accepts
    `pytestmark = pytest.mark.asyncio` (four non-twin files in this repo use
    exactly that idiom, so it is the line someone will copy), accepts a
    function-local `pytestmark` that pytest ignores, and — via `ast.dump` —
    accepts a string constant that merely spells "skipif".

    A twin that imports the shared helper is exempt from the `skipif` shape, for
    the same reason it is exempt from declaring the env var: the helper owns the
    gate, and `tests/mysql_dialect.py`'s stated endgame is twins taking it from
    there (a future `pytestmark = mysql_dialect.SKIP_WITHOUT_MYSQL` must not be
    reported as ungated). It must still assign `pytestmark`.
    """
    for node in getattr(tree, "body", []):  # module level only; pytest ignores the rest
        if "pytestmark" not in _assigned_names(node):
            continue
        value = node.value
        if value is None:  # `pytestmark: list` with no value gates nothing
            continue
        if imports_helper or _mentions_skipif(value):
            return True
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for dec in node.decorator_list:
                target = dec.func if isinstance(dec, ast.Call) else dec
                if _mentions_skipif(target):
                    return True
    return False


def _test_roots() -> list[Path]:
    """Every directory pytest collects from (``pyproject.toml`` ``testpaths``).

    ``tests/`` alone was the scan face, but batch 6 moved a plugin's tests into
    ``plugins/<id>/tests`` and added them to ``testpaths``: a MySQL twin placed
    there was invisible to every check in this file — it could drift its env
    var, skip in CI and stay green forever, which is the exact failure this
    file exists to prevent. Derived from the same helper the other engine-wide
    guards use, so the next move fixes every guard at once.
    """
    roots = [_TESTS, *sorted((_REPO / "plugins").glob("*/tests"))]
    return [r for r in roots if r.is_dir()]


def _iter_test_files(pattern: str) -> list[Path]:
    seen: list[Path] = []
    for root in _test_roots():
        seen.extend(p for p in root.rglob(pattern) if "__pycache__" not in p.parts)
    return sorted(seen)


def test_the_scan_face_matches_what_pytest_collects():
    """The guards below are only worth their runtime if they look everywhere
    pytest does; an empty or shrunken scan face is the failure mode this whole
    file was written to catch, so it is asserted rather than assumed."""
    import tomllib

    testpaths = tomllib.loads((_REPO / "pyproject.toml").read_text())["tool"]["pytest"]["ini_options"]["testpaths"]
    expected = {p for spec in testpaths for p in _REPO.glob(spec) if p.is_dir()}
    assert set(_test_roots()) == expected, sorted(str(p) for p in expected.symmetric_difference(_test_roots()))
    assert len(_iter_test_files("*.py")) >= 500  # the walk really found the suite


def _twins() -> list[Path]:
    return _iter_test_files("*_mysql.py")


def test_the_helper_still_declares_the_env_var_name():
    """Everything below is measured against this one line, so its absence has to
    be a failure here rather than a silently empty comparison."""
    assert _canonical_env_name()


def test_no_gated_file_sits_outside_the_twin_shape():
    """Nothing may gate on the MySQL env var from outside `*_mysql.py`.

    This replaces a hand-written twin count (`_TWIN_FLOOR = 9`). The count did
    catch a twin renamed out of the shape, but only while it equalled the real
    number: a floor is pressured downward only, so the first twin added without
    raising it bought a free silent departure — and raising it meant every PR
    that adds a twin had to edit THIS file, a cross-branch tax paid once per
    twin. One such branch was already in flight when the count landed.

    Scanning content instead asks the question the count was standing in for:
    does any file gate on this env var while sitting outside the glob that the
    check below iterates? It is not self-satisfying (content and filename are
    independent signals), needs no maintenance, and additionally catches a twin
    that was never named `*_mysql.py` in the first place — which the count never
    could.

    What it gives up, stated plainly: a twin DELETED outright leaves no trace
    here, where the count would have gone red and demanded a reason. That is the
    acceptable half — a deletion is visible in its own diff, whereas a rename
    silently shrinks the checked set.
    """
    strays: dict[str, list[str]] = {}
    for path in _iter_test_files("*.py"):
        rel = str(path.relative_to(_REPO))
        if rel in _NOT_TWINS or path.name.endswith("_mysql.py"):
            continue
        tree = _parse(path, path.read_text())
        reasons = []
        if _declared_env_names(tree):
            reasons.append("declares MYSQL_URL_ENV")
        if _imports_the_helper(tree):
            reasons.append("imports tests.mysql_dialect")
        if reasons:
            strays[rel] = reasons

    assert not strays, (
        "these files gate on the MySQL dialect env var but are not named "
        f"`*_mysql.py`, so `_twins()` cannot see them and the check below never "
        f"looks at them — they can drift their env var and skip in CI, green, "
        f"forever: {strays}. Either rename them into the shape or, if one is a "
        f"legitimate non-twin, add it to _NOT_TWINS with a reason."
    )


def test_every_twin_gates_on_the_canonical_env_var_name():
    """Most twins hold their own copy of the name. A copy that drifts does not
    fail — it skips, and a skip is green. So does a twin that holds the right
    name and never acts on it, which is why the gate's existence is asserted too.
    """
    canonical = _canonical_env_name()
    offenders: dict[str, list[str]] = {}

    for twin in _twins():
        tree = _parse(twin, twin.read_text())
        names = _declared_env_names(tree)
        # A twin that IMPORTS the helper declares nothing of its own; it inherits
        # the canonical name by construction.
        imports_helper = _imports_the_helper(tree)

        problems = sorted(n for n in names if n != canonical)
        if not names and not imports_helper:
            problems = ["<no gate at all>"]
        elif not _has_skip_gate(tree, imports_helper):
            problems = problems or ["<names the env var but installs no skipif>"]
        if problems:
            offenders[str(twin.relative_to(_REPO))] = problems

    assert not offenders, (
        f"these dialect twins do not gate on {canonical!r}, so they will skip "
        f"(and report green) wherever it is configured: {offenders}"
    )


def test_ci_passes_the_same_env_var_name_the_twins_read():
    """The other end of the same wire. CI can be right about the URL and still
    set it under a name nothing reads — every twin skips, the job stays green,
    nobody the wiser.

    Parsed as YAML, not grepped. A substring search over the file also matches
    the workflow's own COMMENTS about this variable — of which there are now
    several, one of them a single character away from `NAME:` — so a future
    `# NARRANEXUS_MYSQL_TEST_URL: injected below` would satisfy this assertion
    permanently while the real env line was gone. A guard that prose can satisfy
    is worse than no guard: without one, somebody would still go and look.

    And the question asked is "can the process that runs pytest SEE this
    variable", not "does the name appear in some step's env". Those differ in
    both directions:

    * too wide — step-level `env` reaches only that step, so hanging it on
      `Install dependencies`, or splitting pytest into two steps and env-ing
      only one, keeps this green while the twins skip in the other;
    * too narrow — lifting the variable to `jobs.backend-tests.env` or the
      workflow's top-level `env` is a legitimate, tidier refactor that would
      turn this red for nothing. False red is how guards get "relaxed" away.

    So: find the steps that actually run pytest, and resolve each one's visible
    environment through Actions' own override order.
    """
    import yaml

    canonical = _canonical_env_name()
    workflow = yaml.safe_load(_WORKFLOW.read_text())

    # Readable failures instead of KeyError, same as _canonical_env_name(). A
    # renamed job (say, split into backend-tests-sqlite / -mysql) should tell the
    # reader what this file was locking, not just which dict key was absent.
    job = (workflow.get("jobs") or {}).get(_CI_JOB)
    if job is None:
        pytest.fail(
            f"{_WORKFLOW.relative_to(_REPO)} no longer has a {_CI_JOB!r} job — "
            f"that is where the MySQL service and {canonical} are wired, so the "
            f"dialect twins have nowhere left to run. Jobs seen: "
            f"{sorted((workflow.get('jobs') or {}).keys())}"
        )
    steps = job.get("steps") or []
    if not steps:
        pytest.fail(f"the {_CI_JOB!r} job has no steps")

    # `make test` is this repo's other way of running the suite (its target is
    # `uv run pytest tests/ -v`), so matching only "pytest" would false-red on a
    # perfectly good rewrite of the step.
    pytest_steps = [
        s for s in steps
        if re.search(r"pytest|make\s+test", str(s.get("run", "")))
    ]
    assert pytest_steps, (
        f"no step in the {_CI_JOB!r} job runs pytest any more — the job that "
        f"exists to execute the suite (and with it the dialect twins) stopped "
        f"doing so. Steps seen: {[s.get('name') for s in steps]}"
    )

    # Actions' precedence: workflow env < job env < step env.
    workflow_env = workflow.get("env") or {}
    job_env = job.get("env") or {}
    for step in pytest_steps:
        visible = {**workflow_env, **job_env, **(step.get("env") or {})}
        assert canonical in visible, (
            f"the pytest step {step.get('name')!r} cannot see {canonical} — the "
            f"dialect twins would skip in CI, which looks exactly like passing. "
            f"Visible env keys: {sorted(visible)}"
        )
        # The NAME is what this file locks; the URL itself is
        # environment-specific. But an empty value gates the twins off just as
        # effectively as a missing one.
        assert str(visible[canonical]).strip(), (
            f"the pytest step {step.get('name')!r} sets {canonical} to an empty "
            f"value"
        )

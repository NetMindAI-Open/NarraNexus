"""
@file_name: test_only_one_writer_of_agent_name.py
@author: NarraNexus
@date: 2026-08-19
@description: The invariant this whole area rests on, as a gate rather than a
request: every write to the `agents` row goes through a known place.

Renaming an agent is three writes — the column, the identity correction in its
Awareness profile, and the peer-discovery row — and for a while it had four
writers each remembering a different subset (Shenzhen round 2, P1). The fix was
to give the transaction one home. What KEEPS it one home was, until this test, a
`git grep` written into a mirror md for the next person to run by hand — and
that same md argues, correctly, that an expectation nobody enforces has already
failed twice here: the 2026-08-04 fix lasted ten days before a second writer
went around it.

So the allowlist lives HERE, where it runs. The mirror points at this file
rather than carrying its own copy — two lists drift, and the executable one is
the one that is true.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

# Anything that can put a value into the `agents` row: the repository helpers
# and the raw insert/update forms. Matching `update_agent(` also matches the
# route handler that shares the name, which is excluded below.
# ERE, not PCRE: `git grep -E` has no non-capturing groups, and a pattern it
# cannot parse returns NOTHING — which would make this gate pass by finding no
# writers at all. The `assert found` below exists for exactly that failure.
WRITE_PATTERN = (
    r'add_agent\(|update_agent\(|(insert|update)\(\s*"agents"|_ins\("agents"'
)

# Where a write is legitimate, as (path suffix, what it does, why it is allowed).
# Most creation paths set the name as the agent comes into existence, so there is
# no previous name to correct and no memory to have gone stale. The two metadata
# writers never touch agent_name.
#
# One exception, stated because a gate is only worth its reasons: bundle/importer
# DOES rename — it clamps, appends a dedupe suffix, and falls back on an empty
# name — and it copies instance_awareness row for row, so an imported agent can
# arrive declaring the name it had in the bundle. It is allowed here because the
# row is created rather than updated (the transaction's precondition is a row it
# may write), and it calls reconcile_identity_record after the awareness insert
# instead. Do not copy "creation paths need no correction" onto the next entry
# without checking whether that entry renames.
ALLOWED = {
    ("src/narranexus/platform/bootstrap/provision.py", "add_agent"),
    ("src/narranexus/platform/migration/applier.py", "add_agent"),
    ("backend/integrations/arena/arena_provisioning_service.py", "add_agent"),
    ("backend/integrations/arena/arena_provisioning_service.py", "update_agent"),
    ("src/narranexus/platform/bootstrap/profiles.py", "update_agent"),
    # Metadata only: re-renders the stored first-run greeting after a rename
    # (bootstrap_greeting); the name itself was written by agent_profile.
    ("src/narranexus/platform/bootstrap/greeting_seed.py", "update_agent"),
    ("backend/onboarding/provisioning.py", "update_agent"),
    ("backend/routes/manyfold/agents.py", 'insert("agents"'),
    ("src/narranexus/platform/bundle/importer.py", '_ins("agents"'),
    # The transaction itself — the one writer a rename may go through.
    (
        "src/narranexus/platform/agent_profile/_agent_profile_impl/profile_write.py",
        "update_agent",
    ),
}


def _writes_found() -> set[tuple[str, str]]:
    out = subprocess.run(
        ["git", "grep", "-nE", WRITE_PATTERN, "--", "backend", "src"],
        cwd=REPO, capture_output=True, text=True, encoding="utf-8",
    ).stdout
    found = set()
    for line in out.splitlines():
        path, _, text = line.split(":", 2)
        if path.endswith("repository/agent_repository.py"):
            continue  # the helpers' own definitions
        if "async def update_agent" in text:
            continue  # the HTTP route handler that shares the name
        for form in ("add_agent", "update_agent", 'insert("agents"', '_ins("agents"'):
            if form in text:
                found.add((path, form))
                break
    return found


def test_every_writer_of_the_agents_row_is_on_the_allowlist():
    """A new writer must be a deliberate act, not a discovery made after an
    incident. The previous enforcement was a command in a document."""
    found = _writes_found()
    assert found, "the scan found nothing — the pattern or the paths moved"

    unexpected = found - ALLOWED
    assert not unexpected, (
        "a new writer of the agents row appeared. If it can set agent_name, it "
        "must go through narranexus.platform.agent_profile — a rename is not a "
        "column write. If it genuinely cannot (creation, or metadata only), add "
        f"it here with the reason: {sorted(unexpected)}"
    )


def test_the_allowlist_has_no_entries_that_no_longer_exist():
    """Asserted as equality, not as a count: swapping one writer for another
    keeps the number the same and would pass a count check silently."""
    stale = ALLOWED - _writes_found()
    assert not stale, (
        f"the allowlist names writers that are gone — remove them so the list "
        f"keeps meaning something: {sorted(stale)}"
    )


# Which files may name the column at all. Narrower than ALLOWED above, and this
# is the check that actually discriminates: ALLOWED is keyed on (file, form), so
# a file already on it can gain a SECOND write of the same form invisibly —
# verified, by smuggling `update_agent(aid, {"agent_name": ...})` into
# bootstrap/profiles.py and watching the coarse check pass.
MAY_NAME_THE_COLUMN = {
    # Creation: the name is set as the agent comes into existence.
    "src/narranexus/platform/bootstrap/provision.py",
    "src/narranexus/platform/migration/applier.py",
    "backend/integrations/arena/arena_provisioning_service.py",
    "backend/routes/manyfold/agents.py",
    "src/narranexus/platform/bundle/importer.py",
    # The rename transaction.
    "src/narranexus/platform/agent_profile/_agent_profile_impl/profile_write.py",
    # The repository helpers the above go through.
    "src/narranexus/platform/repository/agent_repository.py",
}


def _sets_agent_name_in_a_write(path: str) -> bool:
    """Does a WRITE CALL in this file put a value into agent_name?

    Asked of the call, not of the file. The first version asked "does this file
    write the row AND mention agent_name anywhere", which flagged
    backend/onboarding/provisioning.py the day it arrived on dev: it generates a
    name, hands it to provision_new_agent (an allowed creation path), and its own
    update_agent writes agent_metadata only. Legitimate, and the gate cried wolf
    — which is how a gate stops being read.
    """
    import ast

    tree = ast.parse((REPO / path).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        name = getattr(fn, "attr", None) or getattr(fn, "id", None)
        if name not in ("add_agent", "update_agent", "insert", "update", "_ins"):
            continue
        # Any literal "agent_name" key, or an agent_name= keyword, INSIDE the
        # call — including one nested in a wrapper like normalize_agent_row_text.
        if any(k.arg == "agent_name" for k in node.keywords if k.arg):
            return True
        for sub in ast.walk(node):
            if isinstance(sub, ast.Constant) and sub.value == "agent_name":
                return True
    return False


def test_no_unlisted_file_writes_agent_name_into_the_row():
    """The discriminating half, asked of the write itself.

    A rename is not a column write, so a call that puts a value into agent_name
    is either the transaction or a creation path. Note what this cannot see: a
    dict built in a variable and passed by name (the transaction's own shape) —
    which is why the coarse allowlist above stays as the second net.
    """
    offenders = sorted(
        path
        for path, _form in _writes_found()
        if path not in MAY_NAME_THE_COLUMN and _sets_agent_name_in_a_write(path)
    )
    assert not offenders, (
        "these files write the agents row and name agent_name, without being "
        "the transaction or a creation path — route the rename through "
        f"narranexus.platform.agent_profile instead: {offenders}"
    )

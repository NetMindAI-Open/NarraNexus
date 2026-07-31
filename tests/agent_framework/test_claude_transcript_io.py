"""
@file_name: test_claude_transcript_io.py
@date: 2026-07-29
@description: Pin the transcript file's lifecycle: write before the spawn,
              delete after the turn, and never break a turn either way.

Two independent contracts live here.

**Fail-open.** The transcript is an optimization. If it cannot be written — the
config dir is read-only, the disk is full, the path is occupied by a directory —
the turn must still run, with history back in the prompt exactly as before. A
CLI-side optimization failing is not a reason to fail an agent run (铁律 #14),
so ``write_transcript`` returns None instead of raising.

**Cleanup that actually happens.** Deleting after the turn is what makes the
whole scheme safe: a transcript that lingers in the shared CLAUDE_CONFIG_DIR is
the cross-tenant read path ``executor_resume_hmac_secret`` exists to cover, and
it grows without bound. So removal must tolerate every state it can meet —
already gone, never created, unreadable parent — without raising, because it
runs from a ``finally`` where an exception would mask the real error.
"""
from __future__ import annotations

from pathlib import Path

from xyz_agent_context.agent_framework.adapters.claude.transcript import (
    remove_transcript,
    transcript_path,
    write_transcript,
)

_TURNS = [
    {"role": "user", "content": "question"},
    {"role": "assistant", "content": "answer"},
]
_SID = "11111111-2222-3333-4444-555555555555"


def _write(tmp_path: Path, turns=_TURNS, working="/w/agent"):
    return write_transcript(
        config_dir=tmp_path,
        working_path=working,
        session_id=_SID,
        history_entries=turns,
        cli_version="2.1.220",
        git_branch="main",
    )


# --- write ------------------------------------------------------------------


def test_writes_to_the_path_resume_reads(tmp_path):
    written = _write(tmp_path)
    assert written == transcript_path(tmp_path, "/w/agent", _SID)
    assert written.exists()
    lines = written.read_text().splitlines()
    # 2 conversation records + the trailing leaf pointer.
    assert len(lines) == 3


def test_creates_the_project_directory(tmp_path):
    """The per-project dir will not exist on a fresh config dir."""
    assert not (tmp_path / "projects").exists()
    assert _write(tmp_path) is not None


def test_empty_history_writes_nothing(tmp_path):
    """Nothing to resume — the caller must run a genuine first turn rather than
    hand the CLI a file it would reject."""
    assert _write(tmp_path, turns=[]) is None
    assert not (tmp_path / "projects").exists()


def test_history_of_only_blanks_writes_nothing(tmp_path):
    assert _write(tmp_path, turns=[{"role": "user", "content": "  "}]) is None


def test_write_failure_returns_none_instead_of_raising(tmp_path):
    """A directory sitting where the file belongs is the cheapest way to force a
    real OSError. The turn must survive it."""
    target = transcript_path(tmp_path, "/w/agent", _SID)
    target.parent.mkdir(parents=True)
    target.mkdir()  # now the "file" path is a directory
    assert _write(tmp_path) is None


def test_write_failure_on_unwritable_parent_returns_none(tmp_path):
    ro = tmp_path / "ro"
    ro.mkdir()
    ro.chmod(0o500)
    try:
        assert _write(ro) is None
    finally:
        ro.chmod(0o700)  # let pytest clean up


# --- remove -----------------------------------------------------------------


def test_remove_deletes_the_file(tmp_path):
    written = _write(tmp_path)
    assert written is not None
    remove_transcript(written)
    assert not written.exists()


def test_remove_tolerates_an_already_missing_file(tmp_path):
    """Runs from a finally; raising there would mask the turn's real error."""
    remove_transcript(transcript_path(tmp_path, "/w/agent", _SID))


def test_remove_tolerates_none(tmp_path):
    """The write may have returned None (fail-open); the same finally still
    calls remove."""
    remove_transcript(None)


def test_remove_never_raises_on_a_directory(tmp_path):
    d = tmp_path / "a-dir"
    d.mkdir()
    remove_transcript(d)
    assert d.exists()  # not deleted, but no exception either

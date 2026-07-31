"""
@file_name: transcript.py
@author: NarraNexus
@date: 2026-07-29
@description: Author the CLI session transcript that `--resume` reads, so
              conversation history stops riding inside the cache prefix.

The problem this solves
-----------------------
The prompt cache matches a strict byte prefix, ordered ``tools → system →
messages``. History injected into the system prompt therefore sits INSIDE the
prefix: every new turn changes those bytes and voids everything after them.
Agent-loop resume was the first fix — hand the CLI a session handle and history
travels in the CLI's own transcript instead — and it worked (measured on a live
agent: a second consecutive resume round's full-price input collapsed from
49,137 to 2,247, −95.4%).

But it left one cost. A COLD round still carries history in the system prompt
(63,603–66,023 chars observed) while a resume round does not (63,244), so those
two prompts differ, and the FIRST resume round after any cold round necessarily
misses from ``system`` onward — ~49K full-price tokens, every time a cold start
happens. Cold starts happen for reasons entirely outside the cache's control:
no handle yet, the narrative changed, a stale handle.

Authoring the transcript ourselves removes the cold/resume distinction
altogether. Every turn is a resume turn, so the system prompt is byte-identical
from the very first one, and history lives where it belongs — at the tail, after
the prefix.

What was measured before this existed
-------------------------------------
Against CLI 2.1.220, via a capture proxy (scripts under
``reference/self_notebook/experiments/``):

* **E4** — the CLI accepts a transcript we wrote, using a session id it never
  issued, and the injected turns reach the request's ``messages``.
* **E5** — ``tool_use`` / ``tool_result`` pairs also survive the round trip, so
  enriching history with tool records later is viable (not done here — see
  "Deliberately text-only" below).
* **E6** — build → use → delete → rebuild leaves the request bytes unchanged,
  which is what makes the delete-after-use lifecycle safe.
* **T0** — varying the session id per turn leaves ``tools`` / ``system`` /
  ``messages`` byte-identical. The envelope fields below are CLI bookkeeping and
  never reach the request, which is why a fresh id per turn is safe — and that
  in turn is what lets the file be deleted after every turn.

Load-bearing details, all observed rather than documented
---------------------------------------------------------
1. **Path.** ``<CLAUDE_CONFIG_DIR>/projects/<cwd-slug>/<session_id>.jsonl`` is
   the only file resume reads — a fresh config dir containing nothing else
   resumed fine, so there is no index to maintain.
2. **The file is a tree, not a log.** The CLI walks ``parentUuid`` backwards
   from the ``leafUuid`` named in a trailing ``last-prompt`` record. Correctly
   ordered records with a broken chain, or no leaf pointer, parse fine and
   resume nothing.
3. **Determinism is a cache requirement, not tidiness.** ``messages`` is cached
   too, so a rebuild from the same turns must be byte-identical. Every uuid is
   derived via ``uuid5`` and every timestamp from the record index — a
   ``now()`` here would look correct in any single run and quietly cost full
   price forever.
4. **``version`` couples this file to a CLI version.** The pin lives in
   ``cli_binary.PINNED_CLI_VERSION``; bumping it means re-running E4/E5/E6,
   because this format is an internal contract that can change underneath us.

What lives here vs in the adapter
---------------------------------
This module owns the whole per-turn lifecycle — decide, write, delete — behind
``prepare_transcript`` / ``remove_transcript``. The adapter calls two functions
and keeps one local. That split is deliberate: ``sdk.agent_loop`` was already
over the project's size guideline before this feature, and inlining the decision
there made a 672-line function that needed an 78-line re-indent to add a
``finally`` (which ``ast.parse`` caught me getting wrong on the first attempt).
The git-branch lookup lives here too, for the same reason — the transcript is
its only consumer.

Deliberately text-only
----------------------
History here is the same plain user/assistant text the system prompt carries
today (``ContextRuntime.extract_narrative_data`` builds it from each event's
``env_context["input"]`` and ``final_output``). Keeping the CONTENT identical
while changing only the CHANNEL is what makes the token effect attributable:
adding tool records at the same time would conflate two changes. E5 proved the
tool path works and ``events.event_log`` already stores what it needs
(``tool_call`` with its id and arguments, ``tool_output`` now with the matching
id) — enriching is a separate, independently measurable change.
"""

from __future__ import annotations

import json
import subprocess
import uuid as uuidlib
from functools import lru_cache
from pathlib import Path
from typing import Any

from loguru import logger

# Namespace for deriving record uuids. Fixed forever: changing it changes every
# derived uuid, which changes the transcript bytes for the same conversation.
_UUID_NAMESPACE = uuidlib.NAMESPACE_URL

# Base for derived timestamps. The CLI only needs them ordered and well-formed;
# it never compares them to real time. A constant base is what makes a rebuild
# byte-identical.
_TS_BASE = "2026-01-01"

_CONVO_ROLES = ("user", "assistant")

# Offset keeping derived promptIds from colliding with the record uuids, which
# come from the same (session_id, seq) namespace.
_PROMPT_ID_OFFSET = 10_000

# Fixed seed for assistant message ids, so they depend on POSITION only and not
# on the per-turn session id (see _assistant_record).
_MSG_ID_NAMESPACE_SEED = "narranexus-transcript-msg"

# Largest history a single transcript can hold while keeping timestamps ordered
# AND unique: minute wraps at 60, hour at 24 → 24 * 60. See _derived_timestamp.
MAX_ORDERED_RECORDS = 24 * 60

# `git rev-parse` on a healthy repo is milliseconds; the bound exists so a wedged
# git (network-backed worktree, fuse mount) degrades to an empty branch field
# instead of stalling a turn.
_GIT_BRANCH_TIMEOUT_S = 10.0


def cwd_slug(working_path: str | Path) -> str:
    """Directory name the CLI archives a project's transcripts under.

    Every character that is not alphanumeric becomes a single '-', one for one.
    Runs are NOT collapsed, so ``/Users/tc/.nexusagent`` yields
    ``-Users-tc--nexusagent`` — the slash and the dot each contribute a dash.

    Verified against directories Claude Code created itself under the production
    config dir, e.g.
    ``/Users/tc/.nexusagent/workspaces/user_tc/agent_9815c65a36a7`` →
    ``-Users-tc--nexusagent-workspaces-user-tc-agent-9815c65a36a7``.

    An earlier version split on '/' only. That works for a path of plain
    alphanumeric segments (the probe ran in this repo, so it passed) and fails
    silently on a real agent workspace, whose path carries a dot and two
    underscores: the file lands next door and the CLI answers "No conversation
    found". Widened after that failure — the character class, not the separator,
    is what the CLI keys on.
    """
    return "".join(c if c.isalnum() else "-" for c in str(working_path))


def transcript_path(
    config_dir: str | Path, working_path: str | Path, session_id: str
) -> Path:
    """The single file ``--resume <session_id>`` reads.

    ``session_id`` becomes a filename, so it must be a bare name: a separator or
    a dot-segment in it would place the transcript outside the project dir.
    Production only ever passes ``uuid4()``, but this function is module-public
    and the failure mode (writing into another project's dir, or over an
    arbitrary path) is worth making impossible by construction.
    """
    # Platform-independent on purpose: `Path(x).name` would let a backslash
    # through on POSIX (it is a legal filename character there) while the same
    # string traverses on Windows. The invariant we want is "contributes exactly
    # one path component", so check the characters rather than asking the local
    # flavour of Path what it thinks a separator is.
    if (
        not session_id.strip()
        or session_id in {".", ".."}
        or any(ch in session_id for ch in ("/", "\\", "\0"))
    ):
        raise ValueError(
            f"session_id must be a single path component, got {session_id!r}"
        )
    return (
        Path(config_dir)
        / "projects"
        / cwd_slug(working_path)
        / f"{session_id}.jsonl"
    )


def _derived_uuid(session_id: str, seq: int) -> str:
    return str(uuidlib.uuid5(_UUID_NAMESPACE, f"{session_id}/{seq}"))


def _derived_timestamp(seq: int) -> str:
    """Ordered, unique, and a pure function of the index.

    Both fields wrap (minute at 60, hour at 24), so the full cycle is
    ``MAX_ORDERED_RECORDS`` = 1440 entries — past that, timestamps repeat and
    ordering breaks. The platform caps the merged timeline at
    ``ChatModule.MERGED_HISTORY_MAX`` (30 today), so the bound is unreachable by
    a wide margin; ``test_derived_timestamps_stay_ordered_across_the_whole_supported_range``
    pins it so raising that cap trips a test rather than silently producing
    duplicate stamps.
    """
    return f"{_TS_BASE}T{seq // 60 % 24:02d}:{seq % 60:02d}:00.000Z"


def _user_record(envelope: dict[str, Any], session_id: str, seq: int,
                 content: str) -> dict[str, Any]:
    """A ``type: user`` record. ``content`` is a plain string here — the CLI
    writes user rows that way and assistant rows as block lists."""
    return {
        **envelope,
        "promptId": _derived_uuid(session_id, _PROMPT_ID_OFFSET + seq),
        "message": {"role": "user", "content": content},
        "permissionMode": "bypassPermissions",
        "promptSource": "sdk",
    }


def _assistant_record(envelope: dict[str, Any], seq: int,
                      content: str) -> dict[str, Any]:
    """An ``type: assistant`` record, content in block form.

    ``message.id`` derives from the POSITION only, never the session id, so the
    message payloads for a given conversation are identical no matter which
    session id the turn uses. T0 showed the CLI discards this id when rebuilding
    the request, but not depending on that keeps the invariant ours to enforce
    rather than the CLI's to preserve.
    """
    return {
        **envelope,
        "effort": "high",
        "message": {
            "id": f"msg_{_derived_uuid(_MSG_ID_NAMESPACE_SEED, seq)[:8]}",
            "type": "message",
            "role": "assistant",
            "content": [{"type": "text", "text": content}],
            "stop_reason": "end_turn",
            "stop_sequence": None,
            "usage": {
                "input_tokens": 0,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
                "output_tokens": 1,
            },
        },
    }


def build_records(
    history_entries: list[dict[str, Any]],
    *,
    session_id: str,
    working_path: str,
    cli_version: str,
    git_branch: str,
) -> list[dict[str, Any]]:
    """Turn ordered history entries into transcript records.

    ``history_entries`` is exactly what ``materializer.split_for_argv`` already
    produces — ``{"role": "user"|"assistant", "content": str}`` oldest first —
    so this consumes the platform's existing history rather than introducing a
    second source that could drift from it.

    Returns ``[]`` for an empty history: there is nothing to resume, and the
    caller must run a genuine first turn instead of writing a file the CLI would
    reject. Blank content and non-conversation roles are dropped — ``system``
    rows are split out upstream, and anything else arriving here is a bug
    elsewhere that must not become a malformed record.
    """
    usable = [
        e
        for e in history_entries
        if e.get("role") in _CONVO_ROLES and str(e.get("content") or "").strip()
    ]
    if not usable:
        return []

    # The annotation says str, but nothing enforces it and a Path landing in the
    # ``cwd`` field would only fail later, inside json serialization.
    working_path = str(working_path)

    records: list[dict[str, Any]] = []
    parent: str | None = None
    leaf = ""

    for seq, entry in enumerate(usable):
        role = entry["role"]
        content = entry["content"]
        this_uuid = _derived_uuid(session_id, seq)
        envelope: dict[str, Any] = {
            "parentUuid": parent,
            "isSidechain": False,
            "type": role,
            "uuid": this_uuid,
            "timestamp": _derived_timestamp(seq),
            "userType": "external",
            "entrypoint": "sdk-py",
            "cwd": working_path,
            "sessionId": session_id,
            "version": cli_version,
            "gitBranch": git_branch,
        }
        records.append(
            _user_record(envelope, session_id, seq, content)
            if role == "user"
            else _assistant_record(envelope, seq, content)
        )
        parent = this_uuid
        leaf = this_uuid

    # The leaf pointer. Without it the chain exists but the CLI has no entry
    # point, and resume silently continues nothing.
    records.append({
        "type": "last-prompt",
        "lastPrompt": usable[-1]["content"],
        "leafUuid": leaf,
        "sessionId": session_id,
    })
    return records


def render(records: list[dict[str, Any]]) -> str:
    """Serialize to JSONL. ``sort_keys`` is part of the determinism contract —
    dict insertion order must not decide the bytes."""
    return "".join(
        json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in records
    )


def write_transcript(
    *,
    config_dir: str | Path,
    working_path: str,
    session_id: str,
    history_entries: list[dict[str, Any]],
    cli_version: str,
    git_branch: str,
) -> Path | None:
    """Materialize the transcript and return its path, or None.

    None means "run this turn the old way" — there was nothing to resume, or the
    file could not be written. Both are non-events: the transcript is an
    optimization, and the caller keeps history in the prompt when it is absent.
    Raising here would turn a full disk or a read-only config dir into a failed
    agent run, which 铁律 #14 forbids.
    """
    records = build_records(
        history_entries,
        session_id=session_id,
        working_path=working_path,
        cli_version=cli_version,
        git_branch=git_branch,
    )
    if not records:
        return None

    path = transcript_path(config_dir, working_path, session_id)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(render(records), encoding="utf-8")
    except Exception as e:  # noqa: BLE001 — fail-open is the contract
        # Deliberately broader than OSError. The obvious failures are IO (full
        # disk, read-only dir, a directory occupying the path), but a
        # non-serializable value reaching ``render`` would raise TypeError and
        # escape a narrower clause — turning an optimization into a failed agent
        # run, which 铁律 #14 forbids. Every failure degrades to
        # history-in-prompt, and says so loudly rather than silently.
        logger.warning(
            f"[TRANSCRIPT] write failed ({type(e).__name__}: {e}) — "
            f"falling back to history-in-prompt for this turn"
        )
        return None
    return path


@lru_cache(maxsize=32)
def _git_branch_of_repo(working_path: str) -> str:
    """Branch name for a path already known to be a git work tree.

    Cached because this spawns a subprocess and runs on the hot turn path, for a
    field the CLI only uses for its own display. Bounded so a long-lived process
    with many agent workspaces cannot grow it without limit.

    Caching a branch name means a checkout inside a live workspace goes
    unnoticed until the process restarts. Accepted: the value is cosmetic, and
    the alternative is a subprocess per turn forever.
    """
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            cwd=working_path,
            timeout=_GIT_BRANCH_TIMEOUT_S,
        )
    except Exception:  # noqa: BLE001 — a missing git or an odd cwd is not an error here
        return ""
    return out.stdout.strip() if out.returncode == 0 else ""


def working_git_branch(working_path: str) -> str:
    """Branch for the transcript's ``gitBranch`` field, or "" when not a repo.

    The ``.git`` probe is a stat, not a subprocess, and it short-circuits the
    common case: an agent workspace is normally NOT a git repo, so without this
    every turn would pay a `git rev-parse` that is guaranteed to fail. It also
    keeps the cached branch lookup from memoizing a "" that came from "not a
    repo yet" — if the directory becomes a repo, the stat notices.
    """
    try:
        if not (Path(working_path) / ".git").exists():
            return ""
    except OSError:
        return ""
    return _git_branch_of_repo(working_path)


def prepare_transcript(
    history_entries: list[dict[str, Any]],
    *,
    config_dir: str,
    working_path: str,
    cli_version: str,
) -> tuple[str | None, Path | None]:
    """Decide-and-write for one turn: returns ``(session_id, path)``.

    ``(None, None)`` means run the turn the old way — the gate is off, there is
    no history to resume, or the write failed. The caller keeps history in the
    prompt in every one of those cases.

    A FRESH uuid4 per turn, never derived from agent or narrative. The file is
    deleted when the turn ends, so nothing durable is left in the shared
    ``CLAUDE_CONFIG_DIR``; a derived, guessable id would undo that. T0 measured
    that transcript envelope fields never reach the request, so varying the id
    per turn costs no cache. It also makes concurrent turns of the same agent
    collision-free — which is the only reason the old handle mechanism needed a
    process-wide lease.
    """
    from xyz_agent_context.settings import settings

    if not settings.claude_synthetic_transcript_enabled or not history_entries:
        return None, None

    session_id = str(uuidlib.uuid4())
    path = write_transcript(
        config_dir=config_dir,
        working_path=working_path,
        session_id=session_id,
        history_entries=history_entries,
        cli_version=cli_version,
        git_branch=working_git_branch(working_path),
    )
    if path is None:
        return None, None
    logger.info(
        f"[TRANSCRIPT] wrote {len(history_entries)} history entries "
        f"→ session={session_id[:12]} ({path.name})"
    )
    return session_id, path


def remove_transcript(path: Path | None) -> None:
    """Delete the transcript. Never raises.

    Called from a ``finally``, so an exception here would mask whatever actually
    ended the turn. It also has to tolerate every state it can legitimately
    meet: ``None`` (the write failed or was skipped), already gone, or a path
    that is not a file.

    Why deleting matters, stated precisely — the ordering of these two controls
    is easy to get backwards. The PRIMARY reason removing the executor's resume
    HMAC was safe is that ``/agent-loop``'s body no longer carries a
    ``resume_session_id`` field at all: no caller can name a transcript, so
    there is no capability left to authenticate. Deleting the file is
    defence-in-depth on top of that — it bounds the window in which the
    conversation exists in plaintext inside a ``CLAUDE_CONFIG_DIR`` that is
    shared across tenants, for anyone who has filesystem access (a different
    threat model, which neither control addresses).
    """
    if path is None:
        return
    try:
        Path(path).unlink(missing_ok=True)
    except Exception as e:  # noqa: BLE001 — the docstring's promise is absolute
        # Broader than OSError on purpose. The expected failures are IO (a
        # directory at that path, a vanished mount, a permission change
        # mid-turn), but ``Path(path)`` also raises TypeError on a non-path
        # value. The signature says ``Path | None``, yet this runs from a
        # ``finally`` where ANY exception would mask whatever actually ended the
        # turn — so "never raises" has to be unconditional, not
        # best-effort-for-the-errors-we-thought-of.
        logger.warning(f"[TRANSCRIPT] cleanup failed for {path}: {type(e).__name__}: {e}")

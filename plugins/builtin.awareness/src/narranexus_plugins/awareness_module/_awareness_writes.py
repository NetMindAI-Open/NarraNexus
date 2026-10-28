"""
@file_name: _awareness_writes.py
@author:
@date: 2026-08-10
@description: The Awareness half of a rename — the identity record the agent
reads about itself — plus the MCP tool's renderer.

The rename TRANSACTION itself lives in ``narranexus.platform.agent_profile``
(moved 2026-08-18; it writes the `agents` row and the peer directory, neither of
which is an Awareness concern). What stays here is what genuinely belongs to
Awareness, because it is written into ``instance_awareness``:

* the ``## Identity Changes (platform record)`` section — its constants, the two
  note builders, the reader that lets a new record supersede a stale one, and
  the merge that keeps the rest of the profile intact;
* ``record_identity_change`` / ``reconcile_identity_record``, the two writers
  the transaction calls (public, not underscore-private: they are called across
  a package boundary now);
* ``retire_self_name``, which rewrites the agent's own name line — the one place
  the platform edits agent-authored text, and only ever the name (rule #15: a
  machine-knowable fact is derived, not left to the model to remember);
* ``update_agent_profile_from_args``, the MCP tool's renderer, which calls the
  transaction and formats its result into the sentence the tool has always
  returned. DirectStore (local) and the backend twin route (cloud) both call it,
  so the two stay byte-identical.

Why any of this exists: P1 section 02 ①, prod evt_1f9c6680 — writing
`agents.agent_name` alone left the agent's free-text long-term memory still
asserting the old identity, and the old name may by then belong to a different
agent of the same owner. A column change cannot move a few thousand words of
narrative; a record the agent reads every turn can.
"""
from __future__ import annotations

import re
from typing import List, Optional

from loguru import logger

from narranexus.platform.repository import (
    InstanceAwarenessRepository,
    InstanceRepository,
)

# Where a rename records itself inside the Awareness profile. The profile is
# injected verbatim into the system prompt every turn, so this is the one place
# a correction is guaranteed to be read. (See module docstring for the incident.)
IDENTITY_CHANGE_SECTION = "## Identity Changes (platform record)"

# Keep the section bounded: renames are rare, but an unbounded log would eat
# the context window it lives in. Newest entries win.
MAX_IDENTITY_CHANGE_ENTRIES = 5


def _for_note(name: str) -> str:
    """A name rendered so the record stays readable back.

    The whole supersede mechanism round-trips through ``You are 「X」``, but a
    name is only ``strip()``-ed on the way into the row — it may legally contain
    ``」`` or a newline. ``」`` truncates the read-back, so every later call sees
    "the record disagrees with the row", rewrites the profile and logs a
    correction that never converges. A newline is worse: the tail becomes a
    separate ``- `` entry, and if it happens to contain the marker phrase it is
    read as the current assertion — a forged platform record that then prunes
    the real one.

    BOTH brackets are escaped, not just the closing one. A name may contain the
    marker phrase itself — ``小绿\n- 2026: You are 「冒充」`` — and with only ``」``
    escaped that forged assertion still parses, and being earlier in the line it
    wins: the record then claims a name its owner chose for it. Neutralising
    ``「`` too means no complete ``You are 「…」`` can occur inside a name, so the
    only one carrying real brackets is the one this builder wrote.

    Escaped rather than rejected at the write edge on purpose: refusing these
    names would leave any existing row holding one permanently unrenameable,
    which is the same class of trap as the normalization repair.
    """
    return (
        (name or "")
        .replace("\n", " ")
        .replace("\r", " ")
        .replace("「", "﹁")
        .replace("」", "﹂")
    )


def build_identity_change_note(
    old_name: str, new_name: str, when: Optional[str] = None
) -> str:
    """One line recording a rename, written for the agent to read about itself.

    States both names and explicitly RETIRES the old one: the failure mode is
    memory that keeps asserting the previous identity, and "you are now X" does
    not contradict "I am Y" as far as a model is concerned — especially when
    the old name may now belong to a different agent of the same owner.
    """
    if when is None:
        from datetime import datetime, timezone
        when = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    old_name, new_name = _for_note(old_name), _for_note(new_name)
    return (
        f"- {when}: renamed by your creator from 「{old_name}」 to 「{new_name}」. "
        f"You are 「{new_name}」. 「{old_name}」 is no longer your name — if it "
        f"appears in your memories or past conversations, that is history, and "
        f"it may now belong to a different agent."
    )


# Every note states the current name exactly once, in this shape. The builder
# below writes it and ``identity_note_asserts`` reads it back, deliberately
# adjacent: the moment those two drift, reconciliation silently stops finding
# stale records and the section goes back to contradicting the row.
_ASSERTS_NAME = re.compile(r"You are 「([^」]+)」")


def identity_note_asserts(entry: str) -> Optional[str]:
    """The name an identity entry tells the agent it currently has."""
    m = _ASSERTS_NAME.search(entry or "")
    return m.group(1) if m else None


def build_identity_reconciliation_note(
    current_name: str, stale_name: str, when: Optional[str] = None
) -> str:
    """Correct a platform record that names the wrong agent name.

    Distinct wording from a rename on purpose: nothing was renamed just now, and
    telling the agent it "was renamed" when its owner did no such thing is a
    false statement in the one section whose whole value is that the agent
    believes it. This says only what is true — the record was wrong, and here is
    the name the platform holds.
    """
    if when is None:
        from datetime import datetime, timezone
        when = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    # "You are 「X」" is not a wording choice — it is the shape
    # ``identity_note_asserts`` reads, and therefore what lets this note
    # supersede the stale one it is correcting. The first draft said "Your name
    # is 「X」", which parsed as asserting nothing, so the record it was written
    # to replace survived beside it. Any future note MUST contain this phrase;
    # ``test_every_identity_note_states_the_current_name_readably`` enforces it.
    current_name, stale_name = _for_note(current_name), _for_note(stale_name)
    return (
        f"- {when}: platform record corrected. You are 「{current_name}」. "
        f"An earlier record here said 「{stale_name}」 — that record was stale, "
        f"not a rename, and 「{stale_name}」 is not your name."
    )


def _split_record_section(profile: str):
    """``(text before the record, its bullet entries, text after it)``.

    The section ends at the NEXT ``## `` heading, not at end-of-text: the model
    rewrites the whole profile in the prescribed structure, so this section
    routinely sits in the MIDDLE, and an early version that treated everything
    after the marker as belonging to it silently dropped the sections below.

    One copy. This walk was written out three times — in the merge, in the
    rewrite carry-over, and in the record reader — and three copies of "where
    does this section end" is how one of them starts eating the section below.
    """
    if IDENTITY_CHANGE_SECTION not in (profile or ""):
        return (profile or ""), [], ""
    head, _, rest = (profile or "").partition(IDENTITY_CHANGE_SECTION)
    lines = rest.splitlines()
    cut = next(
        (i for i, ln in enumerate(lines) if ln.lstrip().startswith("## ")),
        len(lines),
    )
    entries = [ln.strip() for ln in lines[:cut] if ln.strip().startswith("- ")]
    return head.rstrip(), entries, "\n".join(lines[cut:]).strip("\n")


def merge_identity_change_note(profile: str, note: str) -> str:
    """Append ``note`` to the profile's identity-change section.

    Appends — never rewrites the rest of the profile (the agent's observations
    about its owner are not ours to edit, and losing them to a rename would be
    a worse bug than the one this fixes). Keeps a single section and the last
    ``MAX_IDENTITY_CHANGE_ENTRIES`` entries.

    The section is delimited by the NEXT ``##`` heading, not by end-of-text.
    ``update_awareness`` has the model rewrite the whole profile in the
    prescribed structure, so this section routinely sits in the MIDDLE; the
    first implementation treated everything after the marker as belonging to
    it and silently dropped the sections below on the next rename (review,
    2026-08-05). Whatever follows is carried through untouched.
    """
    head, entries, tail = _split_record_section((profile or "").rstrip())
    if not entries:
        entries = [note]
    else:
        # Drop entries this note supersedes — every one that tells the agent it
        # is called something other than what this note says it is called. The
        # incident proved a single platform-voiced line is enough for the agent
        # to introduce itself by the wrong name and defend it; leaving three
        # mutually exclusive ones and trusting the model to prefer the last is
        # the same bet with worse odds. Nothing is lost: the surviving note
        # names the previous name itself ("renamed from X to Y").
        now_called = identity_note_asserts(note)
        if now_called:
            entries = [
                e for e in entries
                if identity_note_asserts(e) in (None, now_called)
            ]
        entries.append(note)

    entries = entries[-MAX_IDENTITY_CHANGE_ENTRIES:]
    section = f"{IDENTITY_CHANGE_SECTION}\n" + "\n".join(entries) + "\n"

    parts = [p for p in (head, section.rstrip(), tail) if p]
    return "\n\n".join(parts) + "\n"


def carry_over_platform_record(previous: str, rewritten: str) -> str:
    """Keep the platform's identity record across a model rewrite of the profile.

    ``update_awareness`` hands the model the whole document and takes back
    whatever it returns, and the format that tool prescribes lists four sections
    — none of them this one. So the correction the rename transaction wrote
    survived exactly until the next time the model reorganised its profile,
    which §5 and the tool's own "When to Update" actively encourage. The agent
    then went back to a stale self-name line with nothing contradicting it, and
    nothing recorded that it had ever been fixed.

    Asking the model to preserve the section would make prompt text the
    mechanism, which rule #15 forbids: a machine-knowable fact is derived. The
    section is re-attached here instead. A rewrite that KEPT the section is left
    alone — the model may legitimately have moved it.
    """
    if IDENTITY_CHANGE_SECTION in (rewritten or ""):
        return rewritten
    _, entries, _ = _split_record_section(previous)
    if not entries:
        return rewritten
    logger.info(
        "[identity] re-attaching the platform record a profile rewrite dropped"
    )
    section = f"{IDENTITY_CHANGE_SECTION}\n" + "\n".join(entries)
    return (rewritten or "").rstrip() + "\n\n" + section + "\n"


def _note_is_readable(note: str, expected_name: str) -> bool:
    """Refuse to file a record whose own assertion cannot be read back.

    The supersede step keys on ``identity_note_asserts``; a note it cannot parse
    is appended beside the record it was meant to replace, leaving the prompt
    holding both. That already happened once, when the reconciliation note was
    first phrased "Your name is 「X」". A wording change should fail loudly here
    rather than silently stop superseding.
    """
    got = identity_note_asserts(note)
    if got == _for_note(expected_name):
        return True
    logger.warning(
        f"[identity-note] refusing to file a record that asserts {got!r} "
        f"instead of {expected_name!r} — the note wording no longer round-trips"
    )
    return False


# A self-name declaration: an optional bullet, a "name" label in either
# language, a separator, then the value. Deliberately narrow — it must not match
# prose. The agent writes this line itself (``update_awareness`` has the model
# rewrite the whole profile in the prescribed structure), so the label wording
# is the model's, and this covers the spellings the prompt's structure produces.
_SELF_NAME_LINE = re.compile(
    r"^(?P<head>[-*\s]*(?:名称|名字|姓名|Name|name|NAME)\s*[：:]\s*)(?P<value>.*)$"
)


# What may follow the name on a declaration line: nothing, or a separator that
# starts a description. A SPACE does not qualify — `- name: 美食家 是 owner 最近
# 常去的那家店` is an owner observation that merely opens with the marker, and
# rewriting it would be the content loss this whole area promises never to cause.
# Which characters may end a name, and the distinction is the whole fix. A
# DESCRIPTION OPENER (`；，、。(`) starts the prose after a name — the shape the
# prescribed template produces and the one prod actually held. A hyphen, slash or
# dot is different: those live INSIDE names (小绿-2, netmind/ops, v1.2), so
# treating one as a boundary is where every corruption came from —
# `美食家-资深；…` cut at the hyphen, yielding the never-existing `小绿-2-资深`,
# and `小绿-2` read as `小绿` and got re-prefixed on every call.
#
# Weak characters are therefore never boundaries: the name simply runs through
# them to the first opener. Owner's call, 2026-08-20 — guessing is allowed, but
# only at one kind of separator.
_NAME_ENDERS = ("", "；", ";", "，", ",", "、", "。", "(", "（")

# Only the agent's OWN section may be edited. Everything else in this profile
# belongs to the owner: the prescribed template puts their preferences and
# observations in sections 1-3, and the model routinely writes further sections
# of its own. An owner recorded as `- 姓名：张三` anywhere outside the identity
# section, for an agent that also happened to be called 张三, would otherwise
# have THEIR name replaced by the agent's new one — silently and unrecoverably,
# since instance_awareness is overwritten by upsert and a log line is not a
# record.
#
# Matched POSITIVELY, and the reason is that the two failures are not
# symmetric. Miss the identity section (the model renamed its heading) and a
# stale self-name line survives: visible, recoverable, and the identity record
# below it still corrects the agent. Edit an owner's line and the value is gone
# for good. So the default is "do not touch", and a skipped candidate is logged
# rather than silently dropped.
#
# An earlier cut excluded sections 1/2/3 instead, which left every section the
# model invented — including the `## 5. Owner observations` this change's own
# fixtures use — inside the editable region.
_IDENTITY_SECTION = re.compile(
    r"^\s*#{1,6}\s.*(?:role|identity|身份|角色|自我认知)", re.IGNORECASE
)
# Any ATX heading, with its level captured. `##` alone was matched before, so a
# profile the model organised with H1 or H3 never left the preamble — where
# `editable` defaults to True — and the positive identity match was inert on that
# whole class of document.
_ANY_HEADING = re.compile(r"^\s*(?P<hashes>#{1,6})\s+")


class _AmbiguousSelfName(Exception):
    """Raised when a self-name line cannot be rewritten without guessing.

    An exception rather than a sentinel return so no caller can take the
    unchanged profile for a successful retirement — which is precisely how this
    degradation stayed invisible.
    """

    def __init__(self, profile: str) -> None:
        super().__init__("ambiguous self-name rewrite refused")
        self.profile = profile


def _ends_the_name(rest: str) -> bool:
    return rest == "" or rest[0] in _NAME_ENDERS


def _scan(profile: str):
    """Yield ``(line, is_the_agents_own_section)`` for the whole profile.

    THE definition of "which part of this document is the agent's", used by
    every reader and the one writer. It said it was shared before it was: the
    retirement kept a hand-rolled copy of the same walk while this docstring
    claimed otherwise, which is how the two answers start to differ and one of
    them begins editing the owner's text.
    """
    # Only a heading at the SAME level or higher may end a section. The template
    # puts `### Role Definition` inside `## 4. Role and Identity`, so resetting on
    # every heading would close the identity section at its own subsection and
    # retirement would silently stop — the failure this whole area is built to
    # avoid. A deeper heading stays inside whatever it is under.
    editable, level = True, 0
    for line in (profile or "").splitlines():
        m = _ANY_HEADING.match(line)
        if m:
            depth = len(m.group("hashes"))
            if depth <= level or level == 0:
                editable = bool(_IDENTITY_SECTION.match(line))
                level = depth
            elif _IDENTITY_SECTION.match(line):
                # A deeper heading may OPEN an identity subsection inside a
                # section that was not one, but never close one. When it does
                # open one, record ITS level — the first version left `level`
                # pinned at the document's first heading, so under
                # `# Agent Awareness Profile` an `## 4` opened here could never
                # be closed by `## 5` (2 <= 1 is never true) and everything
                # after the identity section became editable. Only when opening
                # from a non-editable state: `### Role Definition` inside an
                # already-editable `## 4` must not raise the bar, or its sibling
                # `### Background` (no keyword, 3 <= 3) would close the section.
                if not editable:
                    level = depth
                editable = True
        yield line, editable


def _identity_section_lines(profile: str):
    """Just the agent's own lines."""
    return (line for line, editable in _scan(profile) if editable)


def _declares(value: str, name: str) -> bool:
    """Does ``value`` declare exactly ``name`` (optionally opening a description)?

    One definition. The same test was spelled two ways — `A or (B and C)` in the
    reader and `C and B` in the writer — and this is the core predicate of the
    whole area, so a reader's first job should not be confirming the two agree.
    """
    return value == name or (
        value.startswith(name) and _ends_the_name(value[len(name):])
    )


def _boundary_was_found(profile: str, inferred: str) -> bool:
    """Did the inference actually delimit ``inferred`` inside the line?

    True when the line's value is longer than the inferred name — i.e. a strong
    opener cut it. ``_name_part`` returns the WHOLE value when it finds no
    opener, and that value, fed back as a "known" old name, trivially satisfies
    every check the rewrite makes: the entire text after 名称： was replaced by
    the current name. Fourth corruption from this inference and the only one
    that deleted the agent's own words rather than mangling a name.
    """
    for line in _identity_section_lines(profile):
        m = _SELF_NAME_LINE.match(line)
        if m:
            return len(m.group("value").strip()) > len(inferred)
    return False


def _name_part(value: str) -> str:
    """The leading run of a declaration's value, up to the first separator.

    A guess, and it is only sound where the caller has already established that
    the value does NOT hold the current name — see ``declared_self_name``.
    """
    cut = min(
        (value.index(e) for e in _NAME_ENDERS if e and e in value),
        default=len(value),
    )
    return value[:cut].strip()


def declared_self_name(profile: str, current_name: str = "") -> Optional[str]:
    """The name the agent's own section says it has, if it says one.

    ``current_name`` is not decoration. A name may contain the very characters
    used to find where a name ends — an agent called ``小绿-2`` was read as
    ``小绿``, disagreed with its own row on every call, and each "correction"
    prefixed the name again: 小绿-2 → 小绿-2-2 → 小绿-2-2-2. Checking the value
    against the name we already know, before guessing a boundary at all, is what
    removes the class rather than one instance of it.
    """
    for line in _identity_section_lines(profile):
        m = _SELF_NAME_LINE.match(line)
        if not m:
            continue
        value = m.group("value").strip()
        # Compared in the stored (escaped) form, because that is what the
        # profile holds — see retire_self_name.
        current = _for_note((current_name or "").strip())
        if current and _declares(value, current):
            return current  # already the current name; nothing to guess
        return _name_part(value)
    return None


def _has_weak_separator(value: str) -> bool:
    """Does ``value`` contain whitespace or a character that lives inside names?

    A bare-name declaration reached without finding an opener is only really a
    name if it looks like one. `美食家 是 owner 最近常去的那家店` is the whole
    line, not a name, and its spaces are what say so.
    """
    return any(ch.isspace() for ch in value) or any(
        ch in value for ch in ("-", "—", "/", "|", ".")
    )


def _declares_bare_name(profile: str, name: str) -> bool:
    """Is the identity section's declaration exactly ``name`` and nothing more?

    Then no boundary had to be inferred to obtain it, and a rewrite is exact.
    """
    for line in _identity_section_lines(profile):
        m = _SELF_NAME_LINE.match(line)
        if m:
            return m.group("value").strip() == name
    return False


def retire_self_name(
    profile: str, old_name: str, new_name: str, *, inferred: bool = False
) -> str:
    """Rewrite the agent's own "my name is X" line to the name it now has.

    Why the platform touches agent-authored text at all, when 2026-08-04
    established that it must not: that principle protects the agent's
    OBSERVATIONS — what it has learned about its owner — and losing those to a
    rename would be worse than the bug being fixed. Its own name is a different
    thing: machine-knowable, owned by the ``agents`` row, and 铁律 #15 says a
    machine-knowable fact is derived, never left to the model to remember.

    Measured 2026-08-19 UTC: with the row, BasicInfo, the identity record and the
    peer directory all corrected, a real two-turn run still answered with the
    old name — the profile said ``- 名称：美食家`` above the correction, and the
    model followed the line that came first.

    Only the VALUE of a name-declaration line is touched, and only where the old
    name IS that value (optionally followed by a separator that opens a
    description). An owner observation that merely starts with the marker — and
    then keeps talking — is left exactly as written.

    Scoped to the agent's own section, matched positively (a heading naming
    role / identity / 身份 / 角色, or the preamble before any section). Anything
    else is the owner's: their preferences live in the template's sections 1-3
    and the model writes further sections of its own, so a name declared there
    is the OWNER's name. Missing the identity section leaves a stale line —
    visible, recoverable, and still contradicted by the record below it —
    whereas editing an owner's line cannot be undone.

    Known limit, stated rather than papered over: the label set is the spellings
    this prompt's structure produces in Chinese and English. A profile the model
    wrote in another language keeps its stale self-name line, and the identity
    record below it is then the only correction. Widening the set is easy; making
    it exhaustive is not, which is why the record is the backstop and this is the
    optimisation.
    """
    old, new = (old_name or "").strip(), (new_name or "").strip()
    if not old or old == new:
        return profile or ""

    # An old name the caller INFERRED from the line may only drive a rewrite when
    # the inference actually found a boundary — a strong opener — or the value is
    # a bare name. Without one, `_name_part` hands back the whole value, which
    # then matches everything the loop below tests and replaces the agent's own
    # words with its name. The rename path passes `inferred=False`: there the old
    # name is read off the row, and a line that merely starts with it is filtered
    # out by the declaration test as before.
    #
    # Refusing RAISES so no caller can read the untouched profile as a completed
    # retirement — that silence is what round 12 rejected and what let this one
    # report `identity_record_updated: true` while deleting text.
    if inferred:
        # A bare-name declaration is only safe when the value IS a name — and
        # when no opener was found, the "inferred name" IS the whole value, so
        # _declares_bare_name is trivially true and cannot be the escape hatch.
        # Computed here and not above: both predicates walk the whole profile,
        # and the rename path (inferred=False, the common one) never reads them
        # — two dead scans there also read as if they mattered to both paths,
        # which is the misreading that kept a dead except alive for a round.
        boundary = _boundary_was_found(profile or "", old)
        bare = _declares_bare_name(profile or "", old) and not _has_weak_separator(old)
        if not boundary and not bare:
            logger.warning(
                f"[identity] refusing to retire from an inferred name with no "
                f"boundary ({old!r}); the line keeps its text and the record "
                f"contradicts it instead"
            )
            raise _AmbiguousSelfName(profile or "")

    # Before the first `##`, there is no section to be wrong about: that is the
    # preamble, the default profile, and every bare fragment. Editable.
    profile = profile or ""
    # No prefix guard here any more. It existed because a hyphen could be
    # mistaken for the end of a name, making `小绿` vs `小绿-2` undecidable — and
    # round 13 removed that premise: weak characters are no longer boundaries, so
    # `- 名称：小绿-3` simply does not declare `小绿` and is never a candidate.
    # What the guard did instead was reject renames that ARE exact (小绿 → 小绿2,
    # Ann → Anna — appending is one of the most ordinary shapes) and report the
    # identity memory as not updated. A signal that cries wolf on the common case
    # is not a signal, which is the same reason round 12 rejected the previous
    # version of this field.
    out = []
    for line, editable in _scan(profile):
        m = _SELF_NAME_LINE.match(line)
        value = m.group("value").strip() if m else ""
        # One predicate, asked once: "is this line a declaration of the old
        # name". Where it applies is the separate question below.
        is_old_name_decl = bool(m and _declares(value, old))
        if is_old_name_decl and not editable:
            # A declaration of the old name outside the agent's own section. Not
            # touched — but said out loud, so "the heading drifted and retirement
            # quietly stopped" is diagnosable instead of invisible.
            logger.info(
                f"[identity] leaving a name line outside the identity section "
                f"alone: {line.strip()!r}"
            )
        if is_old_name_decl and editable:
            # Written through the SAME escape the record uses. A name may
            # contain a newline, and pasting it raw into a single-line
            # declaration splits the line in two: the next read sees only the
            # first half, disagrees with the row, and "corrects" it forever.
            rewritten = m.group("head") + _for_note(new) + value[len(old):]
            # The one place the platform edits an agent's own long-term memory.
            # An edit nobody can see afterwards is an edit nobody can undo:
            # instance_awareness is overwritten by upsert, so the previous line
            # exists in no other record once this returns.
            logger.info(
                f"[identity] retiring self-name line: {line.strip()!r} -> "
                f"{rewritten.strip()!r}"
            )
            out.append(rewritten)
        else:
            out.append(line)
    return "\n".join(out) + ("\n" if profile.endswith("\n") else "")


async def record_identity_change(
    db, agent_id: str, old_name: str, new_name: str
) -> Optional[bool]:
    """File the rename into the agent's Awareness profile.

    Best-effort by design: the name change itself has already been written,
    and failing the tool afterwards would tell the model the rename did not
    happen. A missing note degrades to the old (buggy) behaviour, which is
    strictly better than reporting a false failure.
    """
    try:
        instances = await InstanceRepository(db).get_by_agent(
            agent_id=agent_id, module_class="AwarenessModule"
        )
        if not instances:
            # Nothing to correct: no Awareness instance means no identity
            # memory that could be asserting the old name.
            logger.debug(
                f"record_identity_change: no AwarenessModule instance for "
                f"{agent_id}; nothing to correct"
            )
            return None
        instance_id = instances[0].instance_id
        awareness_repo = InstanceAwarenessRepository(db)
        current = await awareness_repo.get_by_instance(instance_id)
        profile = (current.awareness if current else "") or ""
        note = build_identity_change_note(old_name, new_name)
        if not _note_is_readable(note, new_name):
            return False
        # Retire the agent's own "my name is X" line BEFORE appending the
        # record, so the profile is never left with one section naming the old
        # name and another the new one — which is precisely the state a real
        # two-turn run answered the old name from (2026-08-19).
        #
        # No try/except here, on purpose: the rename path's old name comes off
        # the ROW, not from an inference, so `inferred` stays False and
        # retire_self_name cannot refuse — its only raise is gated on
        # `inferred`. An except branch here sat unreachable for one round while
        # the mirror claimed it was live; do not add it back unless this call
        # starts passing inferred=True (it must not: 小绿 → 小绿2 is an exact
        # rename and refusing it is what round 12 was rejected for).
        profile = retire_self_name(profile, old_name, new_name)
        await awareness_repo.upsert(
            instance_id, merge_identity_change_note(profile, note)
        )
        return True
    except Exception as e:  # noqa: BLE001 — see docstring
        logger.warning(f"record_identity_change failed for {agent_id}: {e}")
        return False


def _asserted_name(profile: str) -> Optional[str]:
    """The name the platform record's newest entry claims, if there is one."""
    _, entries, _ = _split_record_section(profile)
    return next(
        (
            name
            for name in (identity_note_asserts(e) for e in reversed(entries))
            if name
        ),
        None,
    )


async def reconcile_identity_record(
    db, agent_id: str, current_name: str
) -> Optional[bool]:
    """Correct the profile when it asserts a name the row does not hold.

    The note used to be written whenever THIS call changed the column, which
    left every already-diverged agent unrepairable: prod agent_4a0ae5f40af2 has
    the row at 「小绿」 and a record asserting 「美食家」, so an owner renaming it to
    「小绿」 takes the "no changes needed" path, is told it worked, and reads the
    same wrong record on the next turn. That is #320's shape — retry, same
    answer, nothing moves — one layer down.

    So the obligation belongs to the STATE, not to the write: if the record
    disagrees with the row, correct it, whether or not this call wrote anything.
    Three-valued on purpose. ``None`` means there was nothing to repair — no
    record, or one that already agrees with the row — while ``False`` means a
    stale record was found and correcting it FAILED. Collapsing those into one
    boolean makes "this agent was fine" and "this agent is still broken"
    indistinguishable, and the second is the state the incident was.
    """
    try:
        instances = await InstanceRepository(db).get_by_agent(
            agent_id=agent_id, module_class="AwarenessModule"
        )
        if not instances:
            return None
        instance_id = instances[0].instance_id
        repo = InstanceAwarenessRepository(db)
        current = await repo.get_by_instance(instance_id)
        profile = (current.awareness if current else "") or ""
        # BOTH sources, always. The profile contradicts the row if the
        # platform record asserts another name, OR the agent's own section
        # declares one — and an agent can be stale in either alone. Round 9
        # keyed on the record and missed the record-less shape; the fix put the
        # self-name check inside the "no record" branch, which turned it into an
        # either/or and missed the mirror case: a record already corrected while
        # the Role and Identity line still named the old name. Two sources, one
        # question, asked of both every time.
        asserted = _asserted_name(profile)
        declared = declared_self_name(profile, current_name)
        stale_record = asserted is not None and asserted != _for_note(current_name)
        stale_line = declared is not None and declared != _for_note(current_name)
        if not stale_record and not stale_line:
            return None

        was_called = asserted if stale_record else declared
        logger.warning(
            f"[agent-profile-write] {agent_id}: profile still says "
            f"{was_called!r} while the row holds {current_name!r} — correcting "
            f"(record={stale_record}, self-name={stale_line})"
        )
        note = build_identity_reconciliation_note(current_name, was_called)
        if not _note_is_readable(note, current_name):
            return False
        refused = False
        if stale_line and declared:
            # The rename path knows the old name — it read it off the row before
            # writing. HERE it was inferred from the line, and acting on an
            # inference is what produced `小绿-2-资深` out of
            # `美食家-资深；精通各地美食推荐`: a name that never existed, after
            # which the profile was considered correct. Third variant of that
            # same inference in three review rounds.
            #
            # So the repair only rewrites a declaration that IS a bare name. A
            # line carrying more than the name keeps it, the record contradicts
            # it, and the caller is told the memory is not fully correct.
            try:
                profile = retire_self_name(
                    profile, declared, current_name, inferred=True
                )
            except _AmbiguousSelfName as exc:
                profile, refused = exc.profile, True
        if stale_record:
            profile = merge_identity_change_note(profile, note)
        elif IDENTITY_CHANGE_SECTION not in profile:
            # No record yet: the retirement alone leaves nothing telling the
            # agent which name is current, so file one.
            profile = merge_identity_change_note(profile, note)
        await repo.upsert(instance_id, profile)
        return not refused
    except Exception as e:  # noqa: BLE001 — the profile write already landed
        logger.warning(f"reconcile_identity_record failed for {agent_id}: {e}")
        return False  # found stale, failed to fix — NOT "nothing to do"


async def update_agent_profile_from_args(
    db, agent_id: str, *, new_name: Optional[str] = None,
    new_description: Optional[str] = None,
) -> str:
    """Record the agent's display name and/or one-line peer description.

    Returns the SAME status string the update_agent_profile MCP tool has always
    produced — DirectStore and the backend twin route both call this, so the
    two paths are byte-identical. Every HANDLED outcome is a string the model
    reads; the repository calls (get_agent / update_agent) can still raise on a
    real db error (db down, a MySQL 1406) — DirectStore lets that propagate
    (as the old tool did), while the Http path degrades it to an "Error: …(500)"
    string, a real parity seam on unhandled db failures only.

    This is now a renderer over ``apply_agent_profile_change``: the transaction
    is shared with the HTTP rename routes so the identity correction cannot be
    skipped by whichever caller happens to write the column.
    """
    from narranexus.platform.agent_profile import apply_agent_profile_change

    result = await apply_agent_profile_change(
        db, agent_id, new_name=new_name, new_description=new_description,
    )

    if result.error is not None:
        return result.error

    if result.status == "unchanged":
        return (
            "No changes needed — the values you passed already match "
            "your current profile."
        )

    notes: List[str] = []
    if result.name_clash_with:
        notes.append(
            f"Note: 「{result.renamed_to}」 is currently also the name of "
            f"{result.name_clash_with}, another agent of your owner. The "
            f"rename was applied as asked — if that was not intended, ask "
            f"your creator which agent should keep it."
        )

    changed = ", ".join(result.updated_fields)
    return " ".join([f"Profile updated successfully ({changed})."] + notes)

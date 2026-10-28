"""
@file_name: test_turn_accessible_roots.py
@author: NarraNexus
@date: 2026-08-10
@description: What a turn may reach outside its own workspace.

The first version of this grant handed every turn the whole per-user
`_shared` tree — every team the owner has, on every turn, including one-to-one
chats that belong to no team. That contradicted the rest of the same change:
`_resolve_entry` deliberately admits only the ONE team the turn belongs to,
and `list_for_agent_context` deliberately joins `team_members` rather than
keying on the owner, with a comment saying an owner-keyed check is the
cross-team leak. The confinement layer was using precisely that owner key.

It also is not read-only. `_PATH_ARG_NAMES` covers `file_path`, so the same
grant governs Write/Edit, and `ShellConfinementLayer` governs shell paths —
hence "accessible", not "readable".

`bus_files` stays user-wide on purpose and is not the same kind of grant: the
bus stages an attachment ONCE into the owner's shared area and every same-user
recipient reads that one path. That is the delivery mechanism, not a team
boundary.
"""

from __future__ import annotations

from narranexus.platform.utils.workspace_paths import (
    bus_files_dir,
    team_shared_dir,
    turn_accessible_roots,
    user_shared_root,
)

BASE = "/tmp/ws"
USER = "user_1"


def test_a_team_turn_gets_its_own_team_folder():
    roots = turn_accessible_roots(USER, team_id="team_1", base=BASE)
    assert str(team_shared_dir(USER, "team_1", BASE)) in roots


def test_a_team_turn_does_not_get_a_sibling_teams_folder():
    """The hole this narrowing closes: one owner has many teams, and the
    grant used to hand every one of them to every turn."""
    roots = turn_accessible_roots(USER, team_id="team_1", base=BASE)
    assert str(team_shared_dir(USER, "team_2", BASE)) not in roots
    assert not any(str(team_shared_dir(USER, "team_2", BASE)).startswith(r + "/") for r in roots)


def test_the_whole_shared_tree_is_never_granted():
    """Granting `_shared` itself re-opens every team through one parent."""
    roots = turn_accessible_roots(USER, team_id="team_1", base=BASE)
    assert str(user_shared_root(USER, BASE)) not in roots


def test_bus_attachments_are_always_reachable():
    """Acceptance #6 covers bus attachment paths too, and they are not a team
    resource: the bus stages one copy per owner and every same-user recipient
    reads that path."""
    assert str(bus_files_dir(USER, BASE)) in turn_accessible_roots(USER, team_id="team_1", base=BASE)
    assert str(bus_files_dir(USER, BASE)) in turn_accessible_roots(USER, team_id=None, base=BASE)


def test_a_private_turn_gets_no_team_folder_at_all():
    """A one-to-one chat belongs to no team, so it has no team folder to
    reach — previously it was handed all of them."""
    roots = turn_accessible_roots(USER, team_id=None, base=BASE)
    assert roots == (str(bus_files_dir(USER, BASE)),)


def test_blank_team_id_is_treated_as_no_team():
    """The trigger publishes "" rather than None outside a team; an empty
    string must not compose a path like `.../teams/`."""
    assert turn_accessible_roots(USER, team_id="", base=BASE) == turn_accessible_roots(
        USER, team_id=None, base=BASE
    )

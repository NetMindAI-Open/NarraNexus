# Governance

NarraNexus is a young project. Governance is intentionally light so
we can move fast, but it should still be predictable for contributors.
This document explains how decisions get made and how contributors
can grow into maintainers.

## Who can do what

| Role               | Can…                                           | Granted by        |
| ------------------ | ---------------------------------------------- | ----------------- |
| **Contributor**    | Open issues, open PRs, comment, vote in Discussions | Anyone with a GitHub account |
| **Maintainer**     | Review, approve, merge PRs; triage issues; cut releases | Existing maintainers |
| **Lead maintainer**| Final say on architecture & roadmap; edits `CLAUDE.md` | Founder-elected   |

Current people in each role: see [`MAINTAINERS.md`](./MAINTAINERS.md).

## How a PR gets merged

1. CI is green (import / lint / build checks).
2. **One maintainer** review approves.
3. A maintainer presses **squash-merge** (one PR = one commit on
   `main`). The PR title becomes the commit message — use
   Conventional Commits.

Maintainers may self-approve and self-merge if the change is small
and uncontroversial, but anything touching the items below requires
a second maintainer review:

- `CLAUDE.md` or any file under `.mindflow/project/references/`
- Database schema (`src/.../utils/schema_registry.py` or table
  registrations)
- Auth / identity (`backend/auth.py`,
  `backend/routes/auth.py`, anything around `request.state.user_id`)
- Bundle export / import (`src/xyz_agent_context/bundle/`)
- Release tooling (`.github/workflows/build-desktop.yml`,
  `pyproject.toml` version, `tauri/src-tauri/`)

`CODEOWNERS` mirrors this list — GitHub auto-requests the right
reviewer.

Only the lead maintainer edits `CLAUDE.md` (binding rule #11 in
`CLAUDE.md` — even maintainers cannot self-merge changes to it).

## How decisions get made

Most decisions happen in the PR review or in the linked issue.
For larger calls (new module category, breaking schema change,
licensing question, change to governance), the flow is:

1. Open a Discussion or an Issue tagged `proposal`. State the problem
   and at least one option.
2. Allow 7 days for feedback. Anyone may comment.
3. Lead maintainer makes the call, summarizes the rationale in the
   thread, and links the resulting PR.

We **don't** require RFCs or design docs for normal feature work —
that's overhead the project doesn't need yet. If a Discussion thread
gets long and consequential, the lead maintainer may upgrade it to a
written design doc retroactively.

## How to become a maintainer

We add maintainers based on demonstrated sustained contribution and
good judgment, not on patch count alone.

A rough bar:

- 5+ merged PRs spanning more than one area of the codebase
- At least 3 helpful issue triages / reviews of other people's PRs
- Familiarity with the binding rules in `CLAUDE.md` and the
  three-tier doc system
- Willing to commit to a 24-72 hour response window on reviews
  during active periods (we are humans, slower is fine — we just
  ask you to set expectations)

When a contributor meets the bar, any existing maintainer can
nominate them by opening a Discussion. If no maintainer objects in
7 days, they're in.

## How to step down

Open a PR removing yourself from `MAINTAINERS.md` and `.github/CODEOWNERS`.
We say thank you, you keep our gratitude and your commits.

## Why so light

We're early. The cost of heavy process (RFCs, voting rules, formal
roadmaps) right now exceeds the benefit. As the project grows or
external contributors outnumber internal contributors, we'll revisit.
The alternative governance models we considered (Fortress, Trunk-Based,
Release-Train, Community-Centric) are kept as internal planning notes
and surfaced again if we move to them — for now this lightweight
contract is what we run on.

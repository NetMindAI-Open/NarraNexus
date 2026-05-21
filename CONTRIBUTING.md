# Contributing to NarraNexus

Thanks for your interest in contributing. This document is the entry point.
中英文都可以 / English or 中文 — both welcome.

> **Just filing a bug or asking a question?** You don't need to read
> this whole file. Jump to [§1 Reporting issues](#1-reporting-issues)
> and pick a template. The rest of this document is for people who
> want to send code or docs.

---

## 0. Before you start — AI-assisted contributing

Most contributions here happen with an AI coding assistant. NarraNexus is
built to make that ergonomic — two files give your AI complete project
context out of the box:

- **[`CLAUDE.md`](./CLAUDE.md)** — the project's binding rules
  (architecture invariants, naming, doc-sync requirements, what's
  forbidden). Treat these as non-negotiable.
- **[`.mindflow/_overview.md`](./.mindflow/_overview.md)** — index into
  our three-tier doc system. The Tier-2 mirror (`.mindflow/mirror/`)
  pairs every source file with a short "why this exists" note your AI
  can read alongside the code.

**How to wire it up:**

- **Claude Code** — reads `CLAUDE.md` automatically. You're done.
- **Cursor / Continue / Cline** — add the two paths to your project
  rules (`.cursorrules` etc.).
- **Anything else** (ChatGPT, Gemini, Aider…) — paste the contents of
  both files into your AI's context. We also keep
  [`AGENTS.md`](./AGENTS.md) at the repo root following the
  [agents.md](https://agents.md/) convention — it's a one-line
  pointer to `CLAUDE.md` so editors that auto-load `AGENTS.md`
  (Cursor, Codex, Jules, Gemini CLI, Aider, Zed, Warp, Copilot, …)
  land in the same place.

With that context loaded, your AI will follow our conventions without
you having to memorize them. **Read the rest of this file once for
orientation — you won't need to come back.**

### 0.1 The one rule even your AI needs reminding about

When you change a `.py / .ts / .tsx / .rs` file, also update its matching
`.mindflow/mirror/<path>.md` in the same commit (CLAUDE.md binding rule
#10). Your AI knows how to do this if you fed it `CLAUDE.md` — just say
"also update the mirror md."

**If you're stuck or short on time:** add a line `Mirror-md:
needs-maintainer` to your PR description, and a maintainer will handle
the mirror md for you. Don't let it block your first PR.

---

## 1. Reporting issues

We have four issue templates — pick the one that fits:

- **Bug** — something broke or behaves unexpectedly
- **Feature** — a new capability or improvement
- **Question** — usage, design, or "how do I…"
- **Good first issue** (maintainer-only template, surfaced via the
  [contribute page](https://github.com/protagolabs/NarraNexus/contribute))

Each template tells you which fields are required. **Don't open a blank
issue** — the contact links in the issue picker route you to
Discussions or security advisories when those fit better.

For security vulnerabilities: **don't** open a public issue. See
[`SECURITY.md`](./SECURITY.md) for the private reporting flow.

---

## 2. Pull requests

### Workflow

1. Fork & branch from `main`. Branch names follow
   `feat/<topic>` · `fix/<topic>` · `docs/<topic>` · `chore/<topic>`.
2. Make changes. Your AI handles the conventions if it has `CLAUDE.md`
   in context.
3. Sanity-check locally: `make lint && make typecheck`.
4. Push and open a PR against `main`. Fill in the PR template.
5. CI runs automatically. A maintainer reviews. Once green + approved,
   we **squash-merge** (one PR = one commit on `main`).

### What we look for

- **Focused PR** — one logical change. Fixing a bug *and* refactoring
  nearby code? Split them.
- **Conventional Commit-style title** — `feat(scope): description`,
  `fix(scope): description`, etc. The PR title becomes the squash
  commit message; that's why title quality matters.
- **No backward-compatibility shims** — the project is young; we
  change cleanly instead of accreting legacy paths.
- **Mirror md synced** — see §0.1. The PR template has a checkbox.

### Review process

- 1 maintainer review is enough for most PRs.
- CI must be green (import / lint / build).
- All conversations resolved before merge.
- Maintainers can self-merge after the same review bar.

---

## 3. Where to find the rules

Rather than restate them here, point your AI (or yourself) at the
canonical sources:

| Topic | Where it lives |
| ----- | -------------- |
| Architecture layers, ironclad rules, naming, file headers | [`CLAUDE.md`](./CLAUDE.md) |
| Per-file intent (Tier-2 mirror) | `.mindflow/mirror/<path>.md` |
| Task-shaped SOPs (add a module / add a table / debug runtime …) | `.mindflow/project/playbooks/` |
| Authoritative deep references (architecture, narrative system, …) | `.mindflow/project/references/` |
| Developer commands (`make dev-backend`, `make db-sync-dry`, …) | [`Makefile`](./Makefile) and [README](./README.md) |
| Governance & decision-making | [`GOVERNANCE.md`](./GOVERNANCE.md) |
| Code of conduct | [`CODE_OF_CONDUCT.md`](./CODE_OF_CONDUCT.md) |
| Security policy | [`SECURITY.md`](./SECURITY.md) |
| Active maintainers | [`MAINTAINERS.md`](./MAINTAINERS.md) |

---

## 4. Quick start (for humans who want a sanity check)

> **Two GitHub orgs, one project.** NarraNexus is developed at
> [`protagolabs/NarraNexus`](https://github.com/protagolabs/NarraNexus) (this repo —
> open PRs here) and mirrored at
> [`NetMindAI-Open/NarraNexus`](https://github.com/NetMindAI-Open/NarraNexus) (release
> distribution + community-facing). Clone from `protagolabs` if you plan to
> contribute back; clone from either if you just want to run it.

```bash
git clone https://github.com/protagolabs/NarraNexus.git
cd NarraNexus
bash run.sh            # full local stack via tmux (4 services + frontend)
```

If you want to run pieces individually instead of the full stack:

```bash
make dev-backend       # FastAPI on :8000
make dev-frontend      # Vite on :5173
make dev-mcp           # MCP servers
make dev-poller        # ModulePoller
```

See [README.md](./README.md) for the full setup walkthrough.

---

## 5. Questions

- Usage / design questions → open a **Question** issue or start a
  [Discussion](https://github.com/protagolabs/NarraNexus/discussions).
- Need help with mirror-md updates → mark your PR `Mirror-md:
  needs-maintainer` and we'll handle it.
- Want to become a maintainer → see [`GOVERNANCE.md`](./GOVERNANCE.md).

---
code_file: real_case_e2e_test/cases/teams/01_team_add_agent.py
last_verified: 2026-05-13
stub: false
---

# teams/01_team_add_agent — Lark bug #13 reproduction (pure REST)

## Why it exists

Bug #13 reports that in local mode the chain "create team → add agent
to team" fails because `backend/routes/teams.py:_user_id_for_request`
ignores the query-param `user_id` (which `/api/auth/agents` honors)
and instead uses `get_local_user_id()` — the first row in the
`users` table. Agent.created_by ends up different from team.owner,
so the team's add-member endpoint sees an "owned by someone else"
agent and 403s.

## Decisions

- `with_llm=False` on `make_agent` — this case never sends a chat, so
  setting up a NetMind provider would waste latency.
- No `TALK` because there is no dialogue; the case body talks to the
  REST API directly via `ctx.api._post`. The runner is tolerant of
  empty TALK lists.
- The `driver_error` text quotes the team owner the backend returned
  next to the user_id this client passed, so a future reader sees the
  divergence the moment they open the manifest.

# real_case_e2e_test

Real-user dialogue end-to-end test suite for NarraNexus.

Each case spins up a fresh local user, creates one or more agents, then
drives a pre-scripted conversation against them. The driver itself is
**deterministic — no LLM in the loop on the test side**. The agent under
test still uses whatever LLM the user configured for that slot.

After every run we produce a structured transcript per case, extract
hard metrics programmatically, then ask Claude Code locally to write a
semantic pass/fail summary on top. The whole thing is built to scale
linearly as cases are added — adding a case means dropping one file in
`cases/<pillar>/`.

---

## Prerequisites

1. The full local stack must be running:
   ```
   bash run.sh
   ```
   This must already have been done **before** invoking the suite. The
   runner refuses to start otherwise; we never `fork` the stack from
   here because that hides startup failures.

2. Default provider slots must be configured for the local user. Pre-
   flight check (`core/preflight.py`) inspects `/api/providers` and
   aborts with a clear error if no usable slot exists.

3. `claude` (Claude Code CLI) must be on PATH for the semantic phase.
   The programmatic phase still produces a full report without it; the
   semantic phase will simply be skipped with a warning.

---

## Run

From the NarraNexus root:

```
# whole suite, default concurrency
python -m real_case_e2e_test.run

# one pillar
python -m real_case_e2e_test.run --pillar chat

# one case
python -m real_case_e2e_test.run --case chat/01_single_turn_greeting

# dry-run / discovery
python -m real_case_e2e_test.run --list

# rerun semantic phase on an existing run
python -m real_case_e2e_test.analyze reports/<run_ts>/
```

Output lands in `reports/<timestamp>/`:

```
reports/20260513_140502/
├── manifest.json              # which cases ran, env (commit, models), totals
├── transcripts/
│   ├── chat__01_single_turn_greeting.json
│   └── ...
├── programmatic/
│   ├── chat__01_single_turn_greeting.json       # timing, model, tool counts
│   └── ...
├── semantic/
│   ├── chat__01_single_turn_greeting.md          # Claude's reasoning
│   └── ...
└── report.md                  # human-readable summary, top-level
```

A line is appended to `state/history.jsonl` per run so future tooling
can answer "case X has been red for N runs".

---

## Adding a case

Three steps, all in one file:

1. Create `cases/<pillar>/NN_<name>.py` (where `<pillar>` is an existing
   folder under `cases/` or a new one — both are auto-discovered).
2. Declare `SPEC: CaseSpec` and `TALK: list[TalkLine]` at module scope.
3. Write `async def run(ctx) -> None` driving the talk. Use
   `ctx.fixtures.*` for all resource creation so cleanup is automatic.

Minimal skeleton:

```python
from real_case_e2e_test.core.case_spec import CaseSpec, TalkLine

SPEC = CaseSpec(
    case_id="chat__01_single_turn_greeting",
    pillar="chat",
    description="One-turn greeting, agent must reply non-empty.",
    linked_bugs=["#2", "#8"],
    severity="P0",
    tags=["needs-llm", "single-turn"],
    turn_timeout_seconds=120,
)

TALK = [
    TalkLine(role="user", content="你好，请用一句话介绍一下你自己。"),
]


async def run(ctx):
    user = await ctx.fixtures.make_user()
    agent = await ctx.fixtures.make_agent(user)
    for line in TALK:
        await ctx.drive_turn(user=user, agent=agent, content=line.content)
```

That is all. The runner discovers SPEC + TALK + run; sets up an isolated
SmokeContext; tears it all down after; pipes every WS event into the
transcript; computes metrics; calls Claude for semantic analysis.

## Removing a case

Delete the file. Next run skips it; leftover resources from prior runs
get swept on the next runner startup via the `e2e_<run_ts>_*` prefix.

---

## Layout

```
real_case_e2e_test/
├── README.md                   This file
├── run.py                      CLI: discovery → group execution → report
├── analyze.py                  CLI: re-run semantic phase on a saved run
├── core/
│   ├── case_spec.py            CaseSpec + TalkLine dataclasses
│   ├── api_client.py           Local REST: register/agent/team
│   ├── ws_client.py            WebSocket: stream agent events into a Transcript
│   ├── transcript.py           Per-case structured record
│   ├── fixtures.py             make_user / make_agent + ledger tracking
│   ├── log_grep.py             Pulls correlated backend log lines (by run_id)
│   ├── preflight.py            Verifies stack is up + provider configured
│   ├── programmatic.py         Hard metric extraction
│   ├── semantic.py             Claude Code CLI wrapper for semantic phase
│   └── runner.py               Orchestration: groups, concurrency, cleanup
├── cases/                      The only place you add files day to day
│   └── <pillar>/<NN_name>.py   One Python file per case
├── prompts/                    Prompt templates for the semantic phase
│   └── semantic_per_case.md    Per-case prompt for Claude Code
├── reports/                    Output (gitignored)
└── state/
    └── history.jsonl           One line per run, for trend queries
```

### Three contracts to remember

1. **Driver is deterministic.** No LLM on the test side. If a case needs
   to branch on what the agent said, encode the branch explicitly in
   the talk script (e.g. `expect_contains` per line). The driver never
   "thinks".
2. **Hard metrics in `programmatic/`, judgement in `semantic/`.** The
   two never mix. If a metric is binary (timeout exceeded, error event
   present), it lives in programmatic. If it requires reading the
   reply, it goes to semantic.
3. **Fixtures own cleanup.** Cases never call `delete_*` themselves;
   they call `ctx.fixtures.make_*` which records in the ledger, and
   the runner cleans up after the whole group finishes.

---

## What "programmatic" extracts (no LLM)

For every case:

- **Timing**: time-to-first-delta, time-to-first-tool-call,
  time-to-completion, per-turn latencies, end-to-end wall clock.
- **Models touched**: every `model=...` token observed in the
  `[TIMED]` log lines (resolves what was actually called, not what
  the config said).
- **Tool calls**: count, names, ordering, distribution per turn.
- **Error signals**: presence of `AGENT-LOOP-FATAL`,
  `NO-REPLY-FALLBACK`, `severity=fatal`, `429 / rate_limit`.
- **No-response placeholder**: any turn whose final reply contains the
  literal `(Agent decided no response needed)` is flagged.

All of these are computable from `transcript.json` + correlated backend
log slice, no LLM required.

## What "semantic" decides (LLM via Claude Code CLI)

For every case the runner pipes the case `SPEC`, the talk script, the
transcript, and the programmatic metrics into a prompt at
`prompts/semantic_per_case.md`, invokes `claude -p ... --output-format json`,
and stores the markdown verdict next to the transcript. The semantic
phase decides things like:

- Did the agent actually answer what was asked, vs. produce verbose
  noise?
- Did the agent drift into a meta-conversation (e.g. talking about its
  own tools instead of the task)?
- Did the multi-turn thread maintain context?

The semantic phase is allowed to be wrong / inconsistent — it's a
second opinion, not a gate. The hard gate is programmatic.

---

## What goes into `state/history.jsonl`

One JSON line per run, e.g.:

```json
{
  "run_ts": "20260513_140502",
  "narranexus_commit": "8daab45",
  "totals": {"passed": 4, "failed": 1, "errored": 0},
  "by_pillar": {"chat": {"passed": 3, "failed": 1}, "teams": {"passed": 1}},
  "failures": ["teams/01_team_add_agent"]
}
```

`python -m real_case_e2e_test.trend --case teams/01_team_add_agent`
(coming when there are >5 runs) will answer "how long has this been
red".

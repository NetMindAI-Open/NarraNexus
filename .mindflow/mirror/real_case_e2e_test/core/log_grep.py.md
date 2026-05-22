---
code_file: real_case_e2e_test/core/log_grep.py
last_verified: 2026-05-13
stub: false
---

# log_grep.py — best-effort backend log correlation

## Why it exists

The transcript already contains every event the agent emitted. The
backend log is supplementary: it surfaces things the protocol does
not (which model the resolver settled on, `[TIMED]` per-step
latencies, `NO-REPLY-FALLBACK` decisions). When the log path is
available, the programmatic phase enriches its output with this slice.

## Decisions

- We **never** assume the log path. `bash run.sh` writes to stdout
  under tmux today; that's a moving target. Cases set
  `NN_E2E_BACKEND_LOG` when they care; we surface an empty slice when
  unset and the report flags the missing log so semantic knows not to
  rely on it.
- run_id matching is full-text scan, not an index. With the local
  stack producing at most a few thousand log lines per case, the
  simplicity wins. If we ever attach this to a long-running container,
  we add an index — not now.

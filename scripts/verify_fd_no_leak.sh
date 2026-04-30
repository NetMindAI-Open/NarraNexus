#!/usr/bin/env bash
# ============================================================================
# verify_fd_no_leak.sh — sanity check for the M4/T15 fd-leak root-cause fix
# ============================================================================
#
# Background: AgentRuntime previously called LoggingService.setup() per
# run(), which added a loguru file sink with enqueue=True. Each sink
# allocated a multiprocessing.SimpleQueue (a pair of OS pipes, 2-3 fd).
# If cleanup didn't run (background hook task killed, setup() raised,
# etc.) those fds leaked. On EC2 the jobs container saturated at
# 1021/1024 fd within 3 days. The workaround was to disable per-agent
# file logging entirely in IM/job/bus triggers via
# `LoggingService(enabled=False)`, which traded observability for
# stability.
#
# After M4/T15 the file sink is added once at process startup by
# setup_logging() and never re-added. Per-run trace is recovered via
# the run_id / event_id / trigger_id contextvar fields on every line
# (M1/T4-T5). FD count should now be O(1) per process — that's what
# this script proves.
#
# Usage:
#   ./scripts/verify_fd_no_leak.sh <pid> <iters>
#
# What it does:
#   1. Snapshot fd count of <pid> as the baseline.
#   2. Sleep <iters> seconds (caller is expected to drive load
#      separately — agent runs, lark messages, jobs, whatever).
#   3. Print fd count again and the delta.
#
# A diff > 5 fds is suspicious. The script exits non-zero in that case
# so it can be wired into smoke-test pipelines.
# ============================================================================
set -euo pipefail

PID="${1:-}"
ITERS="${2:-300}"

if [[ -z "$PID" ]]; then
  echo "usage: $0 <pid> [seconds_to_observe]" >&2
  exit 2
fi

if [[ ! -d "/proc/$PID" ]]; then
  echo "no such process: $PID" >&2
  exit 2
fi

count_fd() {
  ls "/proc/$1/fd" 2>/dev/null | wc -l
}

baseline=$(count_fd "$PID")
echo "PID=$PID baseline_fd=$baseline observe=${ITERS}s"

sleep "$ITERS"

if [[ ! -d "/proc/$PID" ]]; then
  echo "process $PID exited during observation" >&2
  exit 3
fi

current=$(count_fd "$PID")
diff=$(( current - baseline ))
echo "PID=$PID current_fd=$current delta=$diff"

if (( diff > 5 )); then
  echo "FAIL: fd leaked by $diff (>5 threshold)"
  exit 1
fi
echo "OK: fd stable (delta=$diff)"

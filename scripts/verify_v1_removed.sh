#!/usr/bin/env bash
# @file_name: verify_v1_removed.sh
# @description: G011 acceptance — grep counts of v1 dashboard symbols must all be 0.
#
# Run locally or in CI after changes to dashboard surfaces. Exits non-zero
# on any residual v1 symbol; prints which pattern + where for debugging.
set -eu

FORBIDDEN=(
    'DashboardRunningJob'
    'DashboardInProgressInstance'
    'DashboardPendingJob'
    'AgentStatusCard'
    'getDashboardStatus(userId'
    'POLL_INTERVAL_MS = 5000'
)
PATHS=(backend frontend/src tauri/src-tauri/src)

fail=0
for pattern in "${FORBIDDEN[@]}"; do
    # Count matches across python / ts / tsx / rust sources only.
    # -F: fixed string (no regex surprises for `(userId`).
    # -l: list files with matches; `wc -l` gives file count.
    count=$(grep -rF \
        --include='*.py' \
        --include='*.ts' \
        --include='*.tsx' \
        --include='*.rs' \
        -l "$pattern" "${PATHS[@]}" 2>/dev/null | wc -l)
    if [ "$count" -ne 0 ]; then
        echo "FAIL: pattern '$pattern' found in $count file(s):"
        grep -rF \
            --include='*.py' \
            --include='*.ts' \
            --include='*.tsx' \
            --include='*.rs' \
            -n "$pattern" "${PATHS[@]}"
        fail=1
    else
        echo "OK:   '$pattern' count=0"
    fi
done

exit $fail

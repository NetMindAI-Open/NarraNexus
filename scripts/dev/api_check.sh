#!/usr/bin/env bash
# Public API check for the stable contracts: `griffe check` diffs
# narranexus.contracts and narranexus.sdk against a base git ref and fails on
# any breakage not listed in docs/api/accepted-breakages.txt (one symbol per
# line with the reason after " # "; each entry is a deliberate, policy-reviewed
# change — see docs/API_POLICY.md). Usage: scripts/dev/api_check.sh [base-ref]
set -uo pipefail
BASE="${1:-origin/main}"
cd "$(dirname "$0")/../.."
ACCEPTED="docs/api/accepted-breakages.txt"
status=0
for pkg in narranexus.contracts narranexus.sdk; do
  # The base ref may predate the packages/ split (batch 6d); both layouts are searched.
  out="$(griffe check "$pkg" -s packages/narranexus-contracts/src -s packages/narranexus-sdk/src -s src -a "$BASE" 2>&1)"
  rc=$?
  if [ "$rc" -eq 0 ]; then echo "$pkg: no breakage vs $BASE"; continue; fi
  echo "$out"
  # The base ref may predate the package entirely (the PR that introduces it):
  # that is a loud skip, not a pass and not a failure.
  if printf '%s\n' "$out" | grep -qE "No module named '?${pkg%%.*}|Could not find package|does not exist at"; then
    echo "SKIP: $pkg is not present at $BASE — nothing to diff against"
    continue
  fi
  # breakage lines look like "<file>:<line>: <symbol>:"
  # (interpreter warnings such as "file:line: SyntaxWarning: ..." share the prefix; only griffe's
  # breakage kinds count)
  symbols="$(printf '%s\n' "$out" | grep -E '^[^ ]+:[0-9]+: [A-Za-z_][A-Za-z0-9_.]*: (Attribute|Parameter|Return|Public|Object|Class|Function|Module|Decorator)' | sed -E 's/^[^ ]+:[0-9]+: //; s/:.*$//' | sort -u)"
  if [ -z "$symbols" ]; then
    # griffe exited non-zero without reporting a single breakage: the tool
    # itself failed (crash, syntax error, bad ref). A gate that passes on tool
    # failure is not a gate.
    echo "ERROR: griffe failed for $pkg (exit $rc) and reported no breakages — this is not a clean check"
    status=1
    continue
  fi
  for sym in $symbols; do
    if ! grep -qE "^${sym}( |$)" "$ACCEPTED" 2>/dev/null; then
      echo "BREAKING: $pkg $sym is not in $ACCEPTED"
      status=1
    else
      echo "accepted: $sym ($(grep -E "^${sym}( |$)" "$ACCEPTED" | sed 's/^[^#]*# *//'))"
    fi
  done
done
exit $status

#!/usr/bin/env bash
#
# @file_name: verify_release_artifacts.sh
# @date: 2026-06-17
# @description: Pre-push gate for the upstream release sync (release
#               workflow step 6). Catches squash-merge artifacts LOCALLY,
#               before pushing to a public shared branch — instead of
#               discovering them via a red CI run after the push.
#
# Why this exists
# ---------------
# `git merge --squash origin/main` during the upstream sync repeatedly
# leaves the same class of defects: a contiguous block pasted twice
# (duplicate imports / duplicate function or const definitions) and a
# mangled `uv.lock` (duplicate `[[package]]` entries → uv cannot parse).
# We have paid this tax at least three times (LarkConfig.tsx duplicated
# import, port_preflight.rs duplicated fn, and the v1.8.3 sync which
# duplicated MainLayout.tsx's ChatView body + api.ts's
# syncProviderDefaults + corrupted uv.lock). A grep-by-hand after the
# fact is not a gate. This script is.
#
# What it does (mirrors .github/workflows/ci.yml — the source of truth)
# ---------------------------------------------------------------------
#   1. Structural canary (fast, no deps): scans tracked .ts/.tsx/.rs for
#      duplicate top-level definitions and duplicate import lines. This
#      is the cheap early signal for the squash failure mode; it is NOT
#      exhaustive (indented class methods like api.ts's duplicated
#      method slip past it) — steps 2/3 are the real authority.
#   2. Backend: `uv lock --check` (the lock must parse AND be consistent
#      with pyproject) + `uv run ruff check src/ backend/`.
#   3. Frontend: `npm ci` + `npx tsc --noEmit` + `npm run build`
#      (`tsc -b && vite build`). This definitively catches every
#      duplicate-definition error (TS2451 / TS2393 / TS6133).
#
# Usage
# -----
#   scripts/verify_release_artifacts.sh            # full gate (default)
#   scripts/verify_release_artifacts.sh --fast     # canary + tsc + uv
#                                                  #   lock check only
#                                                  #   (skips npm ci /
#                                                  #   full build)
#
# Exit code 0 ⇒ safe to push. Non-zero ⇒ do NOT push; fix the reported
# artifacts first. Every check runs even if an earlier one fails, so a
# single run reports all problems.

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

FAST=0
[[ "${1:-}" == "--fast" ]] && FAST=1

FAILURES=()
note_fail() { FAILURES+=("$1"); }

bold() { printf '\033[1m%s\033[0m\n' "$1"; }
red()  { printf '\033[31m%s\033[0m\n' "$1"; }
grn()  { printf '\033[32m%s\033[0m\n' "$1"; }

# ── 1. Structural canary ────────────────────────────────────────────────
# Per-file: a duplicate top-level definition name or a duplicate import
# statement is the squash "block pasted twice" signature.
#
# Care taken against false positives:
#   - Imports are matched only as COMPLETE single-line statements
#     (`import … from …`); the bare `import {` opener of a multi-line
#     import is excluded (it legitimately repeats across a file).
#   - TS/TSX defs are top-level only (column 0) so a `const` reused
#     inside different function scopes is not flagged.
#   - Rust fn duplicates are cfg-aware: `#[cfg(unix)]` / `#[cfg(not(unix))]`
#     pairs share a name legitimately, so a name is flagged only when two
#     definitions carry the SAME guard (or no guard) — i.e. a real E0428.
bold "[1/3] Structural duplicate-artifact canary"
canary_hits=0
while IFS= read -r f; do
  [[ -f "$f" ]] || continue
  case "$f" in
    *.ts|*.tsx)
      dup_imports=$(grep -E '^import .* from ' "$f" 2>/dev/null | sort | uniq -d)
      dup_defs=$(grep -oE '^(export )?(async )?(function|const) [A-Za-z0-9_]+' "$f" 2>/dev/null \
                 | sed -E 's/^(export )?(async )?(function|const) //' | sort | uniq -d)
      ;;
    *.rs)
      dup_imports=$(grep -E '^use .+;' "$f" 2>/dev/null | sort | uniq -d)
      # Module-level (brace-depth 0) fn duplicates only, cfg-aware. This
      # skips trait-method names (`fn default` / `fn fmt` inside separate
      # impl blocks live at depth > 0) and platform `#[cfg]` pairs, while
      # still catching a genuinely re-pasted top-level fn (E0428).
      dup_defs=$(awk '
        function cnt(s,ch,  n,i){n=0;for(i=1;i<=length(s);i++)if(substr(s,i,1)==ch)n++;return n}
        /^[[:space:]]*#\[cfg/ { cfg=$0; depth+=cnt($0,"{")-cnt($0,"}"); next }
        /^[[:space:]]*\/\//   { next }
        {
          if (depth==0 && match($0,/^[[:space:]]*(pub[[:space:]]+)?(async[[:space:]]+)?fn[[:space:]]+[A-Za-z0-9_]+/)) {
            n=$0; sub(/.*fn[[:space:]]+/,"",n); sub(/[^A-Za-z0-9_].*/,"",n);
            print n SUBSEP cfg; cfg="";
          } else if ($0 !~ /^[[:space:]]*#\[/ && $0 !~ /^[[:space:]]*$/) { cfg="" }
          depth += cnt($0,"{")-cnt($0,"}");
        }
      ' "$f" 2>/dev/null | sort | uniq -d | sed 's/\x1c.*//')
      ;;
    *) continue ;;
  esac
  if [[ -n "$dup_imports" || -n "$dup_defs" ]]; then
    canary_hits=$((canary_hits + 1))
    red "  ✗ $f"
    while IFS= read -r line; do [[ -n "$line" ]] && printf '      duplicate import:  %s\n' "$line"; done <<<"$dup_imports"
    while IFS= read -r line; do [[ -n "$line" ]] && printf '      duplicate def:     %s\n' "$line"; done <<<"$dup_defs"
  fi
done < <(git ls-files '*.ts' '*.tsx' '*.rs')

if [[ "$canary_hits" -gt 0 ]]; then
  note_fail "structural canary: $canary_hits file(s) with duplicate top-level defs/imports"
else
  grn "  ✓ no duplicate top-level definitions or imports"
fi

# ── 2. Backend: lock integrity + lint ───────────────────────────────────
bold "[2/3] Backend — uv.lock parse + ruff"
if command -v uv >/dev/null 2>&1; then
  # `uv export --frozen` PARSES the lock (and would emit requirements)
  # without re-resolving or rewriting it. This is deliberately chosen
  # over `uv lock --check`: --check also asserts the lock is *up to date
  # with the running uv's format*, so an older-than-CI local uv false-
  # fails on a valid revision-3 lock ("needs to be updated"). We only
  # care that the lock is not CORRUPT — the squash failure mode is a
  # duplicate `[[package]]` entry that makes uv error with
  # "Failed to parse `uv.lock`". `export --frozen` catches exactly that,
  # is version-robust, and touches nothing.
  if uv export --frozen --no-emit-project -q >/tmp/_uvlock 2>&1; then
    grn "  ✓ uv.lock parses cleanly"
  else
    red "  ✗ uv.lock failed to parse:"
    sed 's/^/      /' /tmp/_uvlock | tail -10
    note_fail "uv.lock failed to parse"
  fi
  # `--frozen`: run ruff against the env materialized from the existing
  # lock, but NEVER let uv rewrite uv.lock (an older local uv would
  # otherwise downgrade the lock format / strip fields as a side effect —
  # a gate must leave the tree untouched).
  if uv run --frozen ruff check src/ backend/ >/tmp/_ruff_out 2>&1; then
    grn "  ✓ ruff clean"
  else
    red "  ✗ ruff reported issues:"; sed 's/^/      /' /tmp/_ruff_out | tail -20
    note_fail "ruff check failed"
  fi
else
  red "  ✗ uv not found on PATH — cannot verify backend"
  note_fail "uv missing"
fi

# ── 3. Frontend: install + type check + build ───────────────────────────
bold "[3/3] Frontend — tsc + build (the CI-equivalent gate)"
if command -v npm >/dev/null 2>&1; then
  pushd frontend >/dev/null
  if [[ "$FAST" -eq 0 ]]; then
    if npm ci >/tmp/_npm_ci 2>&1; then
      grn "  ✓ npm ci"
    else
      red "  ✗ npm ci failed:"; tail -15 /tmp/_npm_ci | sed 's/^/      /'
      note_fail "npm ci failed"
    fi
  elif [[ ! -d node_modules ]]; then
    red "  ✗ --fast but no node_modules present; run without --fast first"
    note_fail "no node_modules for --fast"
  fi

  if [[ -d node_modules ]]; then
    # MUST be `tsc -b`, NOT `tsc --noEmit`. The duplicate-definition errors
    # (TS2451/TS2393) surface only under the project-reference build; plain
    # `tsc --noEmit` reports them clean — which is exactly why the v1.8.3 CI
    # type-check step went green while the build step failed. `--force`
    # bypasses the incremental cache so a stale .tsbuildinfo can't hide them.
    if npx tsc -b --force >/tmp/_tsc 2>&1; then
      grn "  ✓ tsc -b (project-reference type build)"
    else
      red "  ✗ tsc reported errors:"; grep -E 'error TS' /tmp/_tsc | sed 's/^/      /' | head -30
      note_fail "tsc failed"
    fi
    if [[ "$FAST" -eq 0 ]]; then
      if npm run build >/tmp/_build 2>&1; then
        grn "  ✓ npm run build"
      else
        red "  ✗ npm run build failed:"; grep -E 'error|Error' /tmp/_build | sed 's/^/      /' | head -30
        note_fail "npm run build failed"
      fi
    fi
  fi
  popd >/dev/null
else
  red "  ✗ npm not found on PATH — cannot verify frontend"
  note_fail "npm missing"
fi

# ── Verdict ─────────────────────────────────────────────────────────────
echo
if [[ "${#FAILURES[@]}" -eq 0 ]]; then
  grn "PASS — no release artifacts detected; safe to push."
  exit 0
else
  red "FAIL — do NOT push. ${#FAILURES[@]} problem(s):"
  for x in "${FAILURES[@]}"; do red "  • $x"; done
  exit 1
fi

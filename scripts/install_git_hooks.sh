#!/usr/bin/env bash
# @file_name: install_git_hooks.sh
# @author: NexusAgent
# @date: 2026-04-09
# @description: Install a minimal git pre-commit hook that runs check_nac_doc.py.
#
# Idempotent: if .git/hooks/pre-commit already exists and was authored by this
# script (identified by a marker line), it will be replaced. Otherwise the
# existing hook is preserved and an error is printed asking the user to merge
# manually.

set -euo pipefail

# Use --git-path so we resolve correctly in both main repo and worktrees.
# In a worktree, .git is a file pointing to .git/worktrees/<name>/; hooks
# still live in the shared .git/hooks/ directory.
HOOK="$(git rev-parse --git-path hooks/pre-commit)"
MARKER="# nac-doc-hook-v1"

HOOK_BODY=$(cat <<'HOOK_EOF'
#!/usr/bin/env bash
# nac-doc-hook-v1
# Installed by scripts/install_git_hooks.sh
# Runs NAC Doc Layer 1 structural invariant check.
#
# IMPORTANT: This hook lives in the shared .git/hooks/ directory and is
# therefore executed for commits from ANY worktree or branch. To avoid
# breaking branches that don't have the NAC Doc system yet, the hook
# silently skips if scripts/check_nac_doc.py is not present in the
# current checkout.

set -e
cd "$(git rev-parse --show-toplevel)"

if [ ! -f "scripts/check_nac_doc.py" ]; then
    # Branch doesn't have NAC Doc yet — nothing to check.
    exit 0
fi

if ! command -v uv >/dev/null 2>&1; then
    echo "[nac-doc-hook] uv not found in PATH — skipping check." >&2
    exit 0
fi

uv run python -m scripts.check_nac_doc
HOOK_EOF
)

if [ -f "$HOOK" ]; then
    if grep -q "$MARKER" "$HOOK"; then
        echo "[install_git_hooks] Replacing existing nac-doc hook at $HOOK"
    else
        echo "[install_git_hooks] ERROR: $HOOK already exists and was not installed by this script." >&2
        echo "    Please merge the check manually or remove the existing hook." >&2
        exit 1
    fi
fi

printf '%s\n' "$HOOK_BODY" > "$HOOK"
chmod +x "$HOOK"
echo "[install_git_hooks] Installed pre-commit hook at $HOOK"

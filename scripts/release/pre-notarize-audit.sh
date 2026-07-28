#!/usr/bin/env bash
# ────────────────────────────────────────────────────────────────────────
# pre-notarize-audit.sh — sanity-check a signed .app right before
# submitting to Apple's notary service.
#
# Usage:   scripts/release/pre-notarize-audit.sh <path-to-signed.app>
# Exit:    0 when everything looks notarizable, non-zero on any red flag.
#
# What it checks (all independently fatal unless otherwise noted):
#   1. App layout: Contents/MacOS exists, Info.plist parseable.
#   2. `codesign --verify --deep --strict --verbose=4` on the outer bundle.
#   3. Every regular file that `file(1)` identifies as Mach-O is individually
#      verified with `codesign --verify --strict`. Unsigned inner binaries
#      are notarization rejections — Apple enumerates the whole bundle.
#   4. Entitlements of the main executable are dumped (xml) — human-reviewable.
#   5. `spctl --assess --type execute` is reported (INFORMATIONAL only when
#      the app isn't notarized yet — that's expected on the first pass).
#   6. Extended-attribute scan: the bundle must have zero xattrs. iCloud /
#      rsync / tar can reintroduce them; they are the #1 cause of the
#      cryptic "resource fork, Finder information, or similar detritus not
#      allowed" rejection from notarization.
#   7. Executable inventory: prints every file with any execute bit set and
#      every Mach-O, plus a top-10 largest-files list. Informational —
#      useful for catching accidental inclusions (Homebrew detritus,
#      test_suite binaries, .pyc shells, etc).
#
# The script is intentionally pure bash + macOS-default utilities (codesign,
# spctl, xattr, file, shasum, plutil) so it can run on any build machine
# without extra deps.
# ────────────────────────────────────────────────────────────────────────

set -euo pipefail

if [ $# -ne 1 ]; then
    echo "usage: $0 <path-to-signed.app>" >&2
    exit 2
fi

APP="$1"
if [ ! -d "$APP" ] || [ "${APP##*.}" != "app" ]; then
    echo "error: $APP is not a .app bundle" >&2
    exit 2
fi

APP=$(cd "$APP" && pwd)
APP_NAME=$(basename "$APP")

red()    { printf '\033[31m%s\033[0m\n' "$*"; }
green()  { printf '\033[32m%s\033[0m\n' "$*"; }
yellow() { printf '\033[33m%s\033[0m\n' "$*"; }
hdr()    { printf '\n=== %s ===\n' "$*"; }

FAIL=0
fail() { red "FAIL: $*"; FAIL=1; }
ok()   { green "OK:   $*"; }
warn() { yellow "WARN: $*"; }

hdr "pre-notarize audit: $APP_NAME"
echo "path: $APP"

# ── 1. App layout ──────────────────────────────────────────────────────
hdr "1. Bundle layout"
if [ ! -d "$APP/Contents/MacOS" ]; then
    fail "missing Contents/MacOS"
else
    ok "Contents/MacOS present"
fi
INFO_PLIST="$APP/Contents/Info.plist"
if [ ! -f "$INFO_PLIST" ]; then
    fail "missing Info.plist"
else
    if plutil -lint "$INFO_PLIST" >/dev/null 2>&1; then
        BUNDLE_ID=$(plutil -extract CFBundleIdentifier raw -o - "$INFO_PLIST" 2>/dev/null || echo '?')
        BUNDLE_EXE=$(plutil -extract CFBundleExecutable raw -o - "$INFO_PLIST" 2>/dev/null || echo '?')
        BUNDLE_VER=$(plutil -extract CFBundleShortVersionString raw -o - "$INFO_PLIST" 2>/dev/null || echo '?')
        ok "Info.plist parseable (id=$BUNDLE_ID exe=$BUNDLE_EXE version=$BUNDLE_VER)"
    else
        fail "Info.plist failed plutil -lint"
    fi
fi

# ── 2. Outer bundle codesign --deep --strict ──────────────────────────
hdr "2. Outer bundle codesign --deep --strict --verbose=4"
if codesign --verify --deep --strict --verbose=4 "$APP" 2>&1; then
    ok "outer bundle verify clean"
else
    fail "codesign --verify --deep --strict refused the bundle"
fi

# Dump signing authority chain — useful to confirm Developer ID vs ad-hoc.
echo "— signing authority:"
codesign -dvv "$APP" 2>&1 | grep -E 'Authority|TeamIdentifier|Identifier|Timestamp|Runtime' | sed 's/^/    /' || true

# ── 3. Per-Mach-O verify ──────────────────────────────────────────────
hdr "3. Per-Mach-O verify"
MACHO_LIST="$(mktemp "${TMPDIR:-/tmp}/audit-macho.XXXXXX")"
# Collect every regular file (skip symlinks — we don't want to double-count
# or follow into outside-the-bundle paths) that file(1) labels Mach-O.
while IFS= read -r -d '' f; do
    if file "$f" 2>/dev/null | grep -qE 'Mach-O'; then
        printf '%s\n' "$f" >> "$MACHO_LIST"
    fi
done < <(find "$APP" -type f -print0)

count=$(wc -l < "$MACHO_LIST" | tr -d ' ')
echo "found $count Mach-O binaries under bundle"
per_macho_fail=0
while IFS= read -r macho; do
    if ! codesign --verify --strict "$macho" >/dev/null 2>&1; then
        fail "unsigned / broken signature: ${macho#"$APP/"}"
        per_macho_fail=$((per_macho_fail + 1))
    fi
done < "$MACHO_LIST"
if [ "$per_macho_fail" -eq 0 ]; then
    ok "all $count Mach-O binaries verify"
else
    fail "$per_macho_fail Mach-O binaries failed verify — notarization will reject"
fi

# ── 4. Entitlements ──────────────────────────────────────────────────
hdr "4. Main executable entitlements"
MAIN_EXE="$APP/Contents/MacOS/${BUNDLE_EXE:-}"
if [ -x "$MAIN_EXE" ]; then
    codesign --display --entitlements :- "$MAIN_EXE" 2>/dev/null || warn "no entitlements on main exe"
else
    warn "main executable not found at $MAIN_EXE"
fi

# ── 5. spctl (informational pre-notarization) ────────────────────────
hdr "5. spctl --assess (informational)"
set +e
spctl --assess --verbose=4 --type execute "$APP" 2>&1 | head -5
SPCTL_EXIT=$?
set -e
if [ "$SPCTL_EXIT" -eq 0 ]; then
    ok "spctl accepted the app"
else
    warn "spctl rejected (exit $SPCTL_EXIT). Expected until notarization+staple — not a build failure on first run."
fi

# ── 6. Extended-attribute audit ──────────────────────────────────────
hdr "6. Extended-attribute audit"
# xattr -lr dumps "<path>: <attr>: <value>". We only care whether ANY
# line is emitted. Empty = clean. Any non-empty line is fatal — Apple's
# notary service rejects bundles with xattrs left over.
XATTR_OUT=$(xattr -lr "$APP" 2>/dev/null | grep -v '^$' || true)
if [ -z "$XATTR_OUT" ]; then
    ok "no extended attributes anywhere in bundle"
else
    echo "$XATTR_OUT" | head -20 | sed 's/^/    /'
    fail "bundle carries extended attributes — clean with \`xattr -cr\` before notarizing"
fi

# ── 7. Inventory (informational) ─────────────────────────────────────
hdr "7. Inventory (informational — review for unexpected inclusions)"
# Suspend errexit/pipefail for this purely informational section. Pipelines
# like `find … | sed … | head -40` provoke SIGPIPE on the upstream `sed`
# once `head` closes its stdin, which under `pipefail` propagates to the
# whole script and aborts the audit before submission. Section 7 doesn't
# touch FAIL anyway — it's purely visual.
set +e
set +o pipefail
echo "— executable files (any +x bit):"
find "$APP" -type f -perm +111 2>/dev/null | sed "s|$APP/|    |" | head -40
echo "    (truncated to 40 lines)"
echo ""
echo "— top 10 largest files:"
find "$APP" -type f -print0 2>/dev/null \
    | xargs -0 du -h 2>/dev/null \
    | sort -h \
    | tail -10 \
    | sed "s|$APP/|    |"
set -e
set -o pipefail

rm -f "$MACHO_LIST"

# ── Final verdict ───────────────────────────────────────────────────
hdr "verdict"
if [ "$FAIL" -ne 0 ]; then
    red "audit FAILED — do NOT submit this bundle to Apple notary."
    exit 1
fi
green "audit PASSED — bundle looks safe to submit."

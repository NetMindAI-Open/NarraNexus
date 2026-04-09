#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
TAURI_DIR="$PROJECT_ROOT/tauri"
SRC_TAURI="$TAURI_DIR/src-tauri"
RESOURCES_DIR="$SRC_TAURI/resources"
PYTHON_DIR="$RESOURCES_DIR/python"
PROJ_DIR="$RESOURCES_DIR/project"

echo "=== NarraNexus Desktop Build ==="
echo "Project root: $PROJECT_ROOT"
echo ""

# Step 0: Clean previous build artifacts
echo "--- Step 0: Cleaning previous build ---"
rm -rf "$PYTHON_DIR"
rm -rf "$PROJ_DIR"
rm -rf "$RESOURCES_DIR/venv"
rm -rf "$SRC_TAURI/target"
mkdir -p "$PYTHON_DIR"
mkdir -p "$PROJ_DIR"
echo "Clean done"

# Step 1: Build frontend
echo ""
echo "--- Step 1: Building frontend ---"
cd "$PROJECT_ROOT/frontend"
npm ci
npm run build
echo "Frontend build complete"

# Step 2: Download standalone Python
echo ""
echo "--- Step 2: Downloading standalone Python ---"
ARCH=$(uname -m)
if [ "$ARCH" = "arm64" ] || [ "$ARCH" = "aarch64" ]; then
    PYTHON_URL="https://github.com/astral-sh/python-build-standalone/releases/download/20260325/cpython-3.13.12%2B20260325-aarch64-apple-darwin-install_only.tar.gz"
else
    PYTHON_URL="https://github.com/astral-sh/python-build-standalone/releases/download/20260325/cpython-3.13.12%2B20260325-x86_64-apple-darwin-install_only.tar.gz"
fi

curl -L -o /tmp/python-standalone.tar.gz "$PYTHON_URL"
tar xzf /tmp/python-standalone.tar.gz -C "$PYTHON_DIR" --strip-components=1
rm /tmp/python-standalone.tar.gz
echo "Python downloaded: $("$PYTHON_DIR/bin/python3" --version)"

# Step 3: Install Python dependencies directly into standalone Python
echo ""
echo "--- Step 3: Installing Python dependencies ---"
"$PYTHON_DIR/bin/python3" -m pip install --no-cache-dir -e "$PROJECT_ROOT" 2>&1 | tail -5
echo "Python dependencies installed"

# Step 4: Copy project source
echo ""
echo "--- Step 4: Copying project source ---"
rm -rf "$PROJ_DIR"
mkdir -p "$PROJ_DIR"
rsync -a \
    --exclude='node_modules' --exclude='.venv' --exclude='.git' \
    --exclude='desktop' --exclude='tauri' --exclude='__pycache__' \
    --exclude='*.pyc' --exclude='.env' --exclude='logs' \
    --exclude='.claude' --exclude='.codex' --exclude='.worktrees' \
    --exclude='.evermemos' --exclude='related_project' \
    --exclude='sessions' --exclude='deploy' --exclude='tests' \
    --exclude='* 2' --exclude='.ruff_cache' --exclude='.pytest_cache' \
    --exclude='.vscode' --exclude='.DS_Store' --exclude='*.log' \
    --exclude='image.png' --exclude='.vite' \
    "$PROJECT_ROOT/" "$PROJ_DIR/"
echo "Project source copied"

# Step 5: Clean ALL extended attributes in tauri dir (macOS resource fork issue)
#
# macOS codesign refuses any file carrying extended attributes like
# com.apple.ResourceFork / com.apple.FinderInfo. iCloud, Spotlight, tar/rsync,
# even the linker can add them. `xattr -cr` clears them recursively; we do it
# multiple times around the build because cargo tauri's bundle step re-copies
# files and new xattrs can appear in between cleanup and codesign.
echo ""
echo "--- Step 5: Cleaning extended attributes ---"
# Prevent macOS cp/tar from writing AppleDouble (._*) sidecar files that carry xattrs
export COPYFILE_DISABLE=1
xattr -cr "$TAURI_DIR" 2>/dev/null || true
# Also strip any leftover ._ AppleDouble files
find "$TAURI_DIR" -name '._*' -delete 2>/dev/null || true
echo "xattr cleaned"

# Step 6: Build + bundle via cargo tauri. Its internal codesign WILL likely
# fail because macOS adds xattrs to the binary between the bundle copy and
# the sign step, and we can't hook in between. We let it try anyway — by
# then the .app directory is already assembled, so step 7 can clean xattrs
# and sign manually.
#
# APPLE_SIGNING_IDENTITY='-' = ad-hoc. An empty string causes tauri to
# report "no identity found" and bail before the .app is in a usable state.
echo ""
echo "--- Step 6: Building Tauri app (sign may fail — fallback in step 7) ---"
cd "$TAURI_DIR"
export APPLE_SIGNING_IDENTITY='-'

# Temporarily disable -e so a signing failure here doesn't kill the script.
# We only need the .app directory to exist for step 7 to succeed.
set +e
cargo tauri build
CARGO_EXIT=$?
set -e
if [ $CARGO_EXIT -ne 0 ]; then
    echo "cargo tauri build exited $CARGO_EXIT (expected if codesign failed)"
fi

# Step 7: Escape iCloud sync, clean xattrs, manually codesign, build DMG.
#
# Why ditto to /tmp:
# If the project lives under ~/Documents (default macOS iCloud Drive path),
# iCloud continuously re-writes com.apple.FinderInfo / ResourceFork metadata
# to every file in the bundle. `xattr -cr` clears them, then iCloud puts
# them back the instant codesign starts reading — a losing race. /tmp is
# never iCloud-managed, so once we ditto the .app over there with
# --noextattr --noqtn (which strips xattrs and the quarantine flag during
# the copy), they stay clean.
#
# All subsequent signing / DMG creation happens inside /tmp, then we copy
# the final artifacts back under the project tree for the developer to find.
echo ""
echo "--- Step 7: Signing & packaging ---"
SRC_APP_DIR="$SRC_TAURI/target/release/bundle/macos/NarraNexus.app"
if [ ! -d "$SRC_APP_DIR" ]; then
    echo "Error: .app bundle not found at $SRC_APP_DIR"
    echo "cargo tauri build failed before creating the .app (exit=$CARGO_EXIT)."
    echo "Check step 6 output for the real error."
    exit 1
fi

# Staging directory outside of any sync-watched tree.
STAGE_DIR="/tmp/narranexus-build-$$"
STAGE_APP="$STAGE_DIR/NarraNexus.app"
rm -rf "$STAGE_DIR"
mkdir -p "$STAGE_DIR"

# ditto with --noextattr --noqtn does the copy AND strips extended
# attributes / quarantine bits in one pass. More reliable than xattr -cr
# because the destination never has them in the first place.
echo "  Staging .app to $STAGE_APP (no extended attributes)..."
ditto --noextattr --noqtn "$SRC_APP_DIR" "$STAGE_APP"

# Belt-and-suspenders: also scrub anything ditto might have missed.
xattr -cr "$STAGE_APP" 2>/dev/null || true
find "$STAGE_APP" -name '._*' -delete 2>/dev/null || true
find "$STAGE_APP" -name '.DS_Store' -delete 2>/dev/null || true

# Ad-hoc sign (no Apple Developer identity required; recipients need to
# right-click → Open on first launch to bypass Gatekeeper).
echo "  Signing staged .app..."
codesign --force --deep --sign - "$STAGE_APP"
codesign --verify --verbose=2 "$STAGE_APP" 2>&1 | head -3 || true
echo "  Signing OK"

# Create DMG from the staged .app
echo ""
echo "--- Creating DMG ---"
DMG_DIR="$SRC_TAURI/target/release/bundle/dmg"
mkdir -p "$DMG_DIR"
DMG_PATH="$DMG_DIR/NarraNexus.dmg"
rm -f "$DMG_PATH"
STAGE_DMG="$STAGE_DIR/NarraNexus.dmg"
hdiutil create -volname NarraNexus -srcfolder "$STAGE_APP" -ov -format UDZO "$STAGE_DMG"
# Copy the finished DMG back under the project tree
cp "$STAGE_DMG" "$DMG_PATH"
echo "DMG created: $DMG_PATH"

# Create ZIP (fewer quarantine issues than DMG when distributed over web)
ZIP_PATH="$DMG_DIR/NarraNexus.zip"
rm -f "$ZIP_PATH"
STAGE_ZIP="$STAGE_DIR/NarraNexus.zip"
(cd "$STAGE_DIR" && ditto -c -k --keepParent "NarraNexus.app" "NarraNexus.zip")
cp "$STAGE_ZIP" "$ZIP_PATH"
echo "ZIP created: $ZIP_PATH"

# Also overwrite the .app under src-tauri with the signed-clean copy so
# `cargo tauri build` artifacts are consistent with the DMG contents.
rm -rf "$SRC_APP_DIR"
ditto --noextattr --noqtn "$STAGE_APP" "$SRC_APP_DIR"

# Clean up staging directory
rm -rf "$STAGE_DIR"
cd "$TAURI_DIR"

echo ""
echo "=== Build complete ==="
echo ""

# Show output
DMG=$(find "$SRC_TAURI/target/release/bundle/dmg/" -name "*.dmg" 2>/dev/null | head -1)
APP=$(find "$SRC_TAURI/target/release/bundle/macos/" -name "*.app" -maxdepth 1 2>/dev/null | head -1)

if [ -n "$DMG" ]; then
    ls -lh "$DMG"
    echo ""
    echo "Install: open $DMG"
else
    echo "APP: $APP"
    echo "Run: open \"$APP\""
fi

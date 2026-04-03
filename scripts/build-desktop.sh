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
echo ""
echo "--- Step 5: Cleaning extended attributes ---"
find "$TAURI_DIR" -type f -exec xattr -c {} \; 2>/dev/null || true
echo "xattr cleaned"

# Step 6: Build Tauri (compile only, no bundle yet)
echo ""
echo "--- Step 6: Compiling Tauri app ---"
cd "$TAURI_DIR"
export APPLE_SIGNING_IDENTITY='-'
cargo build --release --manifest-path src-tauri/Cargo.toml
echo "Rust compilation done"

# Step 7: Clean xattr on compiled binary, then bundle
echo ""
echo "--- Step 7: Bundling app ---"
find "$SRC_TAURI/target/release" -type f -exec xattr -c {} \; 2>/dev/null || true
cargo tauri build 2>&1 || {
    # If bundling fails due to codesign, do manual sign + DMG
    echo ""
    echo "--- Bundling failed, trying manual approach ---"
    APP_DIR="$SRC_TAURI/target/release/bundle/macos/NarraNexus.app"
    if [ -d "$APP_DIR" ]; then
        find "$APP_DIR" -type f -exec xattr -c {} \; 2>/dev/null || true
        codesign --force --deep --sign - "$APP_DIR" 2>/dev/null || true
        echo "Manual signing done"

        # Generate DMG
        echo ""
        echo "--- Creating DMG ---"
        DMG_DIR="$SRC_TAURI/target/release/bundle/dmg"
        mkdir -p "$DMG_DIR"
        DMG_PATH="$DMG_DIR/NarraNexus.dmg"
        rm -f "$DMG_PATH"
        hdiutil create -volname NarraNexus -srcfolder "$APP_DIR" -ov -format UDZO "$DMG_PATH"
        echo "DMG created: $DMG_PATH"

        # Also create ZIP (fewer quarantine issues than DMG)
        ZIP_PATH="$DMG_DIR/NarraNexus.zip"
        rm -f "$ZIP_PATH"
        cd "$(dirname "$APP_DIR")"
        ditto -c -k --keepParent "$(basename "$APP_DIR")" "$ZIP_PATH"
        echo "ZIP created: $ZIP_PATH"
        cd "$TAURI_DIR"
    fi
}

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

#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
RESOURCES_DIR="$PROJECT_ROOT/tauri/src-tauri/resources"
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
rm -rf "$PROJECT_ROOT/tauri/src-tauri/target"
mkdir -p "$PYTHON_DIR"
mkdir -p "$PROJ_DIR"
# Keep .gitkeep so the resources glob always matches
touch "$RESOURCES_DIR/.gitkeep"
echo "Clean done"

# Step 1: Build frontend
echo ""
echo "--- Step 1: Building frontend ---"
cd "$PROJECT_ROOT/frontend"
npm ci
npm run build
echo "Frontend build complete: frontend/dist/"

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

# Step 3: Install Python dependencies (directly into standalone Python, no venv)
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
    "$PROJECT_ROOT/" "$PROJ_DIR/"
echo "Project source copied"

# Step 5: Clean extended attributes (macOS)
echo ""
echo "--- Step 5: Cleaning extended attributes ---"
xattr -cr "$PROJECT_ROOT/tauri/" 2>/dev/null || true
echo "xattr cleaned"

# Step 6: Build Tauri
echo ""
echo "--- Step 6: Building Tauri app ---"
cd "$PROJECT_ROOT/tauri"
APPLE_SIGNING_IDENTITY='-' cargo tauri build

echo ""
echo "=== Build complete ==="
echo ""
echo "Output:"
ls -lh "$PROJECT_ROOT/tauri/src-tauri/target/release/bundle/dmg/"*.dmg 2>/dev/null || echo "  DMG: not found (check bundle/macos/ for .app)"
ls -lh "$PROJECT_ROOT/tauri/src-tauri/target/release/bundle/macos/"*.app 2>/dev/null || true

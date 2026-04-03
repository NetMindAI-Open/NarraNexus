#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

echo "=== NarraNexus Desktop Build ==="
echo "Project root: $PROJECT_ROOT"

# Step 1: Build frontend
echo ""
echo "--- Step 1: Building frontend ---"
cd "$PROJECT_ROOT/frontend"
npm ci
npm run build
echo "Frontend build complete: frontend/dist/"

# Step 2: Check Python standalone
PYTHON_DIR="$PROJECT_ROOT/tauri/src-tauri/resources/python"
if [ ! -f "$PYTHON_DIR/bin/python3" ]; then
    echo ""
    echo "--- Step 2: Downloading standalone Python ---"
    mkdir -p "$PYTHON_DIR"

    # Detect architecture
    ARCH=$(uname -m)
    if [ "$ARCH" = "arm64" ] || [ "$ARCH" = "aarch64" ]; then
        PYTHON_URL="https://github.com/indygreg/python-build-standalone/releases/download/20240415/cpython-3.13.0-aarch64-apple-darwin-install_only_stripped.tar.gz"
    else
        PYTHON_URL="https://github.com/indygreg/python-build-standalone/releases/download/20240415/cpython-3.13.0-x86_64-apple-darwin-install_only_stripped.tar.gz"
    fi

    curl -L -o /tmp/python-standalone.tar.gz "$PYTHON_URL"
    tar xzf /tmp/python-standalone.tar.gz -C "$PYTHON_DIR" --strip-components=1
    rm /tmp/python-standalone.tar.gz
    echo "Python downloaded to $PYTHON_DIR"
else
    echo ""
    echo "--- Step 2: Python already present, skipping ---"
fi

# Step 3: Create venv
VENV_DIR="$PROJECT_ROOT/tauri/src-tauri/resources/venv"
if [ ! -f "$VENV_DIR/bin/python3" ]; then
    echo ""
    echo "--- Step 3: Creating virtualenv ---"
    "$PYTHON_DIR/bin/python3" -m venv "$VENV_DIR"
    "$VENV_DIR/bin/pip" install --no-cache-dir -e "$PROJECT_ROOT"
    echo "Virtualenv created at $VENV_DIR"
else
    echo ""
    echo "--- Step 3: Virtualenv exists, updating ---"
    "$VENV_DIR/bin/pip" install --no-cache-dir -e "$PROJECT_ROOT"
fi

# Step 4: Copy project source
echo ""
echo "--- Step 4: Copying project source ---"
PROJ_DIR="$PROJECT_ROOT/tauri/src-tauri/resources/project"
rm -rf "$PROJ_DIR"
mkdir -p "$PROJ_DIR"
rsync -a \
    --exclude='node_modules' --exclude='.venv' --exclude='.git' \
    --exclude='desktop' --exclude='tauri' --exclude='__pycache__' \
    --exclude='*.pyc' --exclude='.env' --exclude='logs' \
    --exclude='.claude' --exclude='.codex' \
    "$PROJECT_ROOT/" "$PROJ_DIR/"
echo "Project source copied"

# Step 5: Build Tauri
echo ""
echo "--- Step 5: Building Tauri app ---"
cd "$PROJECT_ROOT/tauri"
cargo tauri build
echo ""
echo "=== Build complete ==="
echo "Output: tauri/src-tauri/target/release/bundle/"

#!/bin/bash
# Start NexusMatrix Server (port 8953)
# NexusMatrix is a separate project under related_project/
NEXUSMATRIX_DIR="$(dirname "$0")/../related_project/NetMind-AI-RS-NexusMatrix"

if [ ! -d "$NEXUSMATRIX_DIR" ]; then
    echo "[ERROR] NexusMatrix not found at $NEXUSMATRIX_DIR"
    echo "        Run 'bash run.sh install' to clone and set up NexusMatrix automatically."
    exit 1
fi

cd "$NEXUSMATRIX_DIR"
unset CLAUDECODE
uv run python -m nexus_matrix.main

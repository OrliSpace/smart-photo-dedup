#!/usr/bin/env bash
# Photo Deduplication Pipeline Launcher (macOS / Linux)

# Resolve script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Ensure python3 is available
if command -v python3 &>/dev/null; then
    PYTHON_CMD="python3"
elif command -v python &>/dev/null; then
    PYTHON_CMD="python"
else
    echo "Error: Python 3 is not installed or not in PATH."
    exit 1
fi

# Run the pipeline
"$PYTHON_CMD" "$SCRIPT_DIR/photo_dedup_pipeline.py" "$@"

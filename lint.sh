#!/bin/bash
set -e

# Ensure we're in the right directory
cd "$(dirname "$0")"

VENV=".test_venv"

# if [ ! -d "$VENV" ]; then
echo "Creating virtual environment '$VENV'..."
python3 -m venv "$VENV"
echo "Installing requirements..."
$VENV/bin/pip install -q mypy ruff python-xlib pyudev
# fi

echo "Running ruff check..."
$VENV/bin/ruff check windowcharmer/

echo "Running mypy..."
$VENV/bin/mypy windowcharmer/


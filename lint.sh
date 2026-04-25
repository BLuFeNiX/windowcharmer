#!/bin/bash
set -e

cd "$(dirname "$0")"

VENV=".test_venv"

if [ ! -d "$VENV" ]; then
    echo "Creating virtual environment '$VENV'..."
    python3 -m venv "$VENV"
    echo "Installing requirements..."
    $VENV/bin/pip install -q -e ".[dev]"
fi

echo "Running ruff check..."
$VENV/bin/ruff check windowcharmer/

echo "Running mypy..."
$VENV/bin/mypy windowcharmer/

#!/usr/bin/env bash
set -eEuo pipefail

VENV_DIR=".venv"

# cd to script directory
cd "$(dirname "$0")"

# create venv
if [ ! -d "$VENV_DIR" ]; then
    python3 -m venv "$VENV_DIR"
fi

# source venv
source "$VENV_DIR/bin/activate"

# install windowcharmer
if ! command -v windowcharmer; then
    pip install .
fi

# run daemon
windowcharmer -d

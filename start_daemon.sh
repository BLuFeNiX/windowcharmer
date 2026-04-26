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

# install/sync windowcharmer (fast no-op when already up-to-date)
pip install -q -e .

# run daemon
export PYTHONUNBUFFERED=1
windowcharmer ${WINDOWCHARMER_DEBUG:+--debug}

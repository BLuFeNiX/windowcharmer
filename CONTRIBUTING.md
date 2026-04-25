# Contributing to WindowCharmer

## Development Setup

```sh
git clone git@github.com:BLuFeNiX/windowcharmer.git
cd windowcharmer
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

Or with the Makefile:

```sh
make install-dev
```

## Running Checks

```sh
make lint        # ruff check
make typecheck   # mypy
make test        # pytest
make format      # ruff --fix (import sorting etc.)
```

Or use `lint.sh` which creates its own isolated venv:

```sh
bash lint.sh
```

All three must pass before a PR can merge. CI runs them on Python 3.11, 3.12, and 3.13.

## Pre-commit Hooks (optional)

```sh
pip install pre-commit
pre-commit install
```

This runs ruff, mypy, and basic file hygiene checks on every commit.

## Commit Messages

This project uses [Conventional Commits](https://www.conventionalcommits.org/):

```
type(scope): short description

Longer explanation if needed.
```

Common types: `fix`, `feat`, `refactor`, `docs`, `test`, `style`, `chore`

Common scopes: `wm`, `main`, `key_grabber`, `udev`, `daemon`, `meta`

Examples from the project history:
- `fix(wm): unmaximize both axes before configure`
- `refactor(main): split _handle_rebind_request`
- `docs: fix stale keybindings section`

## Project Structure

```
windowcharmer/
  config/         — keybindings, dimensions, settings, TileAction enum
  input/          — KeyGrabber, KeyMonitor (XRecord), udev/sleep monitors
  tiling/         — WindowManager, zone detection
  x11/            — DisplayPool, KeyboardMapper, AtomCache, utilities
  main.py         — CLI entry point, WindowCharmerApp orchestrator
docs/
  x11_coordinates.md   — coordinate system notes
  architecture.md      — threading model, DisplayPool design
contrib/
  windowcharmer.service  — systemd user unit
tests/            — pytest unit tests (no X server required)
```

## Architecture Notes

See [docs/architecture.md](docs/architecture.md) for a description of the threading model, the keymap-swap state machine, and the DisplayPool connection strategy.

## Running the Daemon Locally

```sh
make daemon
# or
WINDOWCHARMER_DEBUG=1 bash start_daemon.sh
```

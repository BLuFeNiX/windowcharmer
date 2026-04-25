# Changelog

All notable changes to WindowCharmer are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versions use [CalVer](https://calver.org/) (`YYYY.MM.PATCH`).

---

## [Unreleased]

### Added
- **Tests**: pytest infrastructure with 25 unit tests covering `TileAction`, `ScreenDimensions`, `Config`, keybinding loading, and zone detection (no X server required).
- **CI**: GitHub Actions workflow on Python 3.11 / 3.12 / 3.13.
- **Release workflow**: tag-triggered PyPI publish via trusted publishing.
- **Pre-commit config**: ruff (fix), mypy, check-yaml, end-of-file-fixer, trailing-whitespace.
- **Makefile**: `lint`, `format`, `typecheck`, `test`, `daemon`, `install-dev` targets.
- **Systemd unit**: `contrib/windowcharmer.service` for `~/.config/systemd/user/`.
- `KeyboardMapper.force_canonical()` — restores canonical Super_L/Hyper_L without the daemon swap logic; used by `--fix-keymap`.
- `WakeFromSleepDetector` now accepts a `stop_event` parameter for clean shutdown symmetry with the udev monitor.
- `SuperPassthroughTracker`: 5-second timeout clears stuck `super_pressed` state when a focus change consumes the KeyRelease event.
- `platformdirs` dependency replaces hand-rolled XDG path logic in keybinding config loading.
- `docs/architecture.md`: threading model, DisplayPool design, keymap state machine.
- `docs/x11_coordinates.md`: coordinate system reference (committed from previously untracked file).
- README: Keymap Notes section, Troubleshooting section, Configuration docs, systemd setup.
- `CONTRIBUTING.md`: dev setup, lint/test instructions, commit convention.

### Changed
- **`_apply_tile_action`**: replaced 11 near-identical `action_*()` methods + large `match` with a data-driven `_TILE_SPEC` dict. Adding a new zone is now a one-liner.
- **`execute_action`**: state refresh (`_update_state`, `get_active_window`) now happens before `grab_server()`, minimising the time all X clients are blocked.
- **`_handle_rebind_request`**: split into `_schedule_rebind()` (udev/sleep debounce) and `_on_mapping_notify()` (MappingNotify handler).
- **`AtomCache`**: replaced `__getattr__` magic with explicit `@property` per atom — type checkers and IDEs can now see all atom names.
- **`FrameExtents`**: `NamedTuple` → frozen `dataclass`.
- **`send_client_message`**: `data: list[int]` → `data: tuple[int, int, int, int, int]`.
- **`--fix-keymap`**: delegates to `KeyboardMapper.force_canonical()`, removing the duplicate code path from `main.py`.
- **udev monitor**: filters to `ID_INPUT_KEYBOARD == '1'` — mice, touchpads, and joysticks no longer trigger a keymap rebind.
- **`move_and_resize`**: now clears both horizontal and vertical maximization before configuring (previously only vertical), fixing configure-ignore on KWin.
- **`_update_state`**: re-queries screen dimensions from X server on every action; RandR resolution changes take effect without a restart.
- **`KeyGrabber`**: catches `Xlib.error.BadAccess` and retries once after 1 s before exiting, instead of immediately calling `sys.exit(1)`.
- **`KeyboardMapper.cleanup`**: logs a warning when original mappings are unavailable instead of silently skipping restore.
- `traceback` imports removed from `main.py`, `manager.py`, `keyboard_mapper.py`, `key_grabber.py`; replaced with `logger.debug("", exc_info=True)`.
- `pyproject.toml`: version `2026.4.1`, dep upper bounds (`<1.0`), expanded classifiers, `[project.optional-dependencies] dev`, `[tool.pytest]`, `[tool.mypy]`, `[tool.ruff]`; `mypy.ini` consolidated and removed.
- `LICENSE`: copyright year updated to `2024-2026`.
- `start_daemon.sh`: `--debug` gated behind `WINDOWCHARMER_DEBUG=1` env var.
- `lint.sh`: restored venv creation guard; added import-sort check via ruff.
- `.gitignore`: added `.mypy_cache/`, `.ruff_cache/`, `.pytest_cache/`, `dist/`, `build/`, `.coverage`, `htmlcov/`.

### Fixed
- `--fix-keymap` wrote its error message to stdout; now writes to `sys.stderr`.
- `--fix-keymap` leaked the X display connection; now closed in `finally`.
- `KeyGrabber._get_keycode`: unknown key names in `config.toml` now log a `WARNING` instead of silently skipping.
- `_REBIND_DEBOUNCE_SECONDS = 0.25` promoted from inline literal to named constant.
- `_ZONE_DEVIATION = 128` promoted from inline literal to named constant with explanatory comment.

---

## [2024.3.1] — 2024-03-?? (approximate)

Initial public release. Three-column tiling for Cinnamon/X11 with Super_L remapping, udev hotplug, sleep/wake detection, and numpad shortcuts.

[Unreleased]: https://github.com/BLuFeNiX/windowcharmer/compare/2024.3.1...HEAD
[2024.3.1]: https://github.com/BLuFeNiX/windowcharmer/releases/tag/2024.3.1

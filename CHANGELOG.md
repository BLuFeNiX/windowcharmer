# Changelog

All notable changes to WindowCharmer are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versions use [CalVer](https://calver.org/) (`YYYY.MM.PATCH`).

---

## [Unreleased]

---

## [2026.4.1] — 2026-04-26

### Added
- **Tests**: pytest infrastructure with unit tests covering `TileAction`, `ScreenDimensions`, `Config`, keybinding loading, zone detection, `SuperPassthroughTracker`, and `WindowManager` (no X server required). 41 tests total.
- **CI**: GitHub Actions workflow on Python 3.11 / 3.12 / 3.13; push trigger restricted to `main`; pytest runs with `--cov-fail-under=70`.
- **Release workflow**: tag-triggered PyPI publish via trusted publishing; gated on CI passing.
- **Dependabot**: weekly updates for pip and github-actions ecosystems.
- **Pre-commit config**: ruff (fix), mypy, check-yaml, end-of-file-fixer, trailing-whitespace.
- **Makefile**: `lint`, `format`, `format-check`, `typecheck`, `test`, `daemon`, `install-dev` targets.
- **Systemd unit**: `contrib/windowcharmer.service` for `~/.config/systemd/user/`.
- `KeyboardMapper.force_canonical()` — restores canonical Super_L/Hyper_L without the daemon swap logic; used by `--fix-keymap`.
- `WakeFromSleepDetector` now accepts a `stop_event` parameter for clean shutdown symmetry with the udev monitor.
- `SuperPassthroughTracker`: 5-second timeout clears stuck `super_pressed` state when a focus change consumes the KeyRelease event.
- `platformdirs` dependency replaces hand-rolled XDG path logic in keybinding config loading.
- `windowcharmer.__version__` sourced from `importlib.metadata`; `--version` CLI flag.
- `docs/architecture.md`: threading model, DisplayPool design, keymap state machine.
- `docs/x11_coordinates.md`: coordinate system reference.
- `docs/configuration.md`: full action list, key name format, config file location, parse error behavior.
- `docs/troubleshooting.md`: BadAccess conflicts, stuck modifier keys, bare WM notes, debug logging.
- `SECURITY.md`: privilege requirements, threat model, what the daemon does and doesn't do.
- README: Keymap Notes section, Troubleshooting section, Configuration docs, systemd setup.
- `CONTRIBUTING.md`: dev setup, lint/test instructions, commit convention, pre-commit vs CI clarification.

### Changed
- **`_apply_tile_action`**: replaced 11 near-identical `action_*()` methods + large `match` with a data-driven `_TILE_SPEC` dict. Adding a new zone is now a one-liner.
- **`execute_action`**: state refresh (`_update_state`, `get_active_window`) now happens before `grab_server()`, minimising the time all X clients are blocked.
- **`_handle_rebind_request`**: split into `_schedule_rebind()` (udev/sleep debounce) and `_on_mapping_notify()` (MappingNotify handler).
- **`AtomCache`**: replaced `__getattr__` magic with explicit `@property` per atom.
- **`FrameExtents`**: `NamedTuple` → frozen `dataclass`.
- **`KeyGrabber._run_loop`**: recursive retry converted to a `for attempt in (1, 2)` loop.
- **`KeyGrabber.IGNORED_MODIFIERS`**: mutable `list` → immutable `tuple`.
- **`KeyMonitor.stop()`**: now opens a separate `Display` connection for `record_disable_context`, fixing a python-xlib thread-safety violation.
- **`_update_state`**: sets `self.dim = None` on failure so the next action is a no-op rather than using stale geometry.
- **`Config._DEFAULT_RATIO_IDX`**: magic literal `2` replaced with a named `ClassVar`.
- **`SuperPassthroughTracker._SUPER_TIMEOUT`**: moved from module scope to `ClassVar`.
- **`WakeFromSleepDetector`**: `_WAIT_TIME` / `_THRESHOLD_TIME` module-level constants document the 15-second worst-case latency.
- **`WindowManager`**: removed `screen_height` and `active_desktop` instance fields; use local vars and `self.config.active_desktop` respectively.
- **`KeyboardMapper.super_l_orig`**: type narrowed from `Sequence[Sequence[int]]` to `list[list[int]]`.
- Event callback signatures: `Any` → `rq.Event` for `_monitor_callback`, `_on_mapping_notify`, `KeyMonitor.callback`, `InputServices.on_key_event_callback`, `SuperPassthroughTracker.handle_event`.
- `pyproject.toml`: version `2026.4.1`, `[tool.ruff.format] quote-style = "double"`, `pytest-cov<7.0`.
- `.pre-commit-config.yaml`: bumped all hooks to current stable versions.
- `LICENSE`: copyright year updated to `2024-2026`.
- `start_daemon.sh`: `--debug` gated behind `WINDOWCHARMER_DEBUG=1` env var.
- `contrib/windowcharmer.service`: added `WorkingDirectory=%h`.

### Fixed
- `get_window_position` / `determine_tile_zone`: now return `None` / `"unknown"` on `BadWindow` / `BadDrawable` instead of raising; `resize_all_windows` skips destroyed windows.
- `get_gtk_frame_extents`: guards against `_GTK_FRAME_EXTENTS` properties with fewer than 4 elements (previously would `IndexError`).
- `load_keybindings`: warns on unknown top-level TOML sections (e.g. typo `[keybinding]`).
- `get_active_desktop`: logs a one-time warning when `_NET_CURRENT_DESKTOP` is absent.
- `--fix-keymap` wrote its error message to stdout; now writes to `sys.stderr`.
- `--fix-keymap` leaked the X display connection; now closed in `finally`.
- `KeyGrabber._get_keycode`: unknown key names in `config.toml` now log a `WARNING` instead of silently skipping.
- Removed unused `AtomCache.name` / `name_fallback` properties.
- Removed unused `ScreenDimensions.measured_decorations` field.
- `DisplayPool` import hoisted to module level in `main.py` (was deferred inside two function bodies).

---

## [2024.3.1] — 2024-03-?? (approximate)

Initial public release. Three-column tiling for Cinnamon/X11 with Super_L remapping, udev hotplug, sleep/wake detection, and numpad shortcuts.

[Unreleased]: https://github.com/BLuFeNiX/windowcharmer/compare/2026.4.1...HEAD
[2026.4.1]: https://github.com/BLuFeNiX/windowcharmer/compare/2024.3.1...2026.4.1
[2024.3.1]: https://github.com/BLuFeNiX/windowcharmer/releases/tag/2024.3.1

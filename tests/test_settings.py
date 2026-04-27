from windowcharmer.config.settings import Config


def test_next_ratio_wraps() -> None:
    cfg = Config(2560)
    n = len(cfg.supported_ratios)
    for _ in range(n):
        cfg.next_ratio(1)
    assert cfg.ratio_idx == Config._DEFAULT_RATIO_IDX


def test_next_ratio_backward_wraps() -> None:
    cfg = Config(2560)
    cfg.next_ratio(-1)
    assert cfg.ratio_idx == Config._DEFAULT_RATIO_IDX - 1


def test_next_ratio_multi_step_forward() -> None:
    cfg = Config(2560)
    initial = cfg.ratio_idx
    cfg.next_ratio(2)
    assert cfg.ratio_idx == (initial + 2) % len(cfg.supported_ratios)


def test_next_ratio_multi_step_backward() -> None:
    cfg = Config(2560)
    initial = cfg.ratio_idx
    cfg.next_ratio(-2)
    assert cfg.ratio_idx == (initial - 2) % len(cfg.supported_ratios)


def test_per_desktop_ratios() -> None:
    cfg = Config(2560)
    cfg.set_active_desktop(0)
    cfg.next_ratio(1)
    idx_desktop0 = cfg.ratio_idx

    cfg.set_active_desktop(1)
    # Desktop 1 should still be at default index (2)
    assert cfg.ratio_idx == 2

    cfg.set_active_desktop(0)
    # Back to desktop 0, ratio should be remembered
    assert cfg.ratio_idx == idx_desktop0


def test_reload_preserves_ratio_idx() -> None:
    cfg = Config(2560)
    cfg.next_ratio(2)
    saved = cfg.ratio_idx
    cfg.reload()
    assert cfg.ratio_idx == saved


def test_center_width_computed() -> None:
    cfg = Config(2560)
    expected = int(2560 * cfg.supported_ratios[cfg.ratio_idx])
    assert cfg.center_width == expected


def test_set_state_reloads_once_per_change() -> None:
    cfg = Config(1920)
    calls = 0
    real_reload = cfg.reload

    def counting_reload() -> None:
        nonlocal calls
        calls += 1
        real_reload()

    cfg.reload = counting_reload  # type: ignore[method-assign]
    cfg.set_state(2560, 1)
    assert calls == 1
    assert cfg.screen_width == 2560
    assert cfg.active_desktop == 1


def test_set_state_noop_when_unchanged() -> None:
    cfg = Config(1920)
    calls = 0
    real_reload = cfg.reload

    def counting_reload() -> None:
        nonlocal calls
        calls += 1
        real_reload()

    cfg.reload = counting_reload  # type: ignore[method-assign]
    cfg.set_state(1920, 0)
    assert calls == 0

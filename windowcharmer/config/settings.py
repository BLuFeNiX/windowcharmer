from typing import ClassVar


class Config:
    # fmt: off
    supported_ratios: ClassVar[tuple[float, ...]] = (
        0.0,       # only 2 columns
        3 / 9,     # 3 even columns
        40 / 100,  # 40% center
        45 / 100,  # 45% center
        50 / 100,  # 50% center
        55 / 100,  # 55% center
        60 / 100,  # 60% center
        65 / 100,  # 65% center
    )
    # fmt: on

    _DEFAULT_RATIO_IDX: ClassVar[int] = 4  # index of 50% in supported_ratios

    def __init__(self, screen_width: int) -> None:
        self.screen_width: int = screen_width
        # Defaults to full screen until the manager learns the real workarea.
        self.wa_w: int = screen_width

        # State mapping desktop index -> ratio index
        self._desktop_ratios: dict[int, int] = {}

        self.ratio: float = 0.0
        self.center_width: int = 0
        self.active_desktop: int = 0

        self.ratio_idx: int = self._DEFAULT_RATIO_IDX
        self.reload()

    def set_state(self, width: int, wa_w: int, desktop: int) -> None:
        """Update screen width, workarea width, and active desktop in one reload."""
        if self.screen_width == width and self.wa_w == wa_w and self.active_desktop == desktop:
            return
        self.screen_width = width
        self.wa_w = wa_w
        self.active_desktop = desktop
        self.reload()

    def reload(self) -> None:
        """Recalculate dimensions based on current state."""
        self.ratio_idx = self._desktop_ratios.get(self.active_desktop, self._DEFAULT_RATIO_IDX)

        self.ratio = self.supported_ratios[self.ratio_idx]
        # Center column is sized relative to the usable workarea, not the raw
        # screen width — otherwise large left/right panels would push the
        # center column past the workarea edge.
        self.center_width = round(self.wa_w * self.ratio)

    def next_ratio(self, step: int = 1) -> None:
        """Change the layout ratio for the active desktop."""
        current_idx = self._desktop_ratios.get(self.active_desktop, self._DEFAULT_RATIO_IDX)

        new_idx = (current_idx + step) % len(self.supported_ratios)

        self._desktop_ratios[self.active_desktop] = new_idx
        self.reload()

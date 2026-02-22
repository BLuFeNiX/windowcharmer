from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

class Config:
    supported_ratios: list[float] = [
        0.0,       # only 2 columns
        3 / 9,     # 3 even columns
        40 / 100,  # 40% center
        45 / 100,  # 45% center
        50 / 100,  # 50% center
        55 / 100,  # 55% center
        60 / 100,  # 60% center
        65 / 100,  # 65% center
    ]

    def __init__(self, screen_width: int) -> None:
        self.screen_width: int = screen_width
        
        # State mapping desktop index -> ratio index
        self._desktop_ratios: dict[int, int] = {}
        
        self.ratio: float = 0.0
        self.center_width: int = 0
        self.active_desktop: int = 0
        
        self.ratio_idx: int = 2
        self.reload()

    def update_screen_width(self, width: int) -> None:
        if self.screen_width != width:
            self.screen_width = width
            self.reload()

    def set_active_desktop(self, desktop: int) -> None:
        if self.active_desktop != desktop:
            self.active_desktop = desktop
            self.reload()

    def reload(self) -> None:
        """Recalculate dimensions based on current state."""
        self.ratio_idx = self._desktop_ratios.get(self.active_desktop, 2)
        
        self.ratio = self.supported_ratios[self.ratio_idx]
        self.center_width = int(self.screen_width * self.ratio)

    def next_ratio(self, step: int = 1) -> None:
        """Change the layout ratio for the active desktop."""
        current_idx = self._desktop_ratios.get(self.active_desktop, 2)
        
        new_idx = (current_idx + len(self.supported_ratios) + step) % len(
            self.supported_ratios
        )
        
        self._desktop_ratios[self.active_desktop] = new_idx
        self.reload()

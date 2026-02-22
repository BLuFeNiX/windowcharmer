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

    # Class-level dictionary to hold state across desktop switches,
    # now that we are running permanently as a daemon.
    _state: dict[str, int] = {}
    
    def __init__(
        self,
        screen_width: int,
        active_desktop: int,
    ) -> None:
        self.screen_width: int = screen_width
        self.active_desktop: int = active_desktop
        
        self.ratio_idx: int = 2
        self.ratio: float = 0.0
        self.center_width: int = 0

        self.reload()

    def reload(self) -> None:
        """Reload configuration from in-memory state."""
        self.ratio_idx = self._state.get(f"ratio_idx_{self.active_desktop}", 2)

        # Clamp ratio_idx to valid range
        if not isinstance(self.ratio_idx, int):
             self.ratio_idx = 2
             
        self.ratio_idx = self.ratio_idx % len(self.supported_ratios)

        self.ratio = self.supported_ratios[self.ratio_idx]
        self.center_width = int(self.screen_width * self.ratio)

    def next_ratio(self, step: int = 1) -> None:
        """Change the layout ratio for the active desktop."""
        self.ratio_idx = (self.ratio_idx + len(self.supported_ratios) + step) % len(
            self.supported_ratios
        )
        self._state[f"ratio_idx_{self.active_desktop}"] = self.ratio_idx
        self.reload()

from __future__ import annotations

import shelve
from typing import Any


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

    def __init__(
        self,
        screen_width: int,
        active_desktop: int,
        config_file: str = "/dev/shm/tilew_state.v2.shelf",
    ) -> None:
        self.screen_width: int = screen_width
        self.active_desktop: int = active_desktop
        self.config_file: str = config_file

        self.measured_height: int | None = None
        self.measured_decorations: int = 0
        self.ratio_idx: int = 2
        self.ratio: float = 0.0
        self.center_width: int = 0

        self.reload()

    def put(self, key: str, value: Any) -> None:
        try:
            with shelve.open(self.config_file) as config:
                config[key] = value
        except Exception:
            pass

    def reload(self) -> None:
        try:
            with shelve.open(self.config_file) as config:
                self.measured_height = config.get("measured_height", None)
                self.measured_decorations = config.get("measured_decorations", 0)
                self.ratio_idx = config.get(f"ratio_idx_{self.active_desktop}", 2)
        except Exception:
            # Fallback defaults if file error
            self.measured_height = None
            self.measured_decorations = 0
            self.ratio_idx = 2

        # Clamp ratio_idx to valid range in case persisted value is stale
        self.ratio_idx = self.ratio_idx % len(self.supported_ratios)

        self.ratio = self.supported_ratios[self.ratio_idx]
        self.center_width = int(self.screen_width * self.ratio)

    def next_ratio(self, step: int = 1) -> None:
        self.ratio_idx = (self.ratio_idx + len(self.supported_ratios) + step) % len(
            self.supported_ratios
        )
        self.put(f"ratio_idx_{self.active_desktop}", self.ratio_idx)
        self.reload()

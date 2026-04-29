from enum import StrEnum


class TileAction(StrEnum):
    LEFT = "left"
    LEFT_CENTER = "left-center"
    RIGHT = "right"
    RIGHT_CENTER = "right-center"
    CENTER = "center"
    TOP_LEFT = "top-left"
    BOTTOM_LEFT = "bottom-left"
    TOP_RIGHT = "top-right"
    BOTTOM_RIGHT = "bottom-right"
    TOP_CENTER = "top-center"
    BOTTOM_CENTER = "bottom-center"
    MAX = "max"
    RESTORE = "restore"
    BIGGER = "bigger"
    SMALLER = "smaller"
    CYCLE = "cycle"
    EXIT = "exit"

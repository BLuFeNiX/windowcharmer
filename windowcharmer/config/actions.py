from enum import StrEnum


class TileAction(StrEnum):
    LEFT = "left"
    RIGHT = "right"
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
    EXIT = "exit"

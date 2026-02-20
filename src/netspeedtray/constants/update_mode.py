"""
Update mode presets for update interval selection.

Provided as discrete, human-friendly choices used by the settings UI.
"""
from typing import Final


class UpdateMode:
    # ❌ DON'T OFFER 100ms - too jarring for human perception
    # Human perception: 200-300ms is threshold for noticeable flicker
    AGGRESSIVE: Final[float] = 1.0   # 1 sec (current default, smooth & responsive)
    BALANCED: Final[float] = 2.0     # 2 sec
    EFFICIENT: Final[float] = 5.0    # 5 sec
    POWER_SAVER: Final[float] = 10.0 # 10 sec (still feels responsive, saves resources)
    SMART: Final[float] = -1.0       # Signal for adaptive mode


update_mode = UpdateMode()

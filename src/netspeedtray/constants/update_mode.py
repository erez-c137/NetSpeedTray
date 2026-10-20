"""
Update-rate presets: the four sampling intervals the Settings slider offers.

The config persists the rate in seconds and only these values are valid; `utils/config.py`
migrates anything else onto this ladder at load (2.2).
"""
from typing import Final, Tuple


class UpdateMode:
    # ❌ DON'T OFFER 100ms - too jarring for human perception
    # Human perception: 200-300ms is threshold for noticeable flicker
    AGGRESSIVE: Final[float] = 1.0   # 1 sec (default, smooth & responsive)
    BALANCED: Final[float] = 2.0     # 2 sec
    EFFICIENT: Final[float] = 5.0    # 5 sec
    POWER_SAVER: Final[float] = 10.0 # 10 sec (still feels responsive, saves resources)

    # Slider order: position i <-> PRESETS[i]. Ascending; the first is the floor, the last the cap.
    PRESETS: Final[Tuple[float, ...]] = (AGGRESSIVE, BALANCED, EFFICIENT, POWER_SAVER)


update_mode = UpdateMode()

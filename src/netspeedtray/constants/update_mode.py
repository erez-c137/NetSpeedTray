"""
Update-rate presets: the five sampling intervals the Settings slider offers.

The config persists the rate in seconds and only these values are valid; `utils/config.py`
migrates anything else onto this ladder at load (2.2).
"""
from typing import Final, Tuple


class UpdateMode:
    # ❌ DON'T OFFER 100ms - too jarring for human perception
    # Human perception: 200-300ms is threshold for noticeable flicker
    # 0.5 s (2.2.1, #456): opt-in. 1.2.4 had it, 1.2.6 dropped it. Everything the widget shows samples
    # at this cadence; the costly sources keep their own floors (LHM's 0.75 s cache, nvidia-smi's 5 s,
    # link stats / network identity 5 s), and history stays one net_raw row per second (same-second
    # intervals merge), so only the live display gets faster.
    REALTIME: Final[float] = 0.5     # 0.5 sec (opt-in)
    AGGRESSIVE: Final[float] = 1.0   # 1 sec (default, smooth & responsive)
    BALANCED: Final[float] = 2.0     # 2 sec
    EFFICIENT: Final[float] = 5.0    # 5 sec
    POWER_SAVER: Final[float] = 10.0 # 10 sec (still feels responsive, saves resources)

    # Slider order: position i <-> PRESETS[i]. Ascending; the first is the floor, the last the cap.
    PRESETS: Final[Tuple[float, ...]] = (REALTIME, AGGRESSIVE, BALANCED, EFFICIENT, POWER_SAVER)


update_mode = UpdateMode()

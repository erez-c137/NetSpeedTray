"""
SMART mode must never reach the sampler as a raw sentinel.

`UpdateMode.SMART` is the sentinel -1.0, chosen so a slider position can mean
"not a fixed rate". `MonitorThread.set_interval` clamps with `max(0.1, interval)`,
so handing it -1.0 does not mean "use the default" - it means **0.1 seconds**.

That is 100ms polling: twenty times the intended 2s, and the one rate that
`constants/update_mode.py` explicitly rules out in its own header comment
("DON'T OFFER 100ms - too jarring for human perception"). It also has a data
consequence, because `speed_history_raw` has a 1-second-resolution primary key
written with `INSERT OR IGNORE`: at 100ms roughly nine of every ten samples are
silently discarded, and the survivor is the *first* sample of each second rather
than an average.

Startup was always correct - `views/widget/main.py` resolves the sentinel before
using it. The break was in `ConfigController.apply_all_settings`, i.e. every
settings save. A user on SMART started at 2s and silently dropped to 100ms the
first time they opened Settings and clicked Save.

`calculate_timer_interval()` already existed and already resolved the sentinel;
the apply path simply did not call it.
"""
import os
import sys
from unittest.mock import MagicMock

import pytest

sys.path.append(os.path.abspath("src"))
from netspeedtray import constants
from netspeedtray.constants.update_mode import UpdateMode
from netspeedtray.core.config_controller import ConfigController
from netspeedtray.utils.timer_utils import calculate_timer_interval


# The floor set_interval() clamps to. Any resolved interval at or below this means
# the sentinel leaked through.
_CLAMP_FLOOR_SEC = 0.1


def test_smart_sentinel_resolves_to_the_documented_interval():
    resolved = calculate_timer_interval(UpdateMode.SMART) / 1000.0
    assert resolved == constants.timers.SMART_MODE_INTERVAL_MS / 1000.0
    assert resolved > _CLAMP_FLOOR_SEC


@pytest.mark.parametrize("rate", [UpdateMode.SMART, 0, 0.0, -1.0, -0.5])
def test_no_non_positive_rate_can_reach_the_clamp_floor(rate):
    """`config.py` permits a min of -1.0, so -0.5 is schema-valid with no meaning."""
    assert calculate_timer_interval(rate) / 1000.0 > _CLAMP_FLOOR_SEC


@pytest.mark.parametrize("rate, expected", [
    (UpdateMode.AGGRESSIVE, 1.0),
    (UpdateMode.BALANCED, 2.0),
    (UpdateMode.EFFICIENT, 5.0),
    (UpdateMode.POWER_SAVER, 10.0),
])
def test_fixed_presets_pass_through_unchanged(rate, expected):
    assert calculate_timer_interval(rate) / 1000.0 == expected


def _widget_with_config(update_rate):
    """Minimal widget stand-in: only the branches apply_all_settings touches."""
    w = MagicMock()
    w.config = {"update_rate": update_rate}
    return w


@pytest.mark.parametrize("rate", [UpdateMode.SMART, -1.0, 0.0])
def test_apply_all_settings_never_hands_a_sentinel_to_the_sampler(rate):
    """The actual regression: a settings save dropped SMART users to 100ms."""
    widget = _widget_with_config(rate)
    controller = ConfigController(widget, MagicMock())
    controller.apply_all_settings()

    widget.monitor_thread.set_interval.assert_called_once()
    passed = widget.monitor_thread.set_interval.call_args[0][0]
    assert passed > _CLAMP_FLOOR_SEC, (
        f"set_interval({passed}) clamps to {_CLAMP_FLOOR_SEC}s = 100ms polling"
    )
    assert passed == constants.timers.SMART_MODE_INTERVAL_MS / 1000.0


def test_apply_all_settings_preserves_an_explicit_rate():
    widget = _widget_with_config(5.0)
    ConfigController(widget, MagicMock()).apply_all_settings()
    assert widget.monitor_thread.set_interval.call_args[0][0] == 5.0

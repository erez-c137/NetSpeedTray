"""
The "Turn on temperatures" explainer (lhm_not_detected) must not fire for a stat nobody turned on.

Step-8 verification: a 2.2 build with a DEFAULT config showed it on every launch. show_hardware_temps
defaults to True but CPU/GPU monitoring defaults to off, so no temperature was ever polled - and "no
reading" was taken to mean "no sensor source". (2.1.7 polled CPU temps by default via the
record_hardware_history gate, removed in #347, so it found a reading and stayed quiet.)
"""
from unittest.mock import MagicMock

import pytest

from netspeedtray import constants
from netspeedtray.core.monitor_thread import StatsMonitorThread


def _thread(q_app, **overrides):
    cfg = dict(constants.config.defaults.DEFAULT_CONFIG)
    cfg.update(overrides)
    t = StatsMonitorThread(interval=0.1, config=cfg)
    t.logger = MagicMock()
    fired = []
    t.lhm_not_detected.connect(lambda: fired.append(1))
    return t, fired


def _polls(t, n=10, stats=None, in_rdp=False):
    for _ in range(n):
        t._update_lhm_notice(dict(stats or {}), in_rdp)


def test_default_config_never_shows_the_explainer(q_app):
    assert constants.config.defaults.DEFAULT_CONFIG["show_hardware_temps"] is True      # the trap
    assert constants.config.defaults.DEFAULT_CONFIG["monitor_cpu_enabled"] is False
    t, fired = _thread(q_app)
    _polls(t, 20, stats={"cpu": 5.0})   # what a forced (Monitor-window) poll without a sensor looks like
    assert fired == []


@pytest.mark.parametrize("switch", ["monitor_cpu_enabled", "monitor_gpu_enabled"])
def test_monitored_metric_with_temps_and_no_reading_shows_it_once(q_app, switch):
    t, fired = _thread(q_app, **{switch: True})
    _polls(t, 4, stats={"cpu_temp": None, "gpu_temp": None})
    assert fired == []                    # waits ~5 polls for LHM to be detected
    _polls(t, 10, stats={"cpu_temp": None, "gpu_temp": None})
    assert fired == [1]                   # once per session


def test_a_reading_keeps_it_quiet(q_app):
    t, fired = _thread(q_app, monitor_cpu_enabled=True)
    _polls(t, 10, stats={"cpu_temp": 48.0})
    assert fired == []


def test_temps_and_power_both_off_keeps_it_quiet(q_app):
    t, fired = _thread(q_app, monitor_cpu_enabled=True, show_hardware_temps=False,
                       show_hardware_power=False)
    _polls(t, 10)
    assert fired == []


def test_gpu_only_in_rdp_keeps_it_quiet(q_app):
    # GPU polling is skipped entirely in RDP sessions, so nothing could have been read.
    t, fired = _thread(q_app, monitor_gpu_enabled=True)
    _polls(t, 10, in_rdp=True)
    assert fired == []

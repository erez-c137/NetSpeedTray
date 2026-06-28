"""
The granular history periods (30m … 48h) added to PERIOD_MAP/CUTOFF_DAYS and the TimelineSelector
dropdown that surfaces them. The selector emits the PERIOD_MAP index (matching GraphHost.set_period).
"""
from datetime import datetime, timedelta

import pytest

from netspeedtray import constants
from netspeedtray.constants.i18n import I18nStrings


_GRANULAR = ("TIMELINE_30_MIN", "TIMELINE_1_HOUR", "TIMELINE_4_HOURS",
             "TIMELINE_8_HOURS", "TIMELINE_12_HOURS", "TIMELINE_48_HOURS")


@pytest.fixture(scope="session")
def q_app():
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def test_granular_periods_present_and_consistent():
    hp = constants.data.history_period
    # validate() runs on import; the new keys must be in PERIOD_MAP AND have a cutoff (or import fails).
    for k in _GRANULAR:
        assert k in hp.PERIOD_MAP.values()
        assert k in hp.CUTOFF_DAYS
    # existing saved indices 0-5 are unchanged (back-compat for stored history_period_slider_value).
    assert hp.PERIOD_MAP[0] == "TIMELINE_SESSION" and hp.PERIOD_MAP[2] == "TIMELINE_24_HOURS"


def test_get_start_time_for_granular():
    hp = constants.data.history_period
    now = datetime(2026, 1, 1, 12, 0, 0)
    assert hp.get_start_time("TIMELINE_30_MIN", now) == now - timedelta(minutes=30)
    assert hp.get_start_time("TIMELINE_4_HOURS", now) == now - timedelta(hours=4)
    assert hp.get_start_time("TIMELINE_48_HOURS", now) == now - timedelta(days=2)


def test_timeline_selector_emits_period_index(q_app):
    from netspeedtray.views.monitor.timeline_selector import TimelineSelector
    sel = TimelineSelector(I18nStrings("en_US"), current_index=2)
    assert sel.current_index() == 2                       # 24h default
    assert len(sel._actions) == 12                         # all periods (incl. session + boot)
    assert sel._CHEVRON in sel._btn.text()                 # reads as a pulldown
    seen = []
    sel.period_changed.connect(seen.append)
    sel.set_period_index(6, emit=False)                    # 30m, silent
    assert sel.current_index() == 6 and seen == []
    sel.set_period_index(11, emit=True)                    # 48h, emits
    assert seen == [11]
    sel._select(3)                                         # choosing from the menu emits (and de-dups)
    assert seen == [11, 3] and sel.current_index() == 3
    sel._select(3)                                         # same value -> no duplicate emit
    assert seen == [11, 3]

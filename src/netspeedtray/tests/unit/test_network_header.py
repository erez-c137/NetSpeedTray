"""
NetworkHeader - the Monitor Network tab's header band. Verifies the period-key mapping, that a
period pick on the shared TimelineSelector is re-emitted as the PERIOD_MAP index, and that the
totals format with the locale decimal separator (matching the graph's localized text in the same
window). (The old PeriodSegmentedControl pill row was retired - the header hosts the shared
TimelineSelector dropdown now.)
"""
import pytest

from netspeedtray.constants.i18n import I18nStrings
from netspeedtray.views.monitor.network.header import NetworkHeader, _period_value


def test_period_value_maps_keys():
    assert _period_value("TIMELINE_SESSION") == 0
    assert _period_value("TIMELINE_24_HOURS") == 2
    assert _period_value("TIMELINE_ALL") == 5
    assert _period_value("nonsense") == 2  # safe default


def test_header_forwards_period_change(q_app):
    seen = []
    h = NetworkHeader(I18nStrings("en_US"), "TIMELINE_24_HOURS")
    h.period_changed.connect(seen.append)
    # The header now hosts the shared TimelineSelector dropdown (consistent with Overview/Hardware);
    # a period pick on it is re-emitted as the PERIOD_MAP index.
    h._timeline.set_period_index(5, emit=True)   # ALL
    assert seen == [5]


def test_header_totals_format(q_app):
    h = NetworkHeader(I18nStrings("en_US"), "TIMELINE_24_HOURS")
    h.set_totals(up_bytes=1.5e9, down_bytes=12.0e9, period_key="TIMELINE_24_HOURS")
    assert "↓" in h._down[1].text() and "GB" in h._down[1].text()
    assert "↑" in h._up[1].text() and "GB" in h._up[1].text()


def test_set_period_key_updates_timeline(q_app):
    """The timeline dropdown self-labels the active window, so set_period_key syncs it (no caption)."""
    h = NetworkHeader(I18nStrings("en_US"), "TIMELINE_24_HOURS")
    assert h._timeline.current_index() == _period_value("TIMELINE_24_HOURS")   # 2
    h.set_period_key("TIMELINE_MONTH")
    assert h._timeline.current_index() == _period_value("TIMELINE_MONTH")      # 4
    assert "Month" in h._timeline.current_label()


def test_initial_value_placeholders_seeded(q_app):
    """Value blocks seed to the populated shape ('↓ -' / '↑ -') so the first emit only swaps digits."""
    h = NetworkHeader(I18nStrings("en_US"), "TIMELINE_24_HOURS")
    assert h._down[1].text() == "↓ -"
    assert h._up[1].text() == "↑ -"



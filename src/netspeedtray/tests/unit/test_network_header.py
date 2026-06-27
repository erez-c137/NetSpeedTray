"""
NetworkHeader — the Monitor Network tab's header band. Verifies the period segmented control emits
the right PERIOD_MAP index, programmatic selection is silent, and the totals format with the
locale decimal separator (matching the graph's localized text in the same window).
"""
import pytest

from netspeedtray.constants.i18n import I18nStrings
from netspeedtray.views.monitor.network.header import (
    NetworkHeader, PeriodSegmentedControl, _period_value,
)


@pytest.fixture(scope="session")
def q_app():
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def test_period_value_maps_keys():
    assert _period_value("TIMELINE_SESSION") == 0
    assert _period_value("TIMELINE_24_HOURS") == 2
    assert _period_value("TIMELINE_ALL") == 5
    assert _period_value("nonsense") == 2  # safe default


def test_pills_emit_index_on_click(q_app):
    seen = []
    pills = PeriodSegmentedControl("TIMELINE_24_HOURS")
    pills.period_changed.connect(seen.append)
    pills._buttons["TIMELINE_WEEK"].click()
    assert seen == [3]
    assert pills._buttons["TIMELINE_WEEK"].isChecked()
    assert not pills._buttons["TIMELINE_24_HOURS"].isChecked()  # exclusive


def test_set_period_key_is_silent(q_app):
    seen = []
    pills = PeriodSegmentedControl("TIMELINE_24_HOURS")
    pills.period_changed.connect(seen.append)
    pills.set_period_key("TIMELINE_MONTH")           # programmatic -> no signal
    assert seen == []
    assert pills._buttons["TIMELINE_MONTH"].isChecked()
    pills.set_period_key("bogus")                     # unknown -> falls back to 24H, still silent
    assert seen == []
    assert pills._buttons["TIMELINE_24_HOURS"].isChecked()


def test_header_forwards_period_change(q_app):
    seen = []
    h = NetworkHeader(I18nStrings("en_US"), "TIMELINE_24_HOURS")
    h.period_changed.connect(seen.append)
    h._pills._buttons["TIMELINE_ALL"].click()
    assert seen == [5]


def test_header_totals_format(q_app):
    h = NetworkHeader(I18nStrings("en_US"), "TIMELINE_24_HOURS")
    h.set_totals(up_bytes=1.5e9, down_bytes=12.0e9, period_key="TIMELINE_24_HOURS")
    assert "↓" in h._down[1].text() and "GB" in h._down[1].text()
    assert "↑" in h._up[1].text() and "GB" in h._up[1].text()


def test_period_caption_labels_the_window(q_app):
    """The totals self-describe their time window via the localized TIMELINE_* value (no new keys)."""
    i18n = I18nStrings("en_US")
    h = NetworkHeader(i18n, "TIMELINE_24_HOURS")
    h.set_totals(1e9, 2e9, period_key="TIMELINE_24_HOURS")
    assert h._period_caption.text() == getattr(i18n, "TIMELINE_24_HOURS")   # "24 Hours"
    h.set_totals(1e9, 2e9, period_key="TIMELINE_MONTH")
    assert h._period_caption.text() == getattr(i18n, "TIMELINE_MONTH")


def test_initial_value_placeholders_seeded(q_app):
    """Value blocks seed to the populated shape ('↓ —' / '↑ —') so the first emit only swaps digits."""
    h = NetworkHeader(I18nStrings("en_US"), "TIMELINE_24_HOURS")
    assert h._down[1].text() == "↓ —"
    assert h._up[1].text() == "↑ —"


def test_pills_style_is_theme_aware(q_app):
    """The Monitor pills must restyle by theme (the dark-only graph style is invisible in light mode)."""
    from netspeedtray.utils import styles as su
    assert su.segmented_pills_style(True) != su.segmented_pills_style(False)
    assert "rgba(0,0,0" in su.segmented_pills_style(False)      # light uses dark-on-light fills
    assert "rgba(255,255,255" in su.segmented_pills_style(True)  # dark uses light-on-dark fills


def test_set_period_raises_dedup_floor_and_routes(q_app):
    """set_period must drop any in-flight previous-period reply (stale-totals race) and route the
    period change through the coordinator (debounce + ylim reset), not a direct refresh."""
    from unittest.mock import MagicMock
    from netspeedtray.views.monitor.graph_host import GraphHost
    mw = MagicMock(); mw.config = {}
    host = GraphHost(mw, {}, I18nStrings("en_US"))
    host._loaded = True                      # bypass ensure_loaded (no matplotlib/thread in the test)
    host.coordinator = MagicMock()
    host._current_request_id = 7
    host.set_period(3)                       # user picks Week
    assert host._accept_from_seq == 8        # floor raised past every in-flight request
    host.coordinator.handle_timeline_change.assert_called_once_with("TIMELINE_WEEK")
    assert host._history_period_value == 3


def test_header_totals_localized_separator(q_app):
    """On a comma-decimal locale the totals must use ',' (so they match the graph's localized text)."""
    h = NetworkHeader(I18nStrings("de_DE"), "TIMELINE_24_HOURS")
    h.set_totals(up_bytes=1.2e9, down_bytes=3.4e9, period_key="TIMELINE_24_HOURS")
    sep = getattr(I18nStrings("de_DE"), "DECIMAL_SEPARATOR", ".")
    if sep == ",":
        assert "," in h._down[1].text()
        assert "." not in h._down[1].text().split("GB")[0]

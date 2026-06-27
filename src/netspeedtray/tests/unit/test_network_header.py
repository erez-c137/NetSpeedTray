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


def test_header_totals_localized_separator(q_app):
    """On a comma-decimal locale the totals must use ',' (so they match the graph's localized text)."""
    h = NetworkHeader(I18nStrings("de_DE"), "TIMELINE_24_HOURS")
    h.set_totals(up_bytes=1.2e9, down_bytes=3.4e9, period_key="TIMELINE_24_HOURS")
    sep = getattr(I18nStrings("de_DE"), "DECIMAL_SEPARATOR", ".")
    if sep == ",":
        assert "," in h._down[1].text()
        assert "." not in h._down[1].text().split("GB")[0]

"""
NetworkHeader - the Monitor Network tab's header band. Verifies the period segmented control emits
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


def test_interface_dropdown_populates_and_emits(q_app):
    h = NetworkHeader(I18nStrings("en_US"), "TIMELINE_24_HOURS")
    h.set_interfaces(["Wi-Fi", "Ethernet"])
    assert len(h._iface_menu.actions()) == 3      # "All Interfaces" + 2 NICs
    seen = []
    h.interface_changed.connect(seen.append)
    h._select_iface("Ethernet", "Ethernet")       # as if the menu item were clicked
    assert seen == ["Ethernet"]
    assert h._iface_value == "Ethernet"
    assert "Ethernet" in h._iface_btn.text()       # button reflects the selection


def test_set_interfaces_preserves_selection(q_app):
    h = NetworkHeader(I18nStrings("en_US"), "TIMELINE_24_HOURS")
    h.set_interfaces(["Wi-Fi", "Ethernet"])
    h._select_iface("Wi-Fi", "Wi-Fi")
    h.set_interfaces(["Wi-Fi", "Ethernet", "vEthernet"])   # repopulate (e.g. a NIC appeared)
    assert h._iface_value == "Wi-Fi"                       # selection kept
    h.set_interfaces(["Ethernet"])                         # Wi-Fi disappeared -> fall back to all
    assert h._iface_value == "all"


def test_set_interface_filter_raises_dedup_floor(q_app):
    from unittest.mock import MagicMock
    from netspeedtray.views.monitor.graph_host import GraphHost
    host = GraphHost(MagicMock(), {}, I18nStrings("en_US"))
    host._loaded = True
    host.update_graph = MagicMock()
    host._current_request_id = 5
    host.set_interface_filter("Ethernet")
    assert host.interface_filter == "Ethernet" and host._accept_from_seq == 6
    host.set_interface_filter("all")
    assert host.interface_filter is None                   # "all" -> no filter


def test_header_totals_localized_separator(q_app):
    """On a comma-decimal locale the totals must use ',' (so they match the graph's localized text)."""
    h = NetworkHeader(I18nStrings("de_DE"), "TIMELINE_24_HOURS")
    h.set_totals(up_bytes=1.2e9, down_bytes=3.4e9, period_key="TIMELINE_24_HOURS")
    sep = getattr(I18nStrings("de_DE"), "DECIMAL_SEPARATOR", ".")
    if sep == ",":
        assert "," in h._down[1].text()
        assert "." not in h._down[1].text().split("GB")[0]

"""
Per-app detail-on-demand in the Monitor's Network tab: clicking an app selects it and opens a panel
showing that app's remote hosts + live connections (reusing the AppActivityWorker payload). This is
the view that retires the standalone App Activity window, so it must show the same data and stay live.
"""
from unittest.mock import MagicMock

import pytest

from netspeedtray.constants.i18n import I18nStrings
from netspeedtray.views.monitor.network.app_list import AppBarList
from netspeedtray.views.monitor.network.detail import ConnectionDetailPanel


@pytest.fixture(scope="session")
def q_app():
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def _payload(updated="12:00:00", include_chrome=True):
    rows = []
    if include_chrome:
        rows.append({
            "identity_key": "chrome.exe", "display_name": "Chrome", "pids": [100, 200],
            "conn_count": 4, "tcp_count": 4, "udp_count": 0, "established_count": 3,
            "distinct_hosts": ["1.2.3.4", "5.6.7.8"], "host_count": 2,
            "endpoints": ["TCP 10.0.0.2:5000 -> 1.2.3.4:443 ESTABLISHED",
                          "TCP 10.0.0.2:5001 -> 5.6.7.8:443 ESTABLISHED"], "is_idle": False})
    rows.append({
        "identity_key": "svchost.exe", "display_name": "svchost", "pids": [4],
        "conn_count": 1, "tcp_count": 0, "udp_count": 1, "established_count": 0,
        "distinct_hosts": [], "host_count": 0, "endpoints": ["UDP 10.0.0.2:53 -> *:*"], "is_idle": True})
    return {"updated_at": updated, "app_count": len(rows), "active_app_count": 1 if include_chrome else 0,
            "total_conn_count": sum(r["conn_count"] for r in rows), "access_limited": False, "rows": rows}


def _i18n():
    return I18nStrings("en_US")


def test_app_row_click_selects_and_emits(q_app):
    lst = AppBarList(_i18n())
    lst.set_payload(_payload())
    seen = []
    lst.row_selected.connect(seen.append)
    lst._rows["chrome.exe"].clicked.emit("chrome.exe")
    assert seen == ["chrome.exe"]
    assert lst.selected_key() == "chrome.exe"
    assert lst._rows["chrome.exe"]._selected is True
    assert lst._rows["svchost.exe"]._selected is False
    # get_row exposes the live payload data the detail panel reads.
    assert lst.get_row("chrome.exe")["distinct_hosts"] == ["1.2.3.4", "5.6.7.8"]


def test_detail_panel_renders_hosts_and_connections(q_app):
    panel = ConnectionDetailPanel(_i18n())
    panel.set_row(_payload()["rows"][0])  # chrome
    title = panel._title.text()
    body = panel._body.toPlainText()
    assert "Chrome" in title and "100, 200" in title           # name + PIDs
    assert "Talking to:" in body and "1.2.3.4" in body and "5.6.7.8" in body
    assert "Connections:" in body and "1.2.3.4:443 ESTABLISHED" in body


def test_detail_panel_empty_when_no_connections(q_app):
    panel = ConnectionDetailPanel(_i18n())
    panel.set_row(_payload()["rows"][1])  # svchost: no hosts, one UDP endpoint
    body = panel._body.toPlainText()
    assert "Connections:" in body          # it does have an endpoint
    assert "Talking to:" not in body       # but no off-box hosts


def test_detail_tracks_current_app_for_scroll_reset(q_app):
    # _current_key drives "reset scroll to top on a new app vs keep position on a live refresh".
    panel = ConnectionDetailPanel(_i18n())
    panel.set_row(_payload()["rows"][0])
    assert panel._current_key == "chrome.exe"
    panel.set_row(_payload()["rows"][1])
    assert panel._current_key == "svchost.exe"


def _network_tab(q_app):
    from netspeedtray import constants
    from netspeedtray.views.monitor.network.tab import NetworkTab
    cfg = dict(constants.config.defaults.DEFAULT_CONFIG)
    return NetworkTab(MagicMock(), MagicMock(), cfg, _i18n())


def test_network_tab_showevent_resyncs_timeline_to_shared_period(q_app):
    """#1: returning to the Network tab after the period was changed on another tab must re-sync the
    header's timeline dropdown to the shared host period (the graph + totals already follow it)."""
    from netspeedtray import constants
    from PyQt6.QtGui import QShowEvent
    tab = _network_tab(q_app)
    tab._feed = MagicMock()                             # don't start the real activity-feed QThread
    week_val = next(k for k, v in constants.data.history_period.PERIOD_MAP.items() if v == "TIMELINE_WEEK")
    tab._host._history_period_value = week_val          # shared period is now 'Week'
    tab._header.set_period_key = MagicMock()            # spy
    tab.showEvent(QShowEvent())
    tab._header.set_period_key.assert_called_once_with("TIMELINE_WEEK")


def test_network_tab_detail_flow_select_refresh_close(q_app):
    tab = _network_tab(q_app)
    tab._on_payload(_payload(updated="12:00:00"))
    assert tab._detail.isHidden()                         # nothing selected yet

    # Click Chrome -> panel opens with its detail.
    tab._app_list._rows["chrome.exe"].clicked.emit("chrome.exe")
    assert not tab._detail.isHidden()
    assert "1.2.3.4" in tab._detail._body.toPlainText()

    # A fresh payload (new connection) keeps the open panel live.
    p2 = _payload(updated="12:00:02")
    p2["rows"][0]["distinct_hosts"] = ["1.2.3.4", "5.6.7.8", "9.9.9.9"]
    p2["rows"][0]["endpoints"].append("TCP 10.0.0.2:5002 -> 9.9.9.9:443 ESTABLISHED")
    tab._on_payload(p2)
    assert "9.9.9.9" in tab._detail._body.toPlainText()        # refreshed in place

    # Close -> panel hides + selection clears.
    tab._on_detail_closed()
    assert tab._detail.isHidden()
    assert tab._app_list.selected_key() is None


def test_selected_row_pinned_to_top(q_app):
    # Review fix: the selected row is pinned first so its highlight + detail don't slide when its
    # active/idle state flips. svchost is idle (normally sorts below active Chrome) — once selected
    # it must lead the list.
    lst = AppBarList(_i18n())
    lst.set_payload(_payload())
    lst._rows["svchost.exe"].clicked.emit("svchost.exe")
    lst.set_payload(_payload())
    assert lst._list_layout.itemAt(0).widget() is lst._rows["svchost.exe"]


def test_empty_payload_prunes_rows(q_app):
    # Review fix: an empty payload must clear the stale rows, not just change the summary text.
    lst = AppBarList(_i18n())
    lst.set_payload(_payload())
    assert lst._rows
    lst.set_payload({"rows": [], "app_count": 0, "total_conn_count": 0, "updated_at": "x"})
    assert lst._rows == {}
    widgets = [lst._list_layout.itemAt(i).widget() for i in range(lst._list_layout.count())]
    assert all(w is None for w in widgets)   # only the trailing stretch remains


def test_empty_payload_shows_access_limited_message(q_app):
    # Review fix (parity with the old window): zero rows + access_limited -> the actionable hint.
    lst = AppBarList(_i18n())
    lst.set_payload({"rows": [], "access_limited": True, "app_count": 0,
                     "total_conn_count": 0, "updated_at": "x"})
    assert "Limited permissions" in lst._summary.text()


def test_network_tab_detail_marks_inactive_when_app_drops(q_app):
    tab = _network_tab(q_app)
    tab._on_payload(_payload())
    tab._app_list._rows["chrome.exe"].clicked.emit("chrome.exe")
    assert "1.2.3.4" in tab._detail._body.toPlainText()
    # Chrome closes all connections -> drops off the payload -> panel goes "no details", stays open.
    tab._on_payload(_payload(include_chrome=False))
    assert not tab._detail.isHidden()
    assert "No connection details available." in tab._detail._body.toPlainText()
    assert "Chrome" in tab._detail._title.text()               # keeps the last-known name

"""
GUI smoke + data tests for the App Activity window v2 (pytest-qt, offscreen).

Drives the honest data->table seam (_render_rows / _on_data_ready) with synthetic
identity-grouped rows. No speed columns, no _format_speed — the window now shows live
connections, active count, and the hosts each app is talking to.
"""
import pytest
from unittest.mock import MagicMock

from netspeedtray.constants.i18n import I18nStrings
import netspeedtray.views.app_activity.window as aaw
from netspeedtray.views.app_activity.window import AppActivityWindow


def _row(name, conns, active, hosts, endpoints, pids=(1,)):
    return {
        "display_name": name, "pids": list(pids), "conn_count": conns,
        "tcp_count": conns, "udp_count": 0, "established_count": active,
        "distinct_hosts": hosts, "host_count": len(hosts), "endpoints": endpoints,
        "is_idle": active == 0,
    }


@pytest.fixture
def app_activity(qtbot, monkeypatch):
    monkeypatch.setattr(aaw, "is_rdp_session", lambda: True)  # skip the live worker thread
    main = MagicMock(); main.config = {}; main.config_manager = MagicMock()
    win = AppActivityWindow(main_widget=main, i18n=I18nStrings("en_US"))
    qtbot.addWidget(win)
    return win


def _payload(rows, **extra):
    p = {
        "rows": rows, "app_count": len(rows),
        "active_app_count": sum(1 for r in rows if r["established_count"] > 0),
        "total_conn_count": sum(r["conn_count"] for r in rows),
        "updated_at": "12:34:56", "access_limited": False,
    }
    p.update(extra)
    return p


def test_app_activity_constructs(app_activity):
    """The window builds offscreen with its 5-column honest table and no worker thread."""
    assert app_activity.table is not None
    assert app_activity.table.columnCount() == 5
    assert app_activity.worker is None


def test_render_rows_populates_honest_columns(app_activity):
    rows = [
        _row("chrome.exe", conns=3, active=2, hosts=["1.2.3.4"], endpoints=["TCP a -> b"]),
        _row("spotify.exe", conns=1, active=1, hosts=["5.6.7.8"], endpoints=[]),
    ]
    app_activity._render_rows(rows)
    assert app_activity.table.rowCount() == 2
    assert app_activity.table.item(0, 0).text() == "chrome.exe"
    assert app_activity.table.item(0, 1).text() == "3"   # connections
    assert app_activity.table.item(0, 2).text() == "2"   # active


def test_render_rows_empty_clears_table(app_activity):
    app_activity._render_rows([_row("x", 0, 0, [], [])])
    assert app_activity.table.rowCount() == 1
    app_activity._render_rows([])
    assert app_activity.table.rowCount() == 0


def test_on_data_ready_summary_is_honest_and_selects_first(app_activity):
    rows = [
        _row("chrome.exe", conns=3, active=2, hosts=["1.2.3.4"], endpoints=["TCP a -> b"]),
        _row("spotify.exe", conns=1, active=0, hosts=[], endpoints=[]),
    ]
    app_activity._on_data_ready(_payload(rows))
    assert app_activity.table.rowCount() == 2
    text = app_activity.summary_label.text()
    assert "2 apps" in text and "connections" in text
    assert "Download" not in text and "Upload" not in text  # no fake speed
    assert app_activity.table.selectionModel().selectedRows()


def test_on_data_ready_empty_shows_message(app_activity):
    app_activity._on_data_ready({"rows": [], "access_limited": False})
    assert app_activity.table.rowCount() == 0
    assert app_activity.summary_label.text()


def test_talking_to_preview_truncates(app_activity):
    hosts = [f"10.0.0.{i}" for i in range(5)]
    preview = app_activity._build_endpoint_preview(hosts)
    assert preview.count(".") >= 3
    assert "2" in preview  # "(+2 more)"


def test_selection_shows_pids_hosts_and_endpoints(app_activity):
    rows = [_row("chrome.exe", conns=2, active=2, hosts=["1.2.3.4"],
                 endpoints=["TCP a -> b", "TCP c -> d"], pids=[42, 43])]
    app_activity._on_data_ready(_payload(rows))
    text = app_activity.details_box.toPlainText()
    assert "chrome.exe" in text and "42" in text and "43" in text
    assert "1.2.3.4" in text
    assert "TCP a -> b" in text and "TCP c -> d" in text


def test_summary_appends_access_limited_suffix(app_activity):
    rows = [_row("p", 1, 0, [], [])]
    app_activity._on_data_ready(_payload(rows, access_limited=True))
    suffix = app_activity._tr("APP_ACTIVITY_SUMMARY_LIMITED_SUFFIX",
                              "(limited access without admin rights)")
    assert suffix in app_activity.summary_label.text()


def test_worker_error_is_surfaced(app_activity):
    app_activity._on_worker_error("boom")
    assert "boom" in app_activity.summary_label.text()

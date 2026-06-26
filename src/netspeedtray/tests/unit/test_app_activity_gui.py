"""
GUI smoke + data tests for the App Activity window (pytest-qt, offscreen).

Constructs the real window (the psutil-scanning worker thread is skipped by mocking
is_rdp_session) and drives the data->table seam (_render_rows) with synthetic rows.
This catches construction/wiring regressions and the row-rendering logic without
spawning the live worker.
"""
import pytest
from unittest.mock import MagicMock

from netspeedtray.constants.i18n import I18nStrings
import netspeedtray.views.app_activity.window as aaw
from netspeedtray.views.app_activity.window import AppActivityWindow


@pytest.fixture
def app_activity(qtbot, monkeypatch):
    # Skip the real psutil-scanning worker — we test construction + rendering, not live
    # sampling. The RDP branch sets worker=None and starts no thread.
    monkeypatch.setattr(aaw, "is_rdp_session", lambda: True)
    main = MagicMock()
    main.config = {}
    main.config_manager = MagicMock()
    win = AppActivityWindow(main_widget=main, i18n=I18nStrings("en_US"))
    qtbot.addWidget(win)
    return win


def test_app_activity_constructs(app_activity):
    """The window builds offscreen with its 6-column table and no worker thread."""
    assert app_activity.table is not None
    assert app_activity.table.columnCount() == 6
    assert app_activity.worker is None  # RDP path -> no live worker


def test_render_rows_populates_table(app_activity):
    """Feeding synthetic rows populates the table with the right process + PID."""
    rows = [
        {"process_name": "chrome.exe", "pid": 1234, "download_bps": 125000,
         "upload_bps": 62500, "connection_count": 3, "endpoints": ["1.2.3.4:443"]},
        {"process_name": "spotify.exe", "pid": 5678, "download_bps": 25000,
         "upload_bps": 1000, "connection_count": 1, "endpoints": []},
    ]
    app_activity._render_rows(rows)

    assert app_activity.table.rowCount() == 2
    assert app_activity.table.item(0, 0).text() == "chrome.exe"
    assert app_activity.table.item(1, 0).text() == "spotify.exe"
    assert app_activity.table.item(0, 1).text() == "1234"  # PID column


def test_render_rows_empty_clears_table(app_activity):
    """An empty sample clears the table without error."""
    app_activity._render_rows([{"process_name": "x", "pid": 1, "download_bps": 0,
                                "upload_bps": 0, "connection_count": 0, "endpoints": []}])
    assert app_activity.table.rowCount() == 1
    app_activity._render_rows([])
    assert app_activity.table.rowCount() == 0


def test_on_data_ready_populates_summary_and_selects_first_row(app_activity):
    """The real worker-data slot fills the table, the summary line, and selects row 0."""
    payload = {
        "rows": [
            {"process_name": "chrome.exe", "pid": 1234, "download_bps": 125000,
             "upload_bps": 62500, "connection_count": 3, "endpoints": ["1.2.3.4:443"]},
            {"process_name": "spotify.exe", "pid": 5678, "download_bps": 25000,
             "upload_bps": 1000, "connection_count": 1, "endpoints": []},
        ],
        "total_down_bps": 150000.0, "total_up_bps": 63500.0, "updated_at": "12:34:56",
        "access_limited": False,
    }
    app_activity._on_data_ready(payload)

    assert app_activity.table.rowCount() == 2
    assert "2" in app_activity.summary_label.text()         # app_count in the summary
    assert app_activity.table.selectionModel().selectedRows()  # first row auto-selected


def test_on_data_ready_empty_shows_message(app_activity):
    """An empty payload clears the table and shows a non-empty status message."""
    app_activity._on_data_ready({"rows": [], "access_limited": False})
    assert app_activity.table.rowCount() == 0
    assert app_activity.summary_label.text()  # some "no data" message, not blank


# --- endpoint preview --------------------------------------------------------

def test_endpoint_preview_truncates_with_more_suffix(app_activity):
    """Only the first ENDPOINT_PREVIEW_LIMIT (3) endpoints are shown inline, with a
    '(+N more)' suffix counting the rest."""
    endpoints = [f"TCP 127.0.0.1:{5000 + i} -> 1.1.1.1:443" for i in range(5)]
    preview = app_activity._build_endpoint_preview(endpoints)
    assert preview.count("->") == 3            # first 3 shown
    assert "2" in preview                       # "(+2 more)"


def test_endpoint_preview_empty_is_dash(app_activity):
    assert app_activity._build_endpoint_preview([]) == "-"


# --- selection -> details ----------------------------------------------------

def test_selection_populates_connection_details(app_activity):
    """Auto-selecting row 0 fills the details box with that app's endpoints."""
    rows = [{"process_name": "chrome.exe", "pid": 42, "download_bps": 0.0,
             "upload_bps": 0.0, "connection_count": 2,
             "endpoints": ["TCP a -> b", "TCP c -> d"]}]
    app_activity._on_data_ready({"rows": rows, "total_down_bps": 0.0,
                                 "total_up_bps": 0.0, "updated_at": "12:00:00"})
    text = app_activity.details_box.toPlainText()
    assert "chrome.exe" in text and "42" in text
    assert "TCP a -> b" in text and "TCP c -> d" in text


# --- speed formatting --------------------------------------------------------

def test_format_speed_without_i18n_falls_back_to_bytes(app_activity):
    app_activity.i18n = None
    assert app_activity._format_speed(1500.0) == "1500 B/s"


def test_format_speed_with_i18n_returns_nonempty(app_activity):
    out = app_activity._format_speed(1_500_000.0)
    assert isinstance(out, str) and out  # routed through helpers.format_speed, no crash


# --- access-limited messaging ------------------------------------------------

def test_summary_appends_access_limited_suffix(app_activity):
    rows = [{"process_name": "p", "pid": 1, "download_bps": 0.0, "upload_bps": 0.0,
             "connection_count": 0, "endpoints": []}]
    app_activity._on_data_ready({"rows": rows, "total_down_bps": 0.0, "total_up_bps": 0.0,
                                 "updated_at": "12:00:00", "access_limited": True})
    suffix = app_activity._tr("APP_ACTIVITY_SUMMARY_LIMITED_SUFFIX",
                              "(limited access without admin rights)")
    assert suffix in app_activity.summary_label.text()


def test_empty_access_limited_shows_limited_message(app_activity):
    app_activity._on_data_ready({"rows": [], "access_limited": True})
    expected = app_activity._tr(
        "APP_ACTIVITY_ACCESS_LIMITED_MESSAGE",
        "Limited permissions detected. Showing only processes you can access.",
    )
    assert app_activity.summary_label.text() == expected


def test_worker_error_is_surfaced(app_activity):
    app_activity._on_worker_error("boom")
    assert "boom" in app_activity.summary_label.text()

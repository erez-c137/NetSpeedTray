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

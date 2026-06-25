"""
GUI smoke test for the history Graph window (pytest-qt, offscreen).

The graph window is the heaviest of the three (matplotlib + a DB-backed worker
thread), so this is a construct-and-build smoke: it proves the window, its
matplotlib UI, and its handlers wire up without crashing, with widget_state mocked
so no real database is touched. Catches construction/wiring regressions.
"""
from datetime import datetime

import pytest
from unittest.mock import MagicMock

from netspeedtray import constants
from netspeedtray.constants.i18n import I18nStrings
from netspeedtray.views.graph.window import GraphWindow


@pytest.fixture
def graph_window(qtbot, tmp_path):
    main = MagicMock()
    main.config = dict(constants.config.defaults.DEFAULT_CONFIG)
    main.config_manager = MagicMock()
    main.widget_state = MagicMock()
    # A real (empty) path so any db-size monitoring can stat it without error.
    db = tmp_path / "speed_history.db"
    db.write_bytes(b"")
    main.widget_state.db_worker.db_path = str(db)
    main.widget_state.get_earliest_data_timestamp.return_value = None
    main.widget_state.get_speed_history.return_value = []

    win = GraphWindow(main_widget=main, i18n=I18nStrings("en_US"),
                      session_start_time=datetime(2026, 1, 1, 12, 0, 0))
    qtbot.addWidget(win)
    return win


def test_graph_window_constructs(graph_window):
    """The graph window (matplotlib + worker) builds offscreen without crashing."""
    assert graph_window.ui is not None
    assert graph_window.renderer is not None
    assert graph_window.coordinator is not None

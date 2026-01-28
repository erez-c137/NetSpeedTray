"""
Unit tests for the data processing logic within the GraphWindow class.
"""

import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime, timedelta
from typing import Iterator

from PyQt6.QtWidgets import QApplication, QWidget

from netspeedtray import constants
from netspeedtray.views.graph import GraphWindow

# --- Fixtures ---
@pytest.fixture(scope="session")
def qapp() -> QApplication:
    """Provides a QApplication instance for the test session."""
    return QApplication.instance() or QApplication([])

@pytest.fixture
def graph_window_instance(qapp, mocker) -> Iterator[GraphWindow]:
    """
    Provides a properly initialized GraphWindow instance for logic testing.
    This fixture allows the __init__ to run but mocks the low-level,
    problematic dependencies, ensuring graceful cleanup.
    """
    mocker.patch('matplotlib.pyplot.subplots', return_value=(MagicMock(), (MagicMock(), MagicMock())))
    mocker.patch('netspeedtray.views.graph.window.FigureCanvas', return_value=QWidget())
    mocker.patch('netspeedtray.utils.helpers.get_app_asset_path', return_value=MagicMock())
    mocker.patch.object(GraphWindow, '_init_worker_thread', return_value=None)
    mocker.patch.object(GraphWindow, '_connect_signals', return_value=None)
    mocker.patch.object(GraphWindow, '_position_window', return_value=None)

    # Use main_widget and set parent=None to satisfy the constructor requirements.
    # Create a mock for the main widget, which is what the constructor now expects.
    mock_main_widget = MagicMock()
    mock_main_widget.config = constants.config.defaults.DEFAULT_CONFIG.copy()
    mock_main_widget.i18n = constants.i18n.get_i18n("en_US")
    mock_main_widget.widget_state = MagicMock()

    # Call the constructor with the correct arguments
    graph = GraphWindow(
        main_widget=mock_main_widget,
        parent=None, # Explicitly set parent to None
        i18n=mock_main_widget.i18n,
        session_start_time=datetime.now()
    )

    graph.logger = MagicMock()
    
    # The constructor creates a real QLabel. We must replace it with a mock
    # so that our test assertions will work.
    graph.stats_bar = MagicMock()
    
    yield graph
    
    graph.close()


def test_update_stats_bar_correctly_computes_values(graph_window_instance):
    """
    Tests the _update_stats_bar method to ensure it correctly calculates and
    formats max speeds and total bandwidth.
    """
    # ARRANGE
    graph = graph_window_instance
    
    now = datetime.now()
    history_data = [
        (now - timedelta(seconds=2), 1_000_000, 2_000_000),
        (now - timedelta(seconds=1), 2_500_000, 5_000_000),
        (now, 1_500_000, 3_000_000)
    ]
    
    with patch.object(GraphWindow, '_get_time_range_from_ui', return_value=(now - timedelta(seconds=2), now)):
        
        total_up = 5_000_000.0
        total_down = 10_000_000.0
        
        # ACT
        graph._update_stats_bar(history_data, total_up, total_down)
    
    # ASSERT
    graph.stats_bar.setText.assert_called_once()
    stats_text = graph.stats_bar.setText.call_args[0][0]
    
    assert "Max: ↑20.00 Mbps, ↓40.00 Mbps" in stats_text
    assert "Total: ↑4.77 MB, ↓9.54 MB" in stats_text


def test_update_stats_bar_handles_empty_data(graph_window_instance):
    """
    Tests that the stats bar shows the "No data" message when history is empty.
    """
    # ARRANGE
    graph = graph_window_instance
    
    # ACT
    graph._update_stats_bar([])
    
    # ASSERT
    graph.stats_bar.setText.assert_called_once_with(graph.i18n.NO_DATA_MESSAGE)
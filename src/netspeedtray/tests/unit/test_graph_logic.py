"""
Unit tests for the data processing logic within the GraphWindow class.
"""

import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime, timedelta
from typing import Iterator

from PyQt6.QtWidgets import QApplication

from netspeedtray import constants
from netspeedtray.views.graph import GraphWindow
from netspeedtray.views.graph.logic import GraphLogic

# --- Fixtures ---
@pytest.fixture(scope="session")
def qapp() -> QApplication:
    """Provides a QApplication instance for the test session."""
    return QApplication.instance() or QApplication([])

@pytest.fixture
def graph_window_instance(qapp) -> Iterator[GraphWindow]:
    """
    Provides a properly initialized GraphWindow instance for logic testing.
    This fixture allows the __init__ to run but mocks the low-level,
    problematic dependencies, ensuring graceful cleanup.
    """
    # Mock Renderer and Interaction Handler to avoid Matplotlib issues
    with patch('netspeedtray.views.graph.window.GraphRenderer', return_value=MagicMock()), \
         patch('netspeedtray.views.graph.window.GraphInteractionHandler', return_value=MagicMock()), \
         patch('netspeedtray.utils.helpers.get_app_asset_path', return_value=MagicMock()), \
         patch.object(GraphWindow, '_init_worker_thread', return_value=None), \
         patch.object(GraphWindow, '_connect_signals', return_value=None), \
         patch.object(GraphWindow, '_position_window', return_value=None):

        # Use main_widget and set parent=None to satisfy the constructor requirements.
        mock_main_widget = MagicMock()
        mock_main_widget.config = constants.config.defaults.DEFAULT_CONFIG.copy()
        # Ensure i18n is initialized
        mock_main_widget.i18n = constants.i18n.get_i18n("en_US")
        mock_main_widget.widget_state = MagicMock()

        # Call the constructor with the correct arguments
        graph = GraphWindow(
            main_widget=mock_main_widget,
            i18n=mock_main_widget.i18n,
            session_start_time=datetime.now()
        )

        # The constructor creates a real UI in setupUi. 
        # We must replace the labels with mocks so that our test assertions will work.
        graph.ui.max_stat_val = MagicMock()
        graph.ui.avg_stat_val = MagicMock()
        graph.ui.total_stat_val = MagicMock()
        graph.logger = MagicMock()
        
        yield graph
        
        graph.close()


def test_update_stats_bar_correctly_computes_values(graph_window_instance):
    """
    Tests the _update_stats_bar method to ensure it correctly calculates and
    updates the individual stat labels.
    """
    # ARRANGE
    graph = graph_window_instance
    
    now = datetime.now()
    history_data = [
        (now - timedelta(seconds=2), 1_000_000, 2_000_000),
        (now - timedelta(seconds=1), 2_500_000, 5_000_000),
        (now, 1_500_000, 3_000_000)
    ]
    
    # ACT
    total_up = 5_000_000.0
    total_down = 10_000_000.0
    graph._update_stats_bar(history_data, total_up, total_down)
    
    # ASSERT
    graph.ui.max_stat_val.setText.assert_called_once()
    graph.ui.avg_stat_val.setText.assert_called_once()
    graph.ui.total_stat_val.setText.assert_called_once()
    
    max_text = graph.ui.max_stat_val.setText.call_args[0][0]
    total_text = graph.ui.total_stat_val.setText.call_args[0][0]
    
    # Mbps calculations: 
    # Max: (2.5 * 8) / 1e6 = 20.0 (Up), (5.0 * 8) / 1e6 = 40.0 (Down)
    # The formatting is: "↓ {max_down:.1f}  ↑ {max_up:.1f} Mbps"
    assert "↓ 40.0" in max_text
    assert "↑ 20.0" in max_text
    
    # Total: 10,000,000 / 1024 / 1024 = 9.537
    # 5,000,000 / 1024 / 1024 = 4.768
    assert "↓ 9.5" in total_text
    assert "↑ 4.7" in total_text


def test_update_stats_bar_handles_empty_data(graph_window_instance):
    """
    Tests that the stats bar remains unchanged (returns early) when history is empty.
    """
    # ARRANGE
    graph = graph_window_instance
    graph.ui.max_stat_val.setText.reset_mock()
    
    # ACT
    graph._update_stats_bar([])
    
    # ASSERT
    graph.ui.max_stat_val.setText.assert_not_called()


def test_calculate_stats_preserves_real_peaks():
    """
    Peak stats should reflect true maxima from the timeline data.
    """
    history_data = [
        (1.0, 10.0, 20.0),
        (2.0, 20.0, 30.0),
        (3.0, 50_000_000.0, 100_000_000.0),
    ]

    stats = GraphLogic.calculate_stats(history_data)

    assert stats["max_up"] == pytest.approx(400.0)
    assert stats["max_down"] == pytest.approx(800.0)

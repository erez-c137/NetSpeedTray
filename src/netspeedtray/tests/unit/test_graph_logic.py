"""
Unit tests for the data processing logic within the GraphWindow class.

These tests verify the correctness of statistical calculations and data transformations
performed by the graph view, ensuring the data presented to the user is accurate.
"""

import pytest
from unittest.mock import MagicMock
from datetime import datetime, timedelta
from typing import Iterator

# The new, single, correct way to import all constants
from netspeedtray import constants

# This import is for type hinting and for the object we will patch.
from netspeedtray.views.graph import GraphWindow

# Fixtures
@pytest.fixture
def mock_graph_parent() -> MagicMock:
    """Provides a mock parent widget with necessary attributes."""
    parent = MagicMock()
    parent.config = constants.config.defaults.DEFAULT_CONFIG.copy()
    parent.i18n = constants.strings
    return parent

@pytest.fixture
def graph_window_instance(mock_graph_parent, mocker) -> Iterator[GraphWindow]:
    """
    Provides a minimally initialized GraphWindow instance for testing logic.
    Uses the 'mocker' fixture from pytest-mock to patch __init__.
    """
    # Use the mocker fixture to patch the __init__ method of the GraphWindow.
    # This prevents it from creating real UI elements which would require a QApplication.
    mocker.patch.object(GraphWindow, '__init__', lambda s, **kwargs: None)
    
    graph_window = GraphWindow()
    # After creating the "empty" instance, manually assign the attributes
    # that our logic tests will depend on.
    graph_window._parent = mock_graph_parent
    graph_window.i18n = mock_graph_parent.i18n
    graph_window.logger = MagicMock()
    
    yield graph_window

def test_calculate_period_stats_correctly_computes_values(graph_window_instance):
    """
    Tests the _calculate_period_stats method to ensure it correctly calculates
    max speeds in Mbps and total data transferred from a list of Bytes/sec readings.
    """
    # ARRANGE
    graph = graph_window_instance
    
    now = datetime.now()
    period_data = [
        (now - timedelta(seconds=2), 1_000_000, 2_000_000),      # 1 MB/s up, 2 MB/s down
        (now - timedelta(seconds=1), 2_500_000, 5_000_000),      # 2.5 MB/s up (max), 5 MB/s down (max)
        (now, 1_500_000, 3_000_000)                             # 1.5 MB/s up, 3 MB/s down
    ]
    
    # ACT
    stats = graph._calculate_period_stats(period_data)
    
    # ASSERT
    # Max Upload = 2,500,000 Bytes/sec = (2.5 * 8) = 20 Mbps
    assert stats["max_upload"] == pytest.approx(20.0)
    # Max Download = 5,000,000 Bytes/sec = (5 * 8) = 40 Mbps
    assert stats["max_download"] == pytest.approx(40.0)
    
    # Total Upload Bytes = (1.75 * 1M) + (2.0 * 1M) = 3,750,000 Bytes
    # Our helper format_data_size correctly converts this to 3.58 MB (using 1024 divisor)
    assert stats["total_upload"] == pytest.approx(3.58, abs=0.01) 
    assert stats["total_upload_unit"] == "MB"
    # Total Download Bytes = (3.5 * 1M) + (4.0 * 1M) = 7,500,000 Bytes -> 7.15 MB
    assert stats["total_download"] == pytest.approx(7.15, abs=0.01)
    assert stats["total_download_unit"] == "MB"

def test_calculate_period_stats_handles_empty_data(graph_window_instance):
    """
    Tests that _calculate_period_stats returns a default, zeroed-out dictionary
    when it receives no data, preventing crashes.
    """
    graph = graph_window_instance
    stats = graph._calculate_period_stats([])
    
    # The actual return dict was missing a key, this test uncovered it.
    # We must check against the *actual* expected return value.
    expected_keys = {
        "max_upload", "max_download", "total_upload", 
        "total_upload_unit", "total_download", "total_download_unit"
    }
    assert set(stats.keys()) == expected_keys
    assert stats["total_upload_unit"] == "B"
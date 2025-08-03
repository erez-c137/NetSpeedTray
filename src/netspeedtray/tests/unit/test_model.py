"""
Unit tests for the Model class in the NetSpeedTray application.
"""
import pytest
from unittest.mock import MagicMock, patch
from typing import Iterator
import psutil
import time

from netspeedtray.core.model import Model, NetworkSpeed

class MockNetIO:
    """A simple mock object to simulate psutil's snetio counter objects."""
    def __init__(self, bytes_sent, bytes_recv):
        self.bytes_sent = bytes_sent
        self.bytes_recv = bytes_recv

@pytest.fixture
def model_instance() -> Model:
    """Provides a fresh Model instance, patching its interface detection."""
    with patch.object(Model, '_get_available_interfaces', return_value=["eth0", "lo"]):
        model = Model()
        model.set_interfaces(interface_mode="all", selected_interfaces=[])
        return model

@pytest.fixture
def mock_psutil() -> Iterator[MagicMock]:
    """Mocks the psutil.net_io_counters function for the duration of a test."""
    with patch("netspeedtray.core.model.psutil.net_io_counters") as mock:
        yield mock

def test_initial_call_returns_zero_speed(model_instance: Model, mock_psutil: MagicMock):
    """
    Tests that the very first call returns a NetworkSpeed object with zero speeds.
    """
    mock_psutil.return_value = {"eth0": MockNetIO(bytes_sent=1000, bytes_recv=2000)}
    speeds = model_instance.get_network_speeds()
    assert isinstance(speeds, NetworkSpeed)
    assert speeds.download_speed == 0.0
    assert speeds.upload_speed == 0.0

def test_speed_calculation_over_time(model_instance: Model, mock_psutil: MagicMock):
    """
    Tests that speeds are correctly calculated over a time interval by controlling
    the model's internal time state.
    """
    # Manually control the time state for a perfectly reproducible test.
    
    # Arrange Step 1: Set the initial time and get the first data point.
    model_instance.last_time = 1000.0
    mock_psutil.return_value = {"eth0": MockNetIO(bytes_sent=1000, bytes_recv=2000)}
    model_instance.get_network_speeds()

    # Arrange Step 2: Manually advance the mock monotonic clock and prepare the next data point.
    with patch('time.monotonic', return_value=1002.0):
        mock_psutil.return_value = {"eth0": MockNetIO(bytes_sent=3000, bytes_recv=5000)}
        
        # Act: Get the calculated speeds.
        speeds = model_instance.get_network_speeds()

    # Assert: Check that the calculation is correct.
    # Expected download: (5000 - 2000) bytes / (1002.0 - 1000.0)s = 1500.0 bytes/s
    assert speeds.download_speed == 1500.0
    # Expected upload: (3000 - 1000) bytes / 2 seconds = 1000.0 bytes/s
    assert speeds.upload_speed == 1000.0

def test_exception_during_speed_calculation_raises_runtime_error(model_instance: Model, mock_psutil: MagicMock):
    """
    Tests that a psutil error is caught and re-raised as a controlled RuntimeError.
    """
    mock_psutil.side_effect = psutil.AccessDenied()
    
    with pytest.raises(RuntimeError, match="Failed to calculate speeds"):
        model_instance.get_network_speeds()
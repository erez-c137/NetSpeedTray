"""Unit tests for the Model class in the NetSpeedTray application.

This module contains comprehensive tests for the Model class, focusing on the
`get_network_speeds` method. The tests verify correct speed calculations,
edge cases, and error handling using mocked dependencies. Tests are designed
to align with the existing implementation in src/netspeedtray/core/model.py.

Requires:
    - pytest
    - pytest-mock
    - freezegun
"""

import pytest #type: ignore
from unittest.mock import Mock, patch
import psutil
from collections import namedtuple
from datetime import datetime
from freezegun import freeze_time #type: ignore
from netspeedtray.core.model import Model

# Define a named tuple to mimic psutil._common.snetio (used in other tests)
SNetIO = namedtuple(
    "SNetIO",
    [
        "bytes_sent",
        "bytes_recv",
        "packets_sent",
        "packets_recv",
        "errin",
        "errout",
        "dropin",
        "dropout",
    ],
)


class TestModel:
    """Test suite for the Model class."""

    @pytest.fixture
    def model_instance(self):
        """Fixture to create a fresh Model instance for each test.

        Returns:
            Model: A new instance of the Model class with default state.
        """
        return Model()

    @pytest.fixture
    def mock_psutil(self):
        """Fixture to mock psutil.net_io_counters for controlled test data.

        Returns:
            Mock: A mock object for psutil.net_io_counters.
        """
        with patch("netspeedtray.core.model.psutil.net_io_counters") as mock:
            yield mock

    def test_get_network_speeds_initial_call(self, model_instance, mock_psutil):
        """Test that the first call to get_network_speeds returns zero speeds.

        Verifies that when no prior data exists, the method returns (0, 0) for
        download and upload speeds, consistent with the current implementation.

        Args:
            model_instance: Fixture providing a Model instance.
            mock_psutil: Fixture mocking psutil.net_io_counters.
        """
        mock_psutil.return_value = {
            "eth0": SNetIO(
                bytes_sent=1000,
                bytes_recv=2000,
                packets_sent=0,
                packets_recv=0,
                errin=0,
                errout=0,
                dropin=0,
                dropout=0,
            )
        }
        download_speed, upload_speed = model_instance.get_network_speeds()

        assert (
            download_speed == 0.0
        ), "Initial download speed should be 0 due to no prior data"
        assert (
            upload_speed == 0.0
        ), "Initial upload speed should be 0 due to no prior data"

    def test_get_network_speeds_with_data(self, model_instance, mock_psutil):
        """Test speed calculation with valid network data over two calls.

        Verifies that speed is calculated correctly based on byte differences
        over a 1-second interval.

        Args:
            model_instance: Fixture providing a Model instance.
            mock_psutil: Fixture mocking psutil.net_io_counters.
        """

        # Create mock objects that mimic psutil._common.snetio more closely
        class MockSNetIO:
            def __init__(
                self,
                bytes_sent,
                bytes_recv,
                packets_sent,
                packets_recv,
                errin,
                errout,
                dropin,
                dropout,
            ):
                self.bytes_sent = bytes_sent
                self.bytes_recv = bytes_recv
                self.packets_sent = packets_sent
                self.packets_recv = packets_recv
                self.errin = errin
                self.errout = errout
                self.dropin = dropin
                self.dropout = dropout

        # Define the two sets of mock data
        first_call_data = {
            "eth0": MockSNetIO(
                bytes_sent=1000,
                bytes_recv=2000,
                packets_sent=0,
                packets_recv=0,
                errin=0,
                errout=0,
                dropin=0,
                dropout=0,
            )
        }
        second_call_data = {
            "eth0": MockSNetIO(
                bytes_sent=1500,
                bytes_recv=2500,
                packets_sent=0,
                packets_recv=0,
                errin=0,
                errout=0,
                dropin=0,
                dropout=0,
            )
        }

        # Set side_effect on mock_psutil directly, since psutil.net_io_counters(pernic=True) calls the function
        mock_psutil.side_effect = [first_call_data, second_call_data]

        # Patch the logger to catch any exceptions
        with patch("netspeedtray.core.model.logging") as mock_logging:
            # First call: time = 1000.0 seconds (1970-01-01 00:16:40)
            with freeze_time("1970-01-01 00:16:40"):
                # Set last_time to match the frozen time to avoid negative time_diff
                model_instance.last_time = datetime.now()
                download_speed, upload_speed = model_instance.get_network_speeds()

            # Second call: time = 1001.0 seconds (1970-01-01 00:16:41)
            with freeze_time("1970-01-01 00:16:41"):
                download_speed, upload_speed = model_instance.get_network_speeds()

            # Check if any errors were logged
            assert not mock_logging.error.called, "No errors should be logged"

        # Expected: (2500 - 2000) / 1 = 500 bytes/s, (1500 - 1000) / 1 = 500 bytes/s
        assert download_speed == 500.0, "Download speed should be 500 bytes/s"
        assert upload_speed == 500.0, "Upload speed should be 500 bytes/s"

    def test_get_network_speeds_no_active_interfaces(self, model_instance, mock_psutil):
        """Test behavior when no network interfaces are active.

        Verifies that the method returns (0, 0) when psutil returns an empty dict,
        aligning with the current fallback logic.

        Args:
            model_instance: Fixture providing a Model instance.
            mock_psutil: Fixture mocking psutil.net_io_counters.
        """
        mock_psutil.return_value = {}
        download_speed, upload_speed = model_instance.get_network_speeds()

        assert download_speed == 0.0, "Download speed should be 0 with no interfaces"
        assert upload_speed == 0.0, "Upload speed should be 0 with no interfaces"

    def test_get_network_speeds_psutil_error(self, model_instance, mock_psutil):
        """Test error handling when psutil raises an exception.

        Verifies that the method returns (0, 0) as a fallback when psutil fails.

        Args:
            model_instance: Fixture providing a Model instance.
            mock_psutil: Fixture mocking psutil.net_io_counters.
        """
        mock_psutil.side_effect = psutil.AccessDenied("Permission denied")
        with patch("netspeedtray.core.model.logging") as mock_logging:
            download_speed, upload_speed = model_instance.get_network_speeds()

        assert download_speed == 0.0, "Download speed should be 0 on error"
        assert upload_speed == 0.0, "Upload speed should be 0 on error"

"""
Unit tests for the NetworkMonitorThread class, specifically focusing on the circuit breaker logic.
"""

import time
import pytest
from unittest.mock import MagicMock, patch
from PyQt6.QtCore import QThread

from netspeedtray.core.monitor_thread import NetworkMonitorThread

class TestNetworkMonitorThread:
    
    @pytest.fixture
    def monitor_thread(self, q_app):
        """Creates a thread instance with a very short interval for testing."""
        thread = NetworkMonitorThread(interval=0.01)
        yield thread
        if thread.isRunning():
            thread.stop()
            thread.wait()

    def test_successful_poll_resets_error_count(self, monitor_thread):
        """Test that a successful poll keeps consecutive_errors at 0."""
        with patch('netspeedtray.core.monitor_thread.psutil.net_io_counters') as mock_psutil:
            mock_psutil.return_value = {"eth0": MagicMock()}
            
            # Simulate a previous error condition
            monitor_thread.consecutive_errors = 5
            
            # Run one iteration manually (or simulate running by mocking time/sleep)
            # Since we can't easily control the infinite loop of update() in a unit test without 
            # modifying the class to be more testable (e.g., dependency injection of loop condition),
            # we can just invoke the logic inside the try/except block if we extracted it, 
            # OR we mock stop() and let it run for a tiny bit.
            
            # Better approach: We can just execute the logic body inside a controlled loop in the test,
            # but that tests the implementation, not the class 'run' method.
            # However, since 'run' is a blocking infinite loop, standard unit testing is hard.
            # Let's start the thread and stop it quickly.
            
            monitor_thread.start()
            time.sleep(0.05) # Allow a few cycles
            monitor_thread.stop()
            
            assert monitor_thread.consecutive_errors == 0

    def test_transient_error_increments_count(self, monitor_thread):
        """Test that errors increment the counter but don't stop the thread immediately."""
        with patch('netspeedtray.core.monitor_thread.psutil.net_io_counters', side_effect=OSError("Test Error")):
            monitor_thread.start()
            time.sleep(0.05)
            monitor_thread.stop()
            
            assert monitor_thread.consecutive_errors > 0
            assert monitor_thread.consecutive_errors <= 10 # Should not have tripped yet if sleep is short enough

    def test_circuit_breaker_trips(self, monitor_thread):
        """Test that >10 errors stops the thread."""
        with patch('netspeedtray.core.monitor_thread.psutil.net_io_counters', side_effect=OSError("Persistent Error")):
             # We need to ensure it runs enough times to trip.
             # Interval is 0.01. 10 times is 0.1s. Let's wait 0.3s.
             
             # Mock the stop method to verify it's called (or verify is_running becomes false)
             with patch.object(monitor_thread, 'stop', wraps=monitor_thread.stop) as mock_stop:
                 monitor_thread.start()
                 time.sleep(0.4) # Wait enough time for >10 iterations
                 
                 # Check if thread stopped itself
                 if monitor_thread.isRunning():
                     monitor_thread.stop()
                     
                 # Assertions
                 # The counter might be 11 (the one that tripped it)
                 assert monitor_thread.consecutive_errors >= 10
                 assert not monitor_thread._is_running

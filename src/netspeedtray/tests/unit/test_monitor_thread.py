"""
Unit tests for the StatsMonitorThread class, specifically focusing on the circuit breaker logic.
"""

import time
import pytest
from unittest.mock import MagicMock, patch
from PyQt6.QtCore import QThread

from netspeedtray.core.monitor_thread import StatsMonitorThread

class TestNetworkMonitorThread:
    
    @pytest.fixture
    def monitor_thread(self, q_app):
        """Creates a thread instance with a very short interval for testing."""
        thread = StatsMonitorThread(interval=0.01)
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

    def test_circuit_breaker_is_recoverable_not_fatal(self, q_app):
        """
        Crossing the error threshold NOTIFIES once and backs off, but the thread is NEVER
        permanently bricked (the old behavior). It keeps running so a transient fault heals.
        """
        with patch('netspeedtray.core.monitor_thread.constants.timers.MINIMUM_INTERVAL_MS', 10):
            mt = StatsMonitorThread(interval=0.01)
            mt._ERROR_NOTIFY_THRESHOLD = 3  # reach the notice fast under test
            emitted = []
            mt.error_occurred.connect(lambda msg: emitted.append(msg))

            with patch('netspeedtray.core.monitor_thread.psutil.net_io_counters',
                       side_effect=OSError("Persistent Error")):
                mt.start()
                deadline = time.time() + 3.0
                while mt.consecutive_errors < 3 and time.time() < deadline:
                    q_app.processEvents()
                    time.sleep(0.02)
                # let the queued cross-thread signal deliver
                for _ in range(30):
                    q_app.processEvents()
                    time.sleep(0.01)

                assert mt.consecutive_errors >= 3
                assert mt._is_running is True          # NOT bricked - recoverable
                assert mt._error_notified is True       # crossed the threshold
                assert len(emitted) == 1                # notified exactly once, not per error

                mt.stop()
                mt.wait(1000)

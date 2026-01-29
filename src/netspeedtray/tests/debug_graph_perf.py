
import sys
import os
import time
import logging
import psutil
from datetime import datetime, timedelta
from unittest.mock import MagicMock
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QTimer

# Add src to path
# Adjusted path for: src/netspeedtray/tests/debug_graph_perf.py -> src
src_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../'))
sys.path.insert(0, src_path)

try:
    from netspeedtray.views.graph.window import GraphWindow
    from netspeedtray.core.widget_state import WidgetState, SpeedDataSnapshot, AggregatedSpeedData
    from netspeedtray import constants
except ImportError as e:
    print(f"Import Error: {e}")
    print(f"Sys Path: {sys.path}")
    sys.exit(1)

# Setup logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger("PerfTest")

def get_memory_usage():
    process = psutil.Process(os.getpid())
    return process.memory_info().rss / 1024 / 1024  # MB

def test_graph_performance():
    app = QApplication(sys.argv)
    
    # Mock Main Widget & Config
    mock_main = MagicMock()
    mock_main.config = {
        "dark_mode": True,
        "live_update": False, # Manual updates for test
        "history_minutes": 60,
        "update_rate": 1.0,
        "keep_data": 30
    }
    
    # Mock WidgetState with fake data
    mock_state = MagicMock()
    mock_state.get_earliest_data_timestamp.return_value = datetime.now() - timedelta(days=365)
    
    # Generate fake history (1000 points)
    fake_history = []
    now = datetime.now()
    for i in range(1000):
        ts = (now - timedelta(seconds=i)).timestamp()
        fake_history.append((ts, float(i * 1024), float(i * 2048)))
    
    mock_state.get_speed_history.return_value = fake_history
    mock_state.get_total_bandwidth_for_period.return_value = (1000.0, 2000.0)
    
    # Mock Worker inside State (since Window uses state.db_worker.db_path)
    mock_state.db_worker.db_path = "test.db"
    
    # Mock WidgetState on main
    mock_main.widget_state = mock_state
    
    # Mock I18n
    class MockI18n:
        def __getattr__(self, name): return f"I18N_{name}"
    mock_i18n = MockI18n()
    
    print(f"Initial Memory: {get_memory_usage():.2f} MB")
    
    # Create Window
    window = GraphWindow(mock_main, i18n=mock_i18n)
    window.show()
    
    print(f"Window Shown Memory: {get_memory_usage():.2f} MB")
    
    # Test Loop: Simulate slider changes (Update History Period)
    update_count = 0
    max_updates = 50
    
    def run_update_loop():
        nonlocal update_count
        if update_count >= max_updates:
            print(f"Final Memory: {get_memory_usage():.2f} MB")
            print("Test Complete. Closing...")
            window.close()
            app.quit()
            return

        start_time = time.perf_counter()
        
        # Simulate changing slider value cyclicly (0 to 6)
        period_val = update_count % 7
        window._on_history_slider_released(period_val)
        
        # Since logic is async (worker thread), we can't easily measure full end-to-end here without signals.
        # But we can measure the MAIN THREAD triggering time.
        
        dur = time.perf_counter() - start_time
        mem = get_memory_usage()
        print(f"Iteration {update_count}: Trigger Time={dur*1000:.2f}ms, Mem={mem:.2f} MB")
        
        update_count += 1
        QTimer.singleShot(100, run_update_loop) # Run every 100ms

    QTimer.singleShot(1000, run_update_loop)
    try:
        app.exec()
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    test_graph_performance()

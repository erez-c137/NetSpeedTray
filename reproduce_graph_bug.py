import sqlite3
import os
from datetime import datetime, timedelta
from pathlib import Path
from netspeedtray.core.widget_state import WidgetState

def reproduce():
    db_path = Path("test_repro.db")
    if db_path.exists():
        os.remove(db_path)
    
    config = {"history_minutes": 30, "update_rate": 1.0, "keep_data": 365}
    
    # We need to mock get_app_data_path because WidgetState uses it
    from unittest.mock import patch
    with patch('netspeedtray.core.widget_state.get_app_data_path', return_value=str(Path("."))):
        state = WidgetState(config)
        # Wait for initialization
        import time
        time.sleep(1)
        
        now = datetime.now()
        
        print("--- Testing get_speed_history (Speed Calculation) ---")
        # Scenario: 5 seconds of 100 KB/s data in the same minute
        # If we request 'minute' resolution, the speed should be 100 KB/s (or 500/60 if we want average over full minute)
        # Currently, it likely sums them to 500 KB/s or more.
        
        base_ts = int(now.timestamp()) - 100 # 100 seconds ago (definitely in RAW tier)
        interface = "eth0"
        
        # USE THE ACTUAL DB PATH THAT WIDGETSTATE CREATED
        db_path_actual = state.db_worker.db_path
        conn = sqlite3.connect(db_path_actual)
        cursor = conn.cursor()
        for i in range(5):
            cursor.execute("INSERT INTO speed_history_raw VALUES (?, ?, ?, ?)", 
                           (base_ts + i, interface, 100.0, 100.0))
        conn.commit()
        
        # Query with minute resolution
        start = now - timedelta(minutes=5)
        history = state.get_speed_history(start_time=start, end_time=now, interface_name="all", resolution='minute')
        
        for ts, up, down in history:
            if up > 0:
                print(f"Minute Bin {ts}: Upload Speed = {up} Bytes/sec")
                if up > 110: # Should be ~100 if it was aggregating correctly by AVG
                    print(f"BUG DETECTED: Speed is {up}, expected ~100. It's likely summing raw values.")
        
        print("\n--- Testing get_total_bandwidth_for_period ---")
        # Scenario: 10 seconds of 100 KB/s data in a minute that has been aggregated.
        # Total bytes should be 1000 Bytes.
        
        old_now = now - timedelta(hours=25)
        old_ts_base = int(old_now.timestamp())
        
        # Add 10 samples of 100 Bytes/sec = 1000 Total Bytes
        for i in range(10):
             cursor.execute("INSERT INTO speed_history_raw VALUES (?, ?, ?, ?)", 
                           (old_ts_base + i, interface, 100.0, 100.0))
        conn.commit()
        
        # Trigger maintenance to move to MINUTE table
        state.trigger_maintenance(now=now)
        time.sleep(2) # wait for worker to finish maintenance
        
        # Check total bandwidth
        start_old = old_now - timedelta(minutes=5)
        end_old = old_now + timedelta(minutes=5)
        total_up, total_down = state.get_total_bandwidth_for_period(start_time=start_old, end_time=end_old, interface_name=interface)
        
        print(f"Total Upload for old period: {total_up} Bytes")
        if total_up == 6000.0:
            print(f"BUG DETECTED: Total is 6000, expected 1000. It's assuming 60 seconds per minute record.")
        elif total_up == 100.0:
            print(f"BUG DETECTED: Total is 100, it's just getting the average but not multiplying by duration correctly (though the bug above is more likely).")

        state.cleanup()
        conn.close()

if __name__ == "__main__":
    reproduce()

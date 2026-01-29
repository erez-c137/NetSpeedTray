
from PyQt6.QtCore import QObject, pyqtSignal
import logging
import time
from typing import List, Tuple
from typing import List, Tuple, Optional
from datetime import datetime

class GraphDataWorker(QObject):
    """
    Processes graph data in a background thread to keep the UI responsive.
    """
    data_ready = pyqtSignal(list, float, float, int) # history_data, total_up, total_down, sequence_id
    error = pyqtSignal(str)


    def __init__(self, widget_state):
        """
        Initializes the worker.
        
        Args:
            widget_state: A direct reference to the application's WidgetState object.
        """
        super().__init__()
        self.widget_state = widget_state
        self._last_received_id = -1


    def process_data(self, start_time: Optional[datetime], end_time: datetime, interface_name: Optional[str], is_session_view: bool = False, sequence_id: int = 0):
        """Processes speed history data in a background thread."""
        try:
            # Check if this request is already obsolete
            if sequence_id < self._last_received_id:
                return
            self._last_received_id = sequence_id

            if not self.widget_state:
                self.error.emit("Data source (WidgetState) not available.")
                return

            from netspeedtray.utils.helpers import downsample_data

            total_up, total_down = 0.0, 0.0

            if is_session_view:
                # OPTIMIZATION: Use the pre-calculated aggregated history from WidgetState.
                aggregated_data = self.widget_state.get_aggregated_speed_history()
                
                processed_history = []
                for d in aggregated_data:
                    ts = float(d.timestamp.timestamp())
                    up = float(d.upload)
                    down = float(d.download)
                    processed_history.append((ts, up, down))
                    total_up += up
                    total_down += down
                
                history_data = processed_history
            else:
                # For all other timelines, get data from the database.
                # db_start = time.perf_counter()
                history_data = self.widget_state.get_speed_history(
                    start_time=start_time, end_time=end_time, 
                    interface_name=interface_name,
                    return_raw=True
                )
                # logging.getLogger(__name__).debug(f"[PERF] DB Fetch DONE (len={len(history_data)}) (dur={time.perf_counter() - db_start:.4f}s)")
                

                # Fetch totals from DB as well (DURING the worker thread to avoid UI freeze)
                # totals_start = time.perf_counter()
                total_up, total_down = self.widget_state.get_total_bandwidth_for_period(
                    start_time=start_time,
                    end_time=end_time,
                    interface_name=interface_name
                )
                # logging.getLogger(__name__).debug(f"[PERF] DB Totals DONE (dur={time.perf_counter() - totals_start:.4f}s)")

                # PERFORMANCE OPTIMIZATION: Downsample large datasets.
                # 500 points is the "sweet spot" for clarity on most monitors.
                if len(history_data) > 500:
                    # ds_start = time.perf_counter()
                    history_data = downsample_data(history_data, 500)
                    # logging.getLogger(__name__).debug(f"[PERF] Downsampling DONE (dur={time.perf_counter() - ds_start:.4f}s)")

            if not history_data or len(history_data) < 2:
                self.data_ready.emit([], 0.0, 0.0, sequence_id)
                return

            # Pass the processed data and pre-calculated totals back to the UI.
            self.data_ready.emit(history_data, total_up, total_down, sequence_id)
        except Exception as e:
            logging.getLogger(__name__).error(f"Error in data worker: {e}", exc_info=True)
            self.error.emit(str(e))

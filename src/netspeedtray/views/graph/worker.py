
from PyQt6.QtCore import QObject, pyqtSignal
import logging
import time
from typing import List, Tuple, Dict, Any
from typing import List, Tuple, Optional
from datetime import datetime

class GraphDataWorker(QObject):
    """
    Processes graph data in a background thread to keep the UI responsive.
    
    TODO: FUTURE OPTIMIZATION - Implement Matplotlib blitting for real-time updates.
    Blitting only redraws changed parts of the canvas, which can significantly
    reduce render time for live data updates. See: https://matplotlib.org/stable/users/explain/animations/blitting.html
    """
    data_ready = pyqtSignal(list, float, float, int) # history_data, total_up, total_down, sequence_id
    error = pyqtSignal(str)

    # Maximum data points to return (prevents excessive rendering time)
    MAX_DATA_POINTS = 2000

    def __init__(self, widget_state):
        """
        Initializes the worker.
        
        Args:
            widget_state: A direct reference to the application's WidgetState object.
        """
        super().__init__()
        self.widget_state = widget_state
        self.logger = logging.getLogger(__name__)
        self._last_received_id = -1
        
        # Timeline data cache: {period_key: (data, total_up, total_down, timestamp)}
        # Cache expires after 30 seconds to ensure data freshness
        self._cache: Dict[str, tuple] = {}
        self._cache_ttl = 30.0  # seconds


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



            total_up, total_down = 0.0, 0.0

            if is_session_view:
                # OPTIMIZATION: Use the pre-calculated aggregated history from WidgetState.
                aggregated_data = self.widget_state.get_aggregated_speed_history()
                
                start_ts = start_time.timestamp() if start_time else 0
                end_ts = end_time.timestamp() if end_time else float('inf')

                processed_history = []
                for d in aggregated_data:
                    ts = float(d.timestamp.timestamp())
                    # Filter for zoom if applicable
                    if ts < start_ts or ts > end_ts:
                        continue

                    up = float(d.upload)
                    down = float(d.download)
                    processed_history.append((ts, up, down))
                    total_up += up
                    total_down += down
                
                history_data = processed_history
            else:
                # For all other timelines, get data from the database.
                history_data = self.widget_state.get_speed_history(
                    start_time=start_time, end_time=end_time, 
                    interface_name=interface_name
                )
                
                # Convert list of (datetime, up, down) to list of (float_timestamp, up, down)
                history_data = [(dt.timestamp(), up, down) for dt, up, down in history_data]
                

                # Fetch totals from DB as well (DURING the worker thread to avoid UI freeze)
                total_up, total_down = self.widget_state.get_total_bandwidth_for_period(
                    start_time=start_time,
                    end_time=end_time,
                    interface_name=interface_name
                )

            # Smart Downsampling: Cap at MAX_DATA_POINTS for rendering performance
            # Uses stride-based sampling to preserve temporal distribution
            if len(history_data) > self.MAX_DATA_POINTS:
                stride = len(history_data) // self.MAX_DATA_POINTS
                history_data = history_data[::stride]

            if not history_data or len(history_data) < 2:
                self.data_ready.emit([], 0.0, 0.0, sequence_id)
                return

            # Pass the processed data and pre-calculated totals back to the UI.
            self.data_ready.emit(history_data, total_up, total_down, sequence_id)
        except Exception as e:
            logging.getLogger(__name__).error(f"Error in data worker: {e}", exc_info=True)
            self.error.emit(str(e))

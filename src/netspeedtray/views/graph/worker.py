
from PyQt6.QtCore import QObject, pyqtSignal
import logging
import time
from typing import List, Tuple, Dict, Any
from typing import List, Tuple, Optional
from datetime import datetime
from netspeedtray.views.graph.request import DataRequest

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

    @staticmethod
    def _preserve_global_peaks(data: List[Tuple[float, float, float]], sampled: List[Tuple[float, float, float]]) -> List[Tuple[float, float, float]]:
        """
        Ensures stride downsampling does not drop global upload/download peaks.
        """
        if not data or not sampled:
            return sampled

        peak_up = max(data, key=lambda point: float(point[1] or 0.0))
        peak_down = max(data, key=lambda point: float(point[2] or 0.0))

        sampled_set = set(sampled)
        if peak_up not in sampled_set:
            sampled.append(peak_up)
            sampled_set.add(peak_up)
        if peak_down not in sampled_set:
            sampled.append(peak_down)

        return sorted(sampled, key=lambda point: point[0])

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


    def process_data(self, request: DataRequest):
        """
        Processes speed history data in a background thread.
        
        Args:
            request: DataRequest object containing all parameters for data fetching
        """
        try:
            # Validate request
            if not isinstance(request, DataRequest):
                self.error.emit(f"Invalid request type: {type(request).__name__}. Expected DataRequest.")
                return
            
            # Check if this request is already obsolete
            if request.sequence_id < self._last_received_id:
                return
            self._last_received_id = request.sequence_id

            if not self.widget_state:
                self.error.emit("Data source (WidgetState) not available.")
                return

            if request.stat_type == "overview":
                # Multi-dataset fetch for Overview tab
                if request.is_session_view:
                    net_data = self.widget_state.get_aggregated_speed_history()
                    cpu_data = self.widget_state.cpu_history
                    gpu_data = self.widget_state.gpu_history
                    
                    start_ts = request.start_time.timestamp() if request.start_time else 0
                    end_ts = request.end_time.timestamp()
                    
                    history_data = {
                        "network": [(d.timestamp.timestamp(), d.upload, d.download) for d in net_data if start_ts <= d.timestamp.timestamp() <= end_ts],
                        "cpu": [(d.timestamp.timestamp(), d.value, 0.0) for d in cpu_data if start_ts <= d.timestamp.timestamp() <= end_ts],
                        "gpu": [(d.timestamp.timestamp(), d.value, 0.0) for d in gpu_data if start_ts <= d.timestamp.timestamp() <= end_ts]
                    }
                else:
                    net_data = self.widget_state.get_speed_history(request.start_time, request.end_time, request.interface_name)
                    cpu_data = self.widget_state.get_hardware_history("cpu", request.start_time, request.end_time)
                    gpu_data = self.widget_state.get_hardware_history("gpu", request.start_time, request.end_time)
                    
                    history_data = {
                        "network": [(dt.timestamp(), up, dw) for dt, up, dw in net_data],
                        "cpu": [(dt.timestamp(), val, 0.0) for dt, val in cpu_data],
                        "gpu": [(dt.timestamp(), val, 0.0) for dt, val in gpu_data]
                    }
                # For Overview, we don't downsample here for simplicity, or handle per-key
                # We'll return early with this dict
                self.data_ready.emit(history_data, 0.0, 0.0, request.sequence_id)
                return

            if request.stat_type in ("cpu", "gpu"):
                if request.is_session_view:
                    # In-memory session data
                    aggregated_data = self.widget_state.cpu_history if request.stat_type == "cpu" else self.widget_state.gpu_history
                    start_ts = request.start_time.timestamp() if request.start_time else 0
                    end_ts = request.end_time.timestamp()
                    
                    history_data = []
                    for d in aggregated_data:
                        ts = d.timestamp.timestamp()
                        if ts < start_ts or ts > end_ts: continue
                        history_data.append((ts, d.value, 0.0)) # Hardware is single value, reuse second slot for convenience or pass 0
                else:
                    # Database data
                    raw_history = self.widget_state.get_hardware_history(request.stat_type, request.start_time, request.end_time)
                    history_data = [(dt.timestamp(), val, 0.0) for dt, val in raw_history]

            elif request.is_session_view:
                # OPTIMIZATION: Use the pre-calculated aggregated history from WidgetState.
                aggregated_data = self.widget_state.get_aggregated_speed_history()
                
                start_ts = request.start_time.timestamp() if request.start_time else 0
                end_ts = request.end_time.timestamp()

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
                    start_time=request.start_time, 
                    end_time=request.end_time, 
                    interface_name=request.interface_name
                )
                
                # Convert list of (datetime, up, down) to list of (float_timestamp, up, down)
                history_data = [(dt.timestamp(), up, down) for dt, up, down in history_data]
                
                # Fetch totals from DB as well (DURING the worker thread to avoid UI freeze)
                total_up, total_down = self.widget_state.get_total_bandwidth_for_period(
                    start_time=request.start_time,
                    end_time=request.end_time,
                    interface_name=request.interface_name
                )

            # Smart Downsampling: Cap at MAX_DATA_POINTS for rendering performance
            if len(history_data) > self.MAX_DATA_POINTS:
                stride = len(history_data) // self.MAX_DATA_POINTS
                downsampled = history_data[::stride]
                if request.stat_type == "network":
                    history_data = self._preserve_global_peaks(history_data, downsampled)
                else:
                    history_data = downsampled

            if not history_data or len(history_data) < 2:
                self.data_ready.emit([], 0.0, 0.0, request.sequence_id)
                return

            # Pass the processed data and pre-calculated totals back to the UI.
            self.data_ready.emit(history_data, total_up, total_down, request.sequence_id)
        except Exception as e:
            logging.getLogger(__name__).error(f"Error in data worker: {e}", exc_info=True)
            self.error.emit(str(e))

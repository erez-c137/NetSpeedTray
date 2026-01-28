
from PyQt6.QtCore import QObject, pyqtSignal
import logging
from typing import List, Tuple
from datetime import datetime

class GraphDataWorker(QObject):
    """
    Processes graph data in a background thread to keep the UI responsive.
    """
    data_ready = pyqtSignal(list, float, float) # history_data, total_up, total_down
    error = pyqtSignal(str)


    def __init__(self, widget_state):
        """
        Initializes the worker.
        
        Args:
            widget_state: A direct reference to the application's WidgetState object.
        """
        super().__init__()
        self.widget_state = widget_state


    def process_data(self, start_time, end_time, interface_to_query, is_session_view):
        """
        The main data processing method. Intelligently selects data source
        and applies downsampling for performance.
        """
        try:
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
                history_data = self.widget_state.get_speed_history(
                    start_time=start_time, end_time=end_time, 
                    interface_name=interface_to_query,
                    return_raw=True
                )
                
                # Fetch totals from DB as well (DURING the worker thread to avoid UI freeze)
                total_up, total_down = self.widget_state.get_total_bandwidth_for_period(
                    start_time=start_time,
                    end_time=end_time,
                    interface_name=interface_to_query
                )

                # PERFORMANCE OPTIMIZATION: Downsample large datasets.
                if len(history_data) > 2000:
                    history_data = downsample_data(history_data, 2000)

            if not history_data or len(history_data) < 2:
                self.data_ready.emit([], 0.0, 0.0)
                return

            # Pass the processed data and pre-calculated totals back to the UI.
            self.data_ready.emit(history_data, total_up, total_down)
        except Exception as e:
            logging.getLogger(__name__).error(f"Error in data worker: {e}", exc_info=True)
            self.error.emit(str(e))


from PyQt6.QtCore import QObject, pyqtSignal
import logging
from typing import List, Tuple
from datetime import datetime

class GraphDataWorker(QObject):
    """
    Processes graph data in a background thread to keep the UI responsive.
    """
    data_ready = pyqtSignal(object)
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
        """The main data processing method."""
        try:
            if not self.widget_state:
                self.error.emit("Data source (WidgetState) not available.")
                return

            if is_session_view:
                # Get data from the in-memory deque for the current session
                history_data = self.widget_state.get_in_memory_speed_history()
                
                # The in-memory data is a list of SpeedDataSnapshot objects,
                # so we need to process it into the tuple format the graph expects.
                processed_history = []
                for snapshot in history_data:
                    if interface_to_query is None:
                        up = sum(s[0] for s in snapshot.speeds.values())
                        down = sum(s[1] for s in snapshot.speeds.values())
                    else:
                        up, down = snapshot.speeds.get(interface_to_query, (0.0, 0.0))
                    # Ensure timestamp is a float (epoch) for numpy compatibility
                    ts = snapshot.timestamp.timestamp() if hasattr(snapshot.timestamp, 'timestamp') else snapshot.timestamp
                    processed_history.append((float(ts), up, down))
                history_data = processed_history
            else:
                # For all other timelines, get data from the database
                # start_time and end_time are now passed in directly from the UI thread
                history_data = self.widget_state.get_speed_history(
                    start_time=start_time, end_time=end_time, interface_name=interface_to_query,
                    return_raw=True
                )

            if len(history_data) < 2:
                self.data_ready.emit([]) # Emit empty list for "No data" message
                return

            # Pass the raw list of tuples: (timestamp, upload_speed, download_speed)
            self.data_ready.emit(history_data)
        except Exception as e:
            logging.getLogger(__name__).error(f"Error in data worker: {e}", exc_info=True)
            self.error.emit(str(e))

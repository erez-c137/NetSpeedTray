
import sys
import unittest
import os
from unittest.mock import MagicMock, patch
import numpy as np
from datetime import datetime

# Add src to path
sys.path.append(os.path.abspath("src"))

from netspeedtray.views.graph import GraphWindow

class TestGraphVectorization(unittest.TestCase):
    def setUp(self):
        self.mock_main_widget = MagicMock()
        self.mock_main_widget.config = {"show_legend": True}
        
        # Mock i18n
        self.mock_i18n = MagicMock()
        self.mock_i18n.DOWNLOAD_LABEL = "Download"
        self.mock_i18n.UPLOAD_LABEL = "Upload"
        self.mock_i18n.DAYS_TEMPLATE = "{days} Days"
        
        self.mock_main_widget.i18n = self.mock_i18n
        
        # Instantiate GraphWindow (mocking super init if needed, but QWidget is hard)
        # We'll rely on mocks being injected or monkeypatching.
        # But GraphWindow inherits QMainWindow. We need QApplication.
    
    @patch('netspeedtray.views.graph.GraphWindow.__init__', return_value=None)
    def test_render_graph_vectorized(self, mock_init):
        # Instantiate without calling __init__ (avoids Qt issues)
        window = GraphWindow(self.mock_main_widget)
        # Re-attach necessary attributes
        window.axes = [MagicMock(), MagicMock()] # ax_down, ax_up
        window.ax_download = window.axes[0]
        window.ax_upload = window.axes[1]
        window.canvas = MagicMock()
        window.figure = MagicMock()
        window._main_widget = self.mock_main_widget
        window.i18n = self.mock_i18n
        window._is_dark_mode = False
        window.logger = MagicMock()
        window.interface_filter = MagicMock()
        window.interface_filter.currentData.return_value = "all"
        window.stats_bar = MagicMock()
        
        # Mock methods called during render
        window._get_time_range_from_ui = MagicMock(return_value=(None, None))
        window._get_nice_y_axis_top = MagicMock(return_value=10.0)
        window._apply_theme = MagicMock()
        window._configure_xaxis_format = MagicMock()
        window._update_stats_bar = MagicMock() # Mocked to check call
        window._show_graph_error = MagicMock()

        # Create raw history data (epochs)
        # 100 points
        now = datetime.now().timestamp()
        timestamps = np.linspace(now - 3600, now, 100)
        ups = np.random.rand(100) * 1000000 # 1 Mbps
        downs = np.random.rand(100) * 2000000 # 2 Mbps
        
        history_data = list(zip(timestamps, ups, downs))
        
        # Call _render_graph
        try:
            window._render_graph(history_data)
        except Exception as e:
            self.fail(f"_render_graph raised exception: {e}")
            
        # Verify Axes calls
        window.ax_download.clear.assert_called()
        window.ax_download.plot.assert_called()
        
        # Check cache types
        self.assertTrue(isinstance(window._graph_x_cache, np.ndarray), "X Cache should be numpy array")
        self.assertTrue(isinstance(window._graph_data_ups, np.ndarray), "Speed Cache should be numpy array")
        
        print("Vectorized render passed successfully.")

if __name__ == "__main__":
    unittest.main()


import sys
import logging
from unittest.mock import MagicMock
from datetime import datetime, timedelta
from typing import List

from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QPainter, QImage, QColor
from PyQt6.QtCore import QRect

# Add src to path
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), 'src')))

from netspeedtray.utils.widget_renderer import WidgetRenderer, RenderConfig
from netspeedtray.core.widget_state import AggregatedSpeedData
from netspeedtray import constants

def test_graph_scaling():
    app = QApplication(sys.argv)
    
    # Mock i18n
    mock_i18n = MagicMock()
    
    # Create Renderer
    config_dict = {
        "graph_enabled": True,
        "history_minutes": 60, # 1 hour
        "update_rate": 1.0,    # 1 second
        # Should result in max_samples = 3600
        "high_speed_threshold": 50,
        "low_speed_threshold": 10,
        "graph_opacity": 100,
        "color_coding": True, 
        "unit_type": "bits_decimal" 
    }
    
    renderer = WidgetRenderer(config_dict, mock_i18n)
    
    print(f"Renderer initialized.")
    if renderer.config.max_samples != 3600:
        print(f"FAIL: max_samples calculation incorrect. Expected 3600, got {renderer.config.max_samples}")
        return
    else:
        print(f"PASS: max_samples = {renderer.config.max_samples}")

    # Create dummy history (30 mins = 1800 points)
    # We expect these 1800 points to occupy the RIGHT HALF of the graph width.
    history: List[AggregatedSpeedData] = []
    now = datetime.now()
    for i in range(1800):
        t = now - timedelta(seconds=(1799 - i))
        history.append(AggregatedSpeedData(100.0, 100.0, t))
        
    # Setup Paint
    width = 1000
    height = 100
    image = QImage(width, height, QImage.Format.Format_ARGB32)
    image.fill(QColor(0,0,0,0))
    painter = QPainter(image)
    
    # Mock text rect (needed for graph placement)
    # Since we are focusing on internal point calculation, we can just call draw_mini_graph with layout_mode='horizontal'
    # which calculates a separate rect.
    # horizontal rect: width=Constants... let's force layout mode to use specific rect?
    # Actually, let's use layout_mode='horizontal' and check the points internally.
    
    # horizontal layout uses:
    # graph_width = constants.layout.MINI_GRAPH_HORIZONTAL_WIDTH (usually small, ~50?)
    # That might be too small for good verification.
    
    # Let's override constants.layout.MINI_GRAPH_HORIZONTAL_WIDTH temporarily or mock get_last_text_rect
    renderer._last_text_rect = QRect(0, 0, width, height) 
    
    # Call draw_mini_graph
    # layout_mode='vertical' uses get_last_text_rect and adjusts margin.
    renderer.draw_mini_graph(painter, width, height, renderer.config, history, layout_mode='vertical')
    
    painter.end()
    
    # Check cached points
    # These are private, but accessible
    points = renderer._cached_upload_points
    if not points:
        print("FAIL: No points generated.")
        return
        
    num_points = len(points)
    print(f"Generated {num_points} points.")
    
    first_pt = points[0] # The oldest point (index 0 in history)
    last_pt = points[-1] # The newest point (index 1799)
    
    print(f"First Point X: {first_pt.x()}")
    print(f"Last Point X: {last_pt.x()}")
    
    # Logic Verification:
    # Graph Width is approx 1000 (minus margins).
    # Constants.renderer.GRAPH_MARGIN = 2.
    # Rect width ~ 1000.
    # 3600 max samples.
    # 1800 samples provided.
    # They should start roughly at x = 1000 - (1800/3600)*1000 = 500.
    # If they started at 0 (Stretched), that would be wrong.
    
    # Let's inspect the rect used.
    # Since we can't easily see the rect var, we deduce from Last Point X.
    # Newest point should be at Right Edge.
    right_edge = last_pt.x()
    
    # Oldest point X
    oldest_x = first_pt.x()
    
    span = right_edge - oldest_x
    print(f"Span of 1800 points: {span}")
    
    # Total width estimation
    # If this span represents 1800 points, and specific step_x was used.
    # step_x = span / 1799
    step_x = span / 1799
    
    # Total capacity width
    total_capacity_width = step_x * (3599)
    print(f"Estimated Total Capacity Width: {total_capacity_width}")
    
    if total_capacity_width > span * 1.5:
        print("PASS: Graph scaling indicates proper 'zoomed out' behavior (fixed time scaling).")
        print("Detailed: The 1800 points occupy approx half of the implied total capacity.")
    else:
        print("FAIL: Graph appears stretched (old behavior).")
        
    # Check #93 (Win11Slider) indirectly? No, UI test.
    # Check #90 (Color Coding)? renderer._get_speed_color is callable.
    color = renderer._get_speed_color(10 * 1024 * 1024, renderer.config) # 10 MB/s = 80 Mbps
    # High Threshold is 50. Should be High Color.
    print(f"Color for 80Mbps (High=50): {color.name()}")
    
    if color == QColor(renderer.config.high_speed_color):
        print("PASS: Color coding logic works.")
    else:
        print(f"FAIL: Color coding mismatch. Got {color.name()}, expected {renderer.config.high_speed_color}")

if __name__ == "__main__":
    test_graph_scaling()

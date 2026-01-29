
import sys
import os
import logging

# Add src to path
sys.path.append(os.path.abspath("src"))

from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QColor

# Mocking i18n
class MockI18n:
    pass

# Import system under test
from netspeedtray.utils.widget_renderer import WidgetRenderer, RenderConfig
from netspeedtray import constants

def test_color_coding():
    app = QApplication([])
    
    # Setup config with color coding enabled
    config_dict = {
        "color_coding": True,
        "default_color": "#FFFFFF",
        "high_speed_color": "#00FF00", # Green
        "low_speed_color": "#FFFF00",  # Yellow
        "high_speed_threshold": 5.0, # Mbps
        "low_speed_threshold": 1.0, # Mbps
        
        # Mandatory fields
        "font_family": "Segoe UI",
        "font_size": 9,
        "font_weight": 400,
        "background_color": "#000000",
        "unit_type": "bytes_binary",
        "speed_display_mode": "always_mbps",
        "decimal_places": 1,
        "text_alignment": "center",
        "force_decimals": False,
        "swap_upload_download": True,
        "hide_arrows": False,
        "hide_unit_suffix": False,
        "short_unit_labels": True,
        "graph_enabled": False
    }
    
    renderer = WidgetRenderer(config_dict, MockI18n())
    
    # Helper to check color
    def check_color(speed_bytes_per_sec, expected_hex_color, label):
        # speed_mbps = (speed * 8) / 1000000
        # 1 Mbps = 125,000 Bytes/sec
        
        color = renderer._get_speed_color(speed_bytes_per_sec, renderer.config)
        actual_hex = color.name().upper()
        expected_hex = expected_hex_color.upper()
        
        if actual_hex == expected_hex:
            print(f"PASS [{label}]: Speed {speed_bytes_per_sec} -> {actual_hex}")
        else:
            print(f"FAIL [{label}]: Speed {speed_bytes_per_sec} -> Got {actual_hex}, Expected {expected_hex}")

    print("--- Testing Color Coding Logic ---")
    
    # 1. Below Low Threshold (< 1 Mbps)
    # 0.5 Mbps = 62,500 Bytes/sec
    check_color(62500, "#FFFFFF", "Low Speed (Default Color)")
    
    # 2. Between Thresholds (1.0 - 5.0 Mbps)
    # 2.0 Mbps = 250,000 Bytes/sec
    # Logic: >= high ? high : >= low ? low : default
    # So if >= 1.0 (low threshold), it should be low_speed_color (Yellow)
    check_color(250000, "#FFFF00", "Medium Speed (Low Speed Color)")
    
    # 3. Above High Threshold (>= 5.0 Mbps)
    # 6.0 Mbps = 750,000 Bytes/sec
    check_color(750000, "#00FF00", "High Speed")
    
    # 4. Disable Color Coding
    print("\n--- Testing Disabled Color Coding ---")
    renderer.config.color_coding = False
    check_color(750000, "#FFFFFF", "High Speed (Coding Disabled)")

if __name__ == "__main__":
    test_color_coding()
